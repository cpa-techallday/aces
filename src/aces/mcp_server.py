"""ACES MCP server: lets Mission Control agents drive ACES the way they drive simlab.

    aces-mcp                      # stdio, policy from ACES_MCP_POLICY (default: read)

Policy tiers (``ACES_MCP_POLICY``), mirroring simlab-mcp:
  read     processes, check, report, ledger_verify
  submit   + design, form, export_simlab               (what agents get)
  full     + rod (runs a study's scripts), calibrate (writes the calibration registry)

Paths.  Every file path an agent passes must resolve inside ``ACES_MCP_ROOT`` (default:
the working directory), and never inside ``src/``, any ``.git`` folder or the folder the
running ACES code is loaded from, so an agent cannot read or write elsewhere on the
machine, plant code or git config (audit H1).  That includes every path written inside a
spec.  The gate finds those fields in the requirements models themselves: fields named
``*_file``, ``*_dir`` or ``*_path``, typed as a path, or marked ``aces_path`` in their
``json_schema_extra`` (a form ``source``), at any depth, so a new domain is covered
without a table to keep up to date (audit R7).  They are resolved against the spec's
folder and checked before anything runs.  aces_report reads files through the same
check, so a planted symbolic link that points outside the root is refused; the CLI never
writes through links and needs a new folder for every run; and export checks against
the server's own ledger, never one named inside a run folder.  The rod route runs only
studies committed under the repository's ``studies/`` with no local changes.

Limits (audit M5).  Each CLI call runs in its own Python process with the minimal
environment, a wall-clock limit (``ACES_MCP_READ_TIMEOUT_S``, default 120 s, for the read
tools; ``ACES_MCP_RUN_TIMEOUT_S``, default 4 h, for runs) and, on POSIX, an address-space
limit (``ACES_MCP_MEMORY_MB``, default 8192; 0 for none; a design run peaks near 0.5 GB)
that its children, such as rod stages, inherit.  Windows has no ``resource`` module, so there a call gets the time limit
only.  At most ``ACES_MCP_MAX_RUNS`` (default 1) runs go at once; another is refused, not
queued, and the read tools never wait for a run.  Specs, reports and ledgers are read
with size limits (``ACES_MCP_MAX_LEDGER_MB``, default 256).

Every call is appended to ``ACES_MCP_AUDIT`` (default ``out/mcp_audit.jsonl``) with
the tool, arguments, exit code and time.  There is deliberately no tool that edits
the evidence ledger, deletes runs or touches the rig.
"""
from __future__ import annotations

import collections.abc
import datetime as _dt
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path, PurePath
from typing import Any, get_args, get_origin

import yaml
from pydantic import BaseModel

from . import evidence, manufacturing, proc, safeio, simlab

TIERS = {"read": 0, "submit": 1, "full": 2}
MAX_TRIALS = 100_000
MAX_CELLS = 6
MAX_REPORT_BYTES = 4_000_000
MAX_SPEC_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 200_000
PATH_SUFFIXES = ("_file", "_dir", "_path")
CODE_DIR = Path(__file__).resolve().parents[1]  # the folder the aces package is loaded from (src/ in a checkout)
_EXIT_ERROR, _EXIT_MEMORY = 70, 71

# The child process: limits first, then the CLI.  Not run through the shell; argv[1] is the memory limit in
# bytes, argv[2] the folder holding the aces package, the rest the CLI arguments.
_CHILD = """\
import sys
lim = int(sys.argv[1])
if lim > 0:
    try:
        import resource
    except ImportError:  # Windows: the call gets its time limit only
        resource = None
    if resource is not None:
        hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        if hard != resource.RLIM_INFINITY:
            lim = min(lim, hard)
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
sys.path.insert(0, sys.argv[2])
try:
    ENTRY
except MemoryError:
    sys.stderr.write("ACES-MCP: out of memory\\n")
    sys.exit(71)
except SystemExit:
    raise
except BaseException as e:
    sys.stderr.write("ACES-MCP-ERROR: %r\\n" % (e,))
    sys.exit(70)
sys.exit(rc)
"""
_ENTRY = "from aces.cli import main; rc = main(sys.argv[3:])"


