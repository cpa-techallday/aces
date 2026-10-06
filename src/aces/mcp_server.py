"""ACES MCP server: lets Mission Control agents drive ACES the way they drive simlab.

    aces-mcp                      # stdio, policy from ACES_MCP_POLICY (default: read)

Policy tiers (``ACES_MCP_POLICY``), mirroring simlab-mcp:
  read     processes, check, report, ledger_verify
  submit   + design, form, rod, export_simlab          (what agents get)
  full     + calibrate (writes the calibration registry)

Every file path an agent passes must resolve inside ``ACES_MCP_ROOT`` (default: the
working directory), so an agent cannot read or write elsewhere on the machine.  That
includes paths written inside a spec (a form ``source``, a rod ``study_dir``, an hx
``calibration_file``), which are resolved against the spec's folder and checked before
anything runs.  aces_report reads files through the same check, so a planted symbolic
link that points outside the root is refused; the CLI refuses to export from, or write
run files through, symbolic links; and export checks against the server's own ledger,
never one named inside a run folder.  The rod route runs the Python stage scripts of
the study it is given, so keep only trusted studies under the root.
Every call is appended to ``ACES_MCP_AUDIT`` (default ``out/mcp_audit.jsonl``) with
the tool, arguments, exit code and time.  There is deliberately no tool that edits
the evidence ledger, deletes runs or touches the rig.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import io
import json
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from . import cli, manufacturing

TIERS = {"read": 0, "submit": 1, "full": 2}
MAX_TRIALS = 100_000
MAX_CELLS = 6
MAX_REPORT_BYTES = 4_000_000

# Paths that a spec may carry, by domain: (requirements key, value that is not a path).
SPEC_PATHS = {
    "hx.two_fluid": [("calibration_file", None)],
    "form.lattice": [("source", "fusion")],
    "rod.lattice": [("study_dir", None)],
}

# cli.main prints to stdout; redirect_stdout swaps a process-wide stream, so one call at a time.
_CLI_LOCK = threading.Lock()


def _server_cls():
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP


class Gate:
    def __init__(self, root: str | Path | None = None, audit: str | Path | None = None, policy: str | None = None):
        self.root = Path(root or os.environ.get("ACES_MCP_ROOT") or Path.cwd()).resolve()
        self.audit = Path(audit or os.environ.get("ACES_MCP_AUDIT") or self.root / "out" / "mcp_audit.jsonl")
        self.policy = policy or os.environ.get("ACES_MCP_POLICY", "read")
        if self.policy not in TIERS:
            raise ValueError(f"ACES_MCP_POLICY must be one of {sorted(TIERS)}, got {self.policy!r}")

    def path(self, p: str) -> str:
        q = (self.root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if not q.is_relative_to(self.root):
            raise PermissionError(f"path {p!r} is outside the ACES MCP root {self.root}")
        return str(q)

    def check_spec(self, spec_path: str) -> str:
        """Resolve a spec path and confine every path written inside the spec to the root."""
        sp = Path(self.path(spec_path))
        try:
            data = yaml.safe_load(sp.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(f"cannot read spec {spec_path!r}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"spec {spec_path!r} is not a mapping")
        req, domain = data.get("requirements"), data.get("domain")
        for key, not_a_path in SPEC_PATHS.get(domain if isinstance(domain, str) else "", []):
            v = req.get(key) if isinstance(req, dict) else None
            if v is None or v == not_a_path:
                continue
            if not isinstance(v, str):
                raise ValueError(f"requirements.{key} must be a path")
            q = Path(v) if Path(v).is_absolute() else sp.parent / v
            self.path(str(q))  # raises PermissionError outside the root
        return str(sp)

    def read_text(self, p: Path, limit: int = MAX_REPORT_BYTES) -> str:
        q = Path(self.path(str(p)))  # resolves symbolic links, so a planted link cannot escape
        if q.stat().st_size > limit:
            raise ValueError(f"{p.name} is larger than {limit} bytes")
        return q.read_text()

    def log(self, tool: str, args: dict[str, Any], rc: int | None, error: str | None = None) -> None:
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        rec = {"time_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "tool": tool,
               "args": args, "exit": rc, "policy": self.policy}
        if error:
            rec["error"] = error
        with self.audit.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")


def _run_cli(gate: Gate, tool: str, argv: list[str], log_args: dict[str, Any]) -> dict[str, Any]:
    buf = io.StringIO()
    try:
        with _CLI_LOCK, contextlib.redirect_stdout(buf):
            rc = cli.main(argv)
    except SystemExit as e:  # argparse errors
        rc = int(e.code or 2)
    except Exception as e:  # report, never crash the server
        gate.log(tool, log_args, None, repr(e))
        return {"exit": None, "error": repr(e), "output": buf.getvalue()}
    gate.log(tool, log_args, rc)
    return {"exit": rc, "output": buf.getvalue()}


def build(gate: Gate | None = None):
    gate = gate or Gate()
    server = _server_cls()(
        name="aces",
        instructions=("ACES computational engineering (Carbon Project Australia). Validate a spec with "
                      "aces_check before running it. Results are fast-tier rankings until simlab verifies "
                      "them; never present them as verified performance."),
    )
    allowed = TIERS[gate.policy]

    def guarded(tool: str, need: str, argv_fn, **args):
        try:
            if TIERS[need] > allowed:
                raise PermissionError(f"{tool} needs policy '{need}', server runs '{gate.policy}'")
            argv = argv_fn()
        except (PermissionError, ValueError, TypeError) as e:
            gate.log(tool, args, None, str(e))
            return {"exit": None, "error": str(e)}
        return _run_cli(gate, tool, argv, args)

    @server.tool()
    def aces_processes() -> dict:
        """List manufacturing routes, their status, and what ACES checks for each."""
        gate.log("aces_processes", {}, 0)
        return {k: {"name": p.name, "status": p.status, "domains": list(p.domains), "checks": list(p.checks),
                    "notes": p.notes} for k, p in manufacturing.PROCESSES.items()}

    @server.tool()
    def aces_check(spec_path: str) -> dict:
        """Validate a spec file and list anything missing or invalid."""
        return guarded("aces_check", "read", lambda: ["check", gate.check_spec(spec_path)], spec_path=spec_path)

    @server.tool()
    def aces_report(run_dir: str) -> dict:
        """Return report.md and run.json from a finished run directory."""
        try:
            d = Path(gate.path(run_dir))
            out = {"report": gate.read_text(d / "report.md"), "run": json.loads(gate.read_text(d / "run.json"))}
            gate.log("aces_report", {"run_dir": run_dir}, 0)
            return out
        except (OSError, ValueError) as e:  # PermissionError and FileNotFoundError are OSErrors
            gate.log("aces_report", {"run_dir": run_dir}, None, str(e))
            return {"error": str(e)}

    @server.tool()
    def aces_ledger_verify(ledger_path: str = "out/evidence/ledger.jsonl") -> dict:
        """Verify the evidence ledger's hash chain."""
        return guarded("aces_ledger_verify", "read", lambda: ["ledger", "verify", gate.path(ledger_path)],
                       ledger_path=ledger_path)

    if allowed >= TIERS["submit"]:
        @server.tool()
        def aces_design(spec_path: str, out_dir: str, trials: int | None = None) -> dict:
            """Run a design search (hx.two_fluid) and write report, Pareto set and evidence."""
            def argv():
                a = ["design", gate.check_spec(spec_path), "--out", gate.path(out_dir),
                     "--ledger", gate.path("out/evidence/ledger.jsonl")]
                if trials is not None:
                    if not 1 <= int(trials) <= MAX_TRIALS:
                        raise ValueError(f"trials must be between 1 and {MAX_TRIALS}")
                    a += ["--trials", str(int(trials))]
                return a
            return guarded("aces_design", "submit", argv, spec_path=spec_path, out_dir=out_dir, trials=trials)

        @server.tool()
        def aces_form(spec_path: str, out_dir: str) -> dict:
            """Run the formgen lattice route (robot cell or printed formwork) from a form.lattice spec."""
            return guarded("aces_form", "submit", lambda: ["form", gate.check_spec(spec_path), "--out", gate.path(out_dir),
                                                           "--ledger", gate.path("out/evidence/ledger.jsonl")],
                           spec_path=spec_path, out_dir=out_dir)

        @server.tool()
        def aces_rod(spec_path: str, out_dir: str) -> dict:
            """Run the rod-lattice route (placed rods, welded nodes) from a rod.lattice spec."""
            return guarded("aces_rod", "submit", lambda: ["rod", gate.check_spec(spec_path), "--out", gate.path(out_dir),
                                                          "--ledger", gate.path("out/evidence/ledger.jsonl")],
                           spec_path=spec_path, out_dir=out_dir)

        @server.tool()
        def aces_export_simlab(run_dir: str, solver: str, vram_gb: float, rank: int = 0,
                               region: str = "fluid_a", cells: int = 3) -> dict:
            """Write geometry and a simlab experiment for one shortlisted design (validate it in simlab next)."""
            def argv():
                if not 1 <= int(cells) <= MAX_CELLS:
                    raise ValueError(f"cells must be between 1 and {MAX_CELLS}")
                if int(rank) < 0:
                    raise ValueError("rank must be 0 or more")
                if not 0 < float(vram_gb) <= 60:
                    raise ValueError("vram_gb must be above 0 and at most 60 (simlab refuses larger cases)")
                return ["export-simlab", gate.path(run_dir), "--solver", solver, "--vram-gb", str(float(vram_gb)),
                        "--rank", str(int(rank)), "--region", region, "--cells", str(int(cells)),
                        "--ledger", gate.path("out/evidence/ledger.jsonl")]  # never a ledger named in run.json
            return guarded("aces_export_simlab", "submit", argv,
                           run_dir=run_dir, solver=solver, vram_gb=vram_gb, rank=rank, region=region, cells=cells)

    if allowed >= TIERS["full"]:
        @server.tool()
        def aces_calibrate(measurements_path: str, registry_path: str, project: str, owner: str,
                           rd_activity: str) -> dict:
            """Fit hx calibration factors from measurements and update the registry (full policy only)."""
            return guarded("aces_calibrate", "full",
                           lambda: ["calibrate", gate.path(measurements_path), "--registry", gate.path(registry_path),
                                    "--project", project, "--owner", owner, "--rd-activity", rd_activity,
                                    "--ledger", gate.path("out/evidence/ledger.jsonl")],
                           measurements_path=measurements_path, registry_path=registry_path, project=project)

    return server


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