def _child_cmd(argv: list[str], memory_bytes: int) -> list[str]:
    """``python -P``: the working directory is never on the child's import path."""
    return [sys.executable, "-P", "-c", _CHILD.replace("ENTRY", _ENTRY), str(int(memory_bytes)), str(CODE_DIR),
            *argv]


# ----------------------------------------------------------------------------- paths inside specs

def requirement_models() -> dict[str, type[BaseModel]]:
    """Every spec domain and its requirements model (design models, then the form and rod routes)."""
    from .domains import MODELS
    from .domains.form import route as form_route
    from .domains.rod import route as rod_route

    out = {d: m.requirements_model for d, m in MODELS.items()}
    out.setdefault(form_route.DOMAIN, form_route.FormRequirements)
    out.setdefault(rod_route.DOMAIN, rod_route.RodRequirements)
    return out


_SEQUENCES = (list, tuple, set, frozenset, collections.abc.Sequence, collections.abc.Set)
_MAPPINGS = (dict, collections.abc.Mapping)


def _inner(ann) -> list[tuple[str, Any]]:
    """(step, annotation) one level down: '*' into a container's items, '' through Optional/Union/Annotated."""
    origin = get_origin(ann)
    if origin is None:
        return []
    args = [a for a in get_args(ann) if a is not Ellipsis]
    if origin in _MAPPINGS:
        return [("*", args[-1])] if args else []
    if origin in _SEQUENCES:
        return [("*", a) for a in args]
    return [("", a) for a in args]


def _holds_path_type(ann) -> bool:
    if isinstance(ann, type) and issubclass(ann, PurePath):
        return True
    return any(_holds_path_type(a) for _, a in _inner(ann))


@functools.cache
def path_fields(model: type[BaseModel]) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    """Fields of a requirements model, nested models included, that hold a file-system path.

    Each entry is (keys from the requirements block, '*' for every item of a list or mapping; values of that
    field that are not paths, such as a form ``source: fusion``).
    """
    found: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def visit(m: type[BaseModel], prefix: tuple[str, ...], seen: frozenset) -> None:
        if m in seen:
            return
        seen = seen | {m}
        for name, f in m.model_fields.items():
            key = (*prefix, name)
            extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
            mark = extra.get("aces_path")
            if mark is not None or name.endswith(PATH_SUFFIXES) or _holds_path_type(f.annotation):
                found.append((key, tuple(mark.get("not_paths", ())) if isinstance(mark, dict) else ()))
                continue
            todo = [((), f.annotation)]
            while todo:
                steps, ann = todo.pop()
                if isinstance(ann, type) and issubclass(ann, BaseModel):
                    visit(ann, (*key, *steps), seen)
                    continue
                todo += [((*steps, s) if s else steps, a) for s, a in _inner(ann)]

    visit(model, (), frozenset())
    return tuple(found)


def _values_at(data: Any, keys: tuple[str, ...]):
    if not keys:
        if isinstance(data, (list, tuple)):
            yield from data
        else:
            yield data
        return
    k, rest = keys[0], keys[1:]
    if k == "*":
        items = data.values() if isinstance(data, dict) else data if isinstance(data, (list, tuple)) else ()
        for item in items:
            yield from _values_at(item, rest)
    elif isinstance(data, dict) and k in data:
        yield from _values_at(data[k], rest)


def _set_at(data: Any, keys: tuple[str, ...], fn) -> None:
    """Replace every value at ``keys`` (as :func:`_values_at` finds them) with ``fn(value)``."""
    if not keys:
        return
    k, rest = keys[0], keys[1:]
    if k == "*":
        if isinstance(data, dict):
            items = list(data.items())
        elif isinstance(data, list):
            items = list(enumerate(data))
        else:
            items = []
        for i, item in items:
            if rest:
                _set_at(item, rest, fn)
            elif isinstance(item, list):
                data[i] = [fn(x) for x in item]
            else:
                data[i] = fn(item)
    elif isinstance(data, dict) and k in data:
        if rest:
            _set_at(data[k], rest, fn)
        elif isinstance(data[k], list):
            data[k] = [fn(x) for x in data[k]]
        else:
            data[k] = fn(data[k])


def confine_spec_paths(data: dict[str, Any], spec_dir: Path, check) -> dict[str, Any]:
    """Every path inside a spec's requirements, checked with ``check(path) -> resolved path`` and rewritten to
    that resolved, absolute path.  Returns the rewritten copy (the input is not changed)."""
    data = json.loads(json.dumps(data, default=str))
    req, domain = data.get("requirements"), data.get("domain")
    model = requirement_models().get(domain) if isinstance(domain, str) else None
    if model is None or not isinstance(req, dict):
        return data
    for keys, not_paths in path_fields(model):
        def fix(v, keys=keys, not_paths=not_paths):
            if v is None or v in not_paths:
                return v
            if not isinstance(v, str):
                raise ValueError(f"requirements.{'.'.join(keys)} must be a path")
            return check(str(Path(v) if Path(v).is_absolute() else spec_dir / v))
        _set_at(req, keys, fix)
    return data


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _tail(text: Any, limit: int = MAX_OUTPUT_CHARS) -> str:
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else (text or "")
    return s if len(s) <= limit else "[... output cut ...]\n" + s[-limit:]


def _server_cls():
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP


class Gate:
    def __init__(self, root: str | Path | None = None, audit: str | Path | None = None, policy: str | None = None,
                 *, read_timeout_s: float | None = None, run_timeout_s: float | None = None,
                 memory_mb: int | None = None, max_runs: int | None = None, max_ledger_mb: float | None = None):
        self.root = Path(root or os.environ.get("ACES_MCP_ROOT") or Path.cwd()).resolve()
        self.audit = Path(audit or os.environ.get("ACES_MCP_AUDIT") or self.root / "out" / "mcp_audit.jsonl")
        self.policy = policy or os.environ.get("ACES_MCP_POLICY", "read")
        if self.policy not in TIERS:
            raise ValueError(f"ACES_MCP_POLICY must be one of {sorted(TIERS)}, got {self.policy!r}")
        self.read_timeout_s = float(read_timeout_s if read_timeout_s is not None
                                    else _env_number("ACES_MCP_READ_TIMEOUT_S", 120))
        self.run_timeout_s = float(run_timeout_s if run_timeout_s is not None
                                   else _env_number("ACES_MCP_RUN_TIMEOUT_S", 4 * 3600))
        self.memory_mb = int(memory_mb if memory_mb is not None else _env_number("ACES_MCP_MEMORY_MB", 8192))
        self.max_runs = int(max_runs if max_runs is not None else _env_number("ACES_MCP_MAX_RUNS", 1))
        self.max_ledger_bytes = int(1024 * 1024 * (max_ledger_mb if max_ledger_mb is not None
                                                    else _env_number("ACES_MCP_MAX_LEDGER_MB", 256)))
        if self.read_timeout_s <= 0 or self.run_timeout_s <= 0 or self.memory_mb < 0 or self.max_runs < 1:
            raise ValueError("ACES MCP limits: timeouts must be above 0, memory 0 (none) or more, max runs 1 or more")
        self.runs = threading.BoundedSemaphore(self.max_runs)
        # Checked specs are frozen here (private to the server, outside the root) and the CLI runs on the frozen
        # copy, so a spec rewritten after its check can't reach the child (verification finding).
        self.frozen_dir = Path(tempfile.mkdtemp(prefix="aces-mcp-specs-"))
        self._audit_lock = threading.Lock()

    @property
    def memory_bytes(self) -> int:
        return self.memory_mb * 1024 * 1024

    def path(self, p: str) -> str:
        if not isinstance(p, str) or not p or "\0" in p:
            raise ValueError("a path must be a non-empty string")
        q = (self.root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if not q.is_relative_to(self.root):
            raise PermissionError(f"path {p!r} is outside the ACES MCP root {self.root}")
        parts = [s.lower() for s in q.relative_to(self.root).parts]
        if ".git" in parts or parts[:1] == ["src"] or q.is_relative_to(CODE_DIR):
            raise PermissionError(f"path {p!r} is inside src/, a .git folder or the ACES code; the ACES MCP server "
                                  f"never reads or writes there")
        return str(q)

    def check_spec(self, spec_path: str) -> str:
        """Resolve a spec path, confine every path written inside it to the root, and return a frozen copy.

        The spec is read once. Its path fields are checked and rewritten to absolute, resolved paths, and the copy
        is written to the server's private folder; the CLI is given the copy, never the original.
        """
        sp = Path(self.path(spec_path))
        try:
            data = yaml.safe_load(safeio.read_text_limited(sp, MAX_SPEC_BYTES)) or {}
        except (OSError, ValueError, yaml.YAMLError) as e:
            raise ValueError(f"cannot read spec {spec_path!r}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"spec {spec_path!r} is not a mapping")
        frozen = confine_spec_paths(data, sp.parent, self.path)  # PermissionError outside the root, src/ or .git
        with safeio.OutDir(self.frozen_dir, mkdir=False) as d:
            out = d.write_text(f"{uuid.uuid4().hex}-{sp.name}", yaml.safe_dump(frozen, sort_keys=False))
        return str(out)

    def read_text(self, p: Path, limit: int = MAX_REPORT_BYTES) -> str:
        q = Path(self.path(str(p)))  # resolves symbolic links, so a planted link cannot escape
        return safeio.read_text_limited(q, limit)

    def log(self, tool: str, args: dict[str, Any], rc: int | None, error: str | None = None) -> None:
        rec = {"time_utc": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"), "tool": tool,
               "args": args, "exit": rc, "policy": self.policy}
        if error:
            rec["error"] = error
        line = json.dumps(rec, default=str) + "\n"
        with self._audit_lock, safeio.OutDir(self.audit.parent) as d, d.open_append(self.audit.name) as fh:
            fh.write(line)


def _run_cli(gate: Gate, tool: str, argv: list[str], log_args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """Run ``aces ARGV`` in its own process with the gate's time and memory limits."""
    env = proc.minimal_env({"ACES_PATH_ROOT": str(gate.root)}, keep=(evidence.HMAC_ENV,))
    try:
        cp = proc.run(_child_cmd(argv, gate.memory_bytes), cwd=gate.root, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        msg = f"{tool} was stopped at its {timeout:g} s limit"
        gate.log(tool, log_args, None, msg)
        return {"exit": None, "error": msg, "output": _tail(e.stdout)}
    except OSError as e:
        gate.log(tool, log_args, None, repr(e))
        return {"exit": None, "error": repr(e), "output": ""}
    out = _tail(cp.stdout)
    if cp.returncode in (_EXIT_ERROR, _EXIT_MEMORY) or cp.returncode < 0:
        if cp.returncode == _EXIT_MEMORY:
            err = f"{tool} ran out of memory (limit {gate.memory_mb} MB, ACES_MCP_MEMORY_MB)"
        elif cp.returncode < 0:
            err = f"{tool} was killed by signal {-cp.returncode}"
        else:
            lines = [ln for ln in (cp.stderr or "").splitlines() if ln.startswith("ACES-MCP-ERROR: ")]
            err = lines[-1][len("ACES-MCP-ERROR: "):] if lines else _tail(cp.stderr, 2000)
        gate.log(tool, log_args, None, err)
        return {"exit": None, "error": err, "output": out}
    gate.log(tool, log_args, cp.returncode)
    res: dict[str, Any] = {"exit": cp.returncode, "output": out}
    if cp.returncode != 0 and (cp.stderr or "").strip():
        res["stderr"] = _tail(cp.stderr, 4000)
    return res


def build(gate: Gate | None = None):
    import anyio.to_thread  # a dependency of mcp

    gate = gate or Gate()
    server = _server_cls()(
        name="aces",
        instructions=("ACES computational engineering (Carbon Project Australia). Validate a spec with "
                      "aces_check before running it. Results are fast-tier rankings until simlab verifies "
                      "them; never present them as verified performance."),
    )
    allowed = TIERS[gate.policy]
    ledger = "out/evidence/ledger.jsonl"  # the server's own ledger; never one named in a spec or run folder

    def guarded(tool: str, need: str, argv_fn, *, run: bool = False, **args):
        """Check the tier and arguments, then run the CLI.  ``run``: a design-type call (run limits)."""
        try:
            if TIERS[need] > allowed:
                raise PermissionError(f"{tool} needs policy '{need}', server runs '{gate.policy}'")
            argv = argv_fn()
        except (PermissionError, ValueError, TypeError) as e:
            gate.log(tool, args, None, str(e))
            return {"exit": None, "error": str(e)}
        if not run:
            return _run_cli(gate, tool, argv, args, timeout=gate.read_timeout_s)
        if not gate.runs.acquire(blocking=False):
            msg = (f"{gate.max_runs} run(s) already in progress (ACES_MCP_MAX_RUNS); try again when one "
                   f"finishes")
            gate.log(tool, args, None, msg)
            return {"exit": None, "error": msg}
        try:
            return _run_cli(gate, tool, argv, args, timeout=gate.run_timeout_s)
        finally:
            gate.runs.release()

    async def off_loop(fn, *a, **kw):
        """Blocking work goes to a worker thread, so one call never holds up another (mcp 1.x and 2.x)."""
        return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))

    @server.tool()
    def aces_processes() -> dict:
        """List manufacturing routes, their status, and what ACES checks for each."""
        gate.log("aces_processes", {}, 0)
        return {k: {"name": p.name, "status": p.status, "domains": list(p.domains), "checks": list(p.checks),
                    "notes": p.notes} for k, p in manufacturing.PROCESSES.items()}

    @server.tool()
    async def aces_check(spec_path: str) -> dict:
        """Validate a spec file and list anything missing or invalid."""
        return await off_loop(guarded, "aces_check", "read", lambda: ["check", gate.check_spec(spec_path)],
                              spec_path=spec_path)

    def report(run_dir: str) -> dict:
        try:
            d = Path(gate.path(run_dir))
            out = {"report": gate.read_text(d / "report.md"), "run": json.loads(gate.read_text(d / "run.json"))}
            gate.log("aces_report", {"run_dir": run_dir}, 0)
            return out
        except (OSError, ValueError) as e:  # PermissionError and FileNotFoundError are OSErrors
            gate.log("aces_report", {"run_dir": run_dir}, None, str(e))
            return {"error": str(e)}

    @server.tool()
    async def aces_report(run_dir: str) -> dict:
        """Return report.md and run.json from a finished run directory."""
        return await off_loop(report, run_dir)

    @server.tool()
    async def aces_ledger_verify(ledger_path: str = ledger, expect_head: str | None = None,
                                 min_records: int | None = None) -> dict:
        """Verify the evidence ledger's hash chain; optionally against a head hash and record count kept elsewhere."""
        def argv():
            p = Path(gate.path(ledger_path))
            if p.is_file() and p.stat().st_size > gate.max_ledger_bytes:
                raise ValueError(f"{ledger_path} is {p.stat().st_size:,} bytes; this server verifies ledgers up to "
                                 f"{gate.max_ledger_bytes:,} bytes (ACES_MCP_MAX_LEDGER_MB). Run `aces ledger "
                                 f"verify` on the host")
            a = ["ledger", "verify", str(p)]
            if expect_head is not None:
                if not re.fullmatch(r"[0-9a-f]{64}", str(expect_head)):
                    raise ValueError("expect_head must be the 64-character head hash from `aces ledger head`")
                a.append(f"--expect-head={expect_head}")
            if min_records is not None:
                if int(min_records) < 1:
                    raise ValueError("min_records must be 1 or more")
                a.append(f"--min-records={int(min_records)}")
            return a
        return await off_loop(guarded, "aces_ledger_verify", "read", argv, ledger_path=ledger_path,
                              expect_head=expect_head, min_records=min_records)

    if allowed >= TIERS["submit"]:
        @server.tool()
        async def aces_design(spec_path: str, out_dir: str, trials: int | None = None) -> dict:
            """Run a design search (hx.two_fluid) and write report, Pareto set and evidence."""
            def argv():
                a = ["design", gate.check_spec(spec_path), f"--out={gate.path(out_dir)}",
                     f"--ledger={gate.path(ledger)}"]
                if trials is not None:
                    if not 1 <= int(trials) <= MAX_TRIALS:
                        raise ValueError(f"trials must be between 1 and {MAX_TRIALS}")
                    a.append(f"--trials={int(trials)}")
                return a
            return await off_loop(guarded, "aces_design", "submit", argv, run=True, spec_path=spec_path,
                                  out_dir=out_dir, trials=trials)

        @server.tool()
        async def aces_form(spec_path: str, out_dir: str) -> dict:
            """Run the formgen lattice route (robot cell or printed formwork) from a form.lattice spec."""
            return await off_loop(guarded, "aces_form", "submit",
                                  lambda: ["form", gate.check_spec(spec_path), f"--out={gate.path(out_dir)}",
                                           f"--ledger={gate.path(ledger)}"],
                                  run=True, spec_path=spec_path, out_dir=out_dir)

        @server.tool()
        async def aces_export_simlab(run_dir: str, solver: str, vram_gb: float, rank: int = 0,
                                     region: str = "fluid_a", cells: int = 3) -> dict:
            """Write geometry and a simlab experiment for one shortlisted design (validate it in simlab next)."""
            def argv():
                if not 1 <= int(cells) <= MAX_CELLS:
                    raise ValueError(f"cells must be between 1 and {MAX_CELLS}")
                if int(rank) < 0:
                    raise ValueError("rank must be 0 or more")
                if not 0 < float(vram_gb) <= 60:
                    raise ValueError("vram_gb must be above 0 and at most 60 (simlab refuses larger cases)")
                simlab.check_name("solver", solver)
                return ["export-simlab", gate.path(run_dir), f"--solver={solver}", f"--vram-gb={float(vram_gb)}",
                        f"--rank={int(rank)}", f"--region={region}", f"--cells={int(cells)}",
                        f"--ledger={gate.path(ledger)}"]
            return await off_loop(guarded, "aces_export_simlab", "submit", argv, run=True, run_dir=run_dir,
                                  solver=solver, vram_gb=vram_gb, rank=rank, region=region, cells=cells)

    if allowed >= TIERS["full"]:
        @server.tool()
        async def aces_rod(spec_path: str, out_dir: str) -> dict:
            """Run the rod-lattice route from a rod.lattice spec (full policy only: it runs the study's scripts).

            Only studies committed under the repository's studies/ folder, with no local changes, run.
            """
            return await off_loop(guarded, "aces_rod", "full",
                                  lambda: ["rod", gate.check_spec(spec_path), f"--out={gate.path(out_dir)}",
                                           f"--ledger={gate.path(ledger)}"],
                                  run=True, spec_path=spec_path, out_dir=out_dir)

        @server.tool()
        async def aces_calibrate(measurements_path: str, registry_path: str, project: str, owner: str,
                                 rd_activity: str) -> dict:
            """Fit hx calibration factors from measurements and update the registry (full policy only)."""
            return await off_loop(guarded, "aces_calibrate", "full",
                                  lambda: ["calibrate", gate.path(measurements_path),
                                           f"--registry={gate.path(registry_path)}", f"--project={project}",
                                           f"--owner={owner}", f"--rd-activity={rd_activity}",
                                           f"--ledger={gate.path(ledger)}"],
                                  run=True, measurements_path=measurements_path, registry_path=registry_path,
                                  project=project)

    return server


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
