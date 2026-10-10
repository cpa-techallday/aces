"""Test helpers: an in-process fake of simlab-mcp 0.4.0 (section 22.2 of the rig build spec) and the network guard.

Everything here is a test fixture. The fake implements the documented request and response shapes of the rig's
simlab-mcp (JSON-RPC over streamable HTTP, the 17 tools of policy full and their tiers, the sim_put_file /
sim_get_file rules of section 5.1, the spec store's rules) with state under a temporary "repo" folder. Jobs move one
step per sim_status call (queued -> started/waiting_for_gpu -> started/running on GPU 2 -> executed); executing a job
emulates the simlab worker for an unstaged adapter around the real golden.rigside.run with ACES_GOLDEN_FORCE_DEVICE=cpu,
so every number a job produces comes from a real CPU solve on this machine. The image digest, the GPU and the host in
the evidence are labelled "(test fixture)". Without ccx, the real conforming.run_check reports ccx-not-found.

Scenarios, per golden case id (section 22.2 item 9, plus two the rig review added): ok, design-fail, input-mismatch,
never-starts, hang, infra-fail, dark-after-submit, corrupt-file, file-changes, delay:<s>, evidence-fail; rig-exception
(the solve or the conforming check raises inside the case, so the outcome is FAILED with design status "error") and
file-vanishes (after the job ends, a file its files.json lists is deleted, so sim_get_file refuses it).

The network guard is installed when this module is imported (the same guard as aces-cad-onshape's fake_onshape.py):
a connection to any host other than loopback raises NonLoopbackConnection and is recorded in BLOCKED, and every
test_g4_* tearDown asserts that BLOCKED is empty.
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
for _p in (TESTS.parent / "lib", TESTS.parent / "vendor"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.append(str(_p))

# --- the network guard --------------------------------------------------------------------------------------------

BLOCKED: list[str] = []
_REAL_CREATE_CONNECTION = getattr(socket.create_connection, "_aces_real", socket.create_connection)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class NonLoopbackConnection(RuntimeError):
    """A test tried to reach a host other than the loopback fakes (not an OSError, so urllib does not wrap it)."""


def _guarded_create_connection(address, *args, **kwargs):
    host = str(address[0]).lower()
    if host not in LOOPBACK:
        BLOCKED.append(f"{host}:{address[1]}")
        raise NonLoopbackConnection(f"test tried to connect to {host}:{address[1]}; tests may only reach the loopback "
                                    f"fakes")
    return _REAL_CREATE_CONNECTION(address, *args, **kwargs)


_guarded_create_connection._aces_real = _REAL_CREATE_CONNECTION
socket.create_connection = _guarded_create_connection

# --- fixture constants --------------------------------------------------------------------------------------------

FAKE_IMAGE = "sha256:fake-image (test fixture)"
FAKE_GPU = {"index": 0, "name": "fake GPU (test fixture)", "uuid": "GPU-fake-0001", "serial": "0"}
FAKE_CAPACITY = {"mode": "sim-lab-4", "pool": [0, 1, 2, 3], "available": [0, 1, 2, 3], "derated": [],
                 "source": "fake (test fixture)", "errors": []}
FAKE_JOB_GPU = 2
UNAUTHORIZED = b'{"error":"unauthorized: simlab-mcp requires Authorization: Bearer <token>"}'
# simlab-mcp 0.4.0 tool tiers (section 5.2 of the rig build spec: 8a0652a's tiers plus sim_get_file and sim_put_file)
TIERS = {
    "read": {"rig_capacity", "sim_list_solvers", "sim_validate", "sim_status", "sim_results", "sim_report",
             "sim_list_experiments", "sim_report_files", "sim_evidence", "sim_whoami", "sim_list_specs",
             "sim_get_file"},
    "submit": {"sim_submit", "sim_sweep", "sim_put_spec", "sim_put_file"},
    "full": {"sim_cancel"},
}
FILE_MAX_BYTES = 268435456
SPEC_MAX_BYTES = 256 * 1024
_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_SPEC_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.ya?ml\Z")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_EXEC_LOCK = threading.RLock()  # jobs run one at a time in this process: the case runner uses process-wide env vars


def allowed_tools(policy: str) -> set[str]:
    out = set(TIERS["read"])
    if policy in ("submit", "full"):
        out |= TIERS["submit"]
    if policy == "full":
        out |= TIERS["full"]
    return out


def vendored_tiers() -> dict | None:
    """The TIERS literal of the vendored simlab/mcp_server.py, read with ast (never imported: it needs the mcp SDK)."""
    import ast

    path = TESTS.parent / "vendor" / "simlab" / "mcp_server.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TIERS" for t in node.targets):
            return {k: set(v) for k, v in ast.literal_eval(node.value).items()}
    return None


def golden_row(kind: str, *, validation: str | None = "pass", compute_sha256: str | None = "desktop",
               image_id: str | None = FAKE_IMAGE) -> dict:
    """One registry row in the shape of simlab/solvers.d/golden.json (test fixture). compute_sha256 "desktop" is this
    desktop's golden.evidence.compute_digest()."""
    if compute_sha256 == "desktop":
        from aces_engineering.golden.evidence import compute_digest

        compute_sha256 = compute_digest()["golden_compute_sha256"]
    module = {"design": "golden_case", "mesh": "golden_check", "conforming": "ccx_check"}[kind]
    return {"module": f"simlab.solvers.{module}", "physics": ["structures"], "precision": ["float64"],
            "image": "aces-golden:0.1", "description": f"golden {kind} solver (test fixture)",
            "golden": {"kind": kind, "params_schema": "aces-golden-rig/1", "compute_sha256": compute_sha256,
                       "image_id": image_id, "aces_os_commit": None},
            "validation": {"status": validation, "date": None, "image_id": image_id, "device": None, "dtype": "float64",
                           "cases": [], "note": "test fixture: not a validation", "record": None}}


def golden_solvers(**kw) -> dict:
    return {"golden-case": golden_row("design", **kw), "golden-check": golden_row("mesh", **kw),
            "ccx-check": golden_row("conforming", **kw)}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _principal_dir(principal: str | None) -> str:
    return (_UNSAFE.sub("_", (principal or "").strip()).strip("._-") or "anonymous")[:64]


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


class FileError(ValueError):
    pass


# --- the fake -----------------------------------------------------------------------------------------------------

class FakeSimlab:
    """A threaded http.server on 127.0.0.1 speaking simlab-mcp (a context manager). `.url`, `.calls` (tool or method,
    args with content_b64 cut to 16 characters, has_auth), `.go_dark()`, `.stop()`."""

    def __init__(self, repo: Path, *, tokens: dict | None = None, policy: str = "full", solvers: dict | None = None,
                 capacity: dict | None = None, scenarios: dict | None = None, file_chunk_max: int = 2097152,
                 latency_s: float = 0.0, fail_after: int | None = None, fault: str = "503", fault_s: float = 0.0):
        self.repo = Path(repo)
        self.repo.mkdir(parents=True, exist_ok=True)
        self.tokens = dict(tokens or {})          # principal name -> token
        self.policy = policy
        self.solvers = solvers if solvers is not None else golden_solvers()
        self.capacity = dict(capacity or FAKE_CAPACITY)
        self.scenarios = dict(scenarios or {})    # golden case id -> scenario
        self.file_chunk_max = int(file_chunk_max)
        self.latency_s = float(latency_s)
        self.fail_after, self.fault, self.fault_s = fail_after, fault, float(fault_s)
        self.calls: list[tuple[str, dict, bool]] = []
        self.cancel_calls: list[str] = []
        self.exps: dict[str, dict] = {}
        self.dark = False
        self.lock = threading.RLock()
        self._n_tool_calls = 0
        self._fault_t0: float | None = None
        self._dark_after: bool = False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.fake = self
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="fake-simlab", daemon=True)
        self._thread.start()

    def __enter__(self) -> "FakeSimlab":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self.httpd.shutdown()
        with contextlib.suppress(Exception):
            self.httpd.server_close()

    def go_dark(self) -> None:
        """Every later request gets a connection reset."""
        self.dark = True

    # ---- helpers --------------------------------------------------------------------------------------------------
    def tool_calls(self, name: str | None = None) -> list[tuple[str, dict, bool]]:
        return [c for c in self.calls if name is None or c[0] == name]

    def store_root(self) -> Path:
        return self.repo / "cases" / "uploaded"

    def queue_root(self) -> Path:
        return self.repo / "out" / "queue"

    def principal_of(self, auth: str) -> str | None:
        if not self.tokens:
            return "anonymous"
        tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        for name, value in self.tokens.items():
            if tok and tok == value:
                return name
        return None

    def _fault_now(self) -> bool:
        if self.fail_after is None or self._n_tool_calls <= self.fail_after:
            return False
        now = time.monotonic()
        if self._fault_t0 is None:
            self._fault_t0 = now
            return True
        return now - self._fault_t0 < self.fault_s

    # ---- tools ----------------------------------------------------------------------------------------------------
    def dispatch(self, name: str, args: dict, principal: str):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise NotImplementedError(f"{name} is not implemented by the fake simlab-mcp (test fixture)")
        return fn(caller=principal, **(args or {}))

    def t_sim_whoami(self, *, caller):
        return {"principal": caller, "policy": self.policy, "tools": sorted(allowed_tools(self.policy)),
                "audit_log": "fake (test fixture)"}

    def t_sim_list_solvers(self, *, caller):
        return json.loads(json.dumps(self.solvers))

    def t_rig_capacity(self, *, caller, mode=None, pool=None):
        cap = dict(self.capacity)
        cap["text"] = "fake capacity (test fixture)"
        return cap

    def t_sim_list_experiments(self, *, caller):
        return sorted(self.exps)

    # files (section 5.1)
    def _check_name(self, name: str) -> list[str]:
        if not isinstance(name, str) or not name or len(name) > 255 or "\\" in name or name.startswith("/"):
            raise FileError(f"name {name!r} must be 1 to 255 characters of POSIX path components")
        comps = name.split("/")
        if not 1 <= len(comps) <= 4 or not all(_COMPONENT.match(c) for c in comps):
            raise FileError(f"name {name!r} must have 1 to 4 components of letters, digits, '.', '_' or '-'")
        last = comps[-1]
        if last.endswith((".yaml", ".yml", ".partial", ".partial.json")):
            raise FileError(f"name {name!r}: specs go through sim_put_spec, and .partial names are reserved")
        return comps

    def t_sim_put_file(self, *, caller, name, content_b64, total_bytes, sha256, offset=0, overwrite=False):
        comps = self._check_name(name)
        try:
            data = base64.b64decode(content_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise FileError(f"content_b64 is not standard base64 ({exc})") from None
        if len(data) > self.file_chunk_max:
            raise FileError(f"a chunk carries at most {self.file_chunk_max} decoded bytes, this one {len(data)}")
        if not isinstance(total_bytes, int) or not 0 <= total_bytes <= FILE_MAX_BYTES:
            raise FileError(f"total_bytes must be 0 to {FILE_MAX_BYTES}")
        if not isinstance(sha256, str) or not _HEX64.match(sha256):
            raise FileError("sha256 must be 64 lowercase hex characters")
        pdir = _principal_dir(caller)
        base = os.path.realpath(self.store_root())
        target = os.path.realpath(os.path.join(base, pdir, *comps))
        if not _inside(target, base):
            raise FileError("path escapes the spec store")
        partial, side = target + ".partial", target + ".partial.json"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        offset = int(offset)
        if offset == 0:
            with open(partial, "wb"):
                pass
            Path(side).write_text(json.dumps({"total_bytes": total_bytes, "sha256": sha256}), encoding="utf-8")
        else:
            try:
                meta = json.loads(Path(side).read_text(encoding="utf-8"))
                size = os.path.getsize(partial)
            except (OSError, ValueError):
                meta, size = {}, 0
            if meta != {"total_bytes": total_bytes, "sha256": sha256} or size != offset:
                raise FileError(f"expected offset {size} for {name}, got {offset}: restart the upload at offset 0")
        if offset + len(data) > total_bytes:
            raise FileError(f"offset {offset} + {len(data)} bytes is past total_bytes {total_bytes}")
        with open(partial, "ab") as fh:
            fh.write(data)
        received = offset + len(data)
        out = {"name": name, "principal": pdir, "complete": False, "received_bytes": received,
               "total_bytes": total_bytes, "path": None, "abs_path": None, "sha256": None, "existed": False,
               "replaced": False}
        if received < total_bytes:
            return out
        got = _sha(Path(partial))
        if got != sha256:
            for p in (partial, side):
                with contextlib.suppress(OSError):
                    os.unlink(p)
            raise FileError(f"received bytes hash to {got}, not {sha256}; the partial upload was discarded")
        if os.path.isfile(target):
            have = _sha(Path(target))
            if have == sha256:
                os.unlink(partial)
                out["existed"] = True
            elif not overwrite:
                for p in (partial, side):
                    with contextlib.suppress(OSError):
                        os.unlink(p)
                raise FileError(f"{name} already exists for this principal with sha256 {have}; pass overwrite=true")
            else:
                os.replace(partial, target)
                out["replaced"] = True
        else:
            os.link(partial, target)
            os.unlink(partial)
        with contextlib.suppress(OSError):
            os.unlink(side)
        out.update(complete=True, path=os.path.relpath(target, self.repo).replace(os.sep, "/"), abs_path=target,
                   sha256=sha256)
        return out

    def t_sim_get_file(self, *, caller, experiment_id, path, offset=0, max_bytes=2097152):
        with self.lock:
            exp = self.exps.get(experiment_id)
        if exp is None:
            raise KeyError(f"unknown experiment {experiment_id}")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
            raise FileError(f"path {path!r} must be relative POSIX components")
        comps = path.split("/")
        if not 1 <= len(comps) <= 8 or not all(_COMPONENT.match(c) for c in comps):
            raise FileError(f"path {path!r} must have 1 to 8 plain components")
        base = os.path.realpath(exp["exp_dir"])
        full = os.path.realpath(os.path.join(base, *comps))
        if not _inside(full, base) or not os.path.isfile(full):
            raise FileError(f"{path} is not a regular file of experiment {experiment_id}")
        size = os.path.getsize(full)
        offset, max_bytes = int(offset), int(max_bytes)
        if not 0 <= offset <= size or not 1 <= max_bytes <= self.file_chunk_max:
            raise FileError(f"offset must be 0 to {size} and max_bytes 1 to {self.file_chunk_max}")
        with open(full, "rb") as fh:
            fh.seek(offset)
            data = fh.read(max_bytes)
        sha = _sha(Path(full))
        rc = comps[0]
        scenario = self._scenario_of_rc(exp, rc)
        if scenario == "corrupt-file" and comps[-1] == "mask.npy" and data:
            data = bytes([data[0] ^ 0xFF]) + data[1:]  # fixture: bytes that do not hash to the reported sha256
        if scenario == "file-changes" and offset > 0:
            sha = hashlib.sha256(b"fixture: the file changed between chunks").hexdigest()
        return {"experiment_id": experiment_id, "path": path, "size": size, "sha256": sha, "offset": offset,
                "length": len(data), "eof": offset + len(data) >= size, "content_b64": base64.b64encode(data).decode(),
                "mtime_utc": dt.datetime.fromtimestamp(os.path.getmtime(full), dt.timezone.utc).isoformat()}

    # specs
    def _parse_spec(self, content: str):
        import yaml
        from simlab.spec import Experiment

        class NoAlias(yaml.SafeLoader):
            def compose_node(self, parent, index):
                if self.check_event(yaml.AliasEvent):
                    raise ValueError("YAML aliases are not allowed in uploaded specs")
                return super().compose_node(parent, index)

        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is empty")
        if len(content.encode("utf-8")) > SPEC_MAX_BYTES:
            raise ValueError(f"content is larger than {SPEC_MAX_BYTES} bytes")
        data = yaml.load(content, Loader=NoAlias)
        if not isinstance(data, dict):
            raise ValueError("content must be a YAML mapping (an experiment.yaml)")
        exp = Experiment(**data)
        self._check_physics(exp.solver, exp.physics)
        return exp

    def _check_physics(self, solver: str, physics: str) -> None:
        row = self.solvers.get(solver)
        if not isinstance(row, dict):
            raise KeyError(f"unknown solver {solver!r}")
        if physics not in (row.get("physics") or []):
            raise ValueError(f"solver {solver} does not cover physics {physics}")

    def t_sim_put_spec(self, *, caller, content, name=None, overwrite=False):
        from simlab.spec import load

        exp = self._parse_spec(content)
        fname = name or f"{exp.id}.yaml"
        if not _SPEC_NAME.match(fname):
            raise ValueError(f"name {fname!r} must be a bare file name ending in .yaml")
        pdir = _principal_dir(caller)
        path = self.store_root() / pdir / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        replaced = path.is_file()
        if replaced and not overwrite:
            raise ValueError(f"{fname} already exists for this principal (sha256 {_sha(path)}); pass overwrite=true to "
                             "replace it")
        path.write_bytes(content.encode("utf-8"))
        spec = load(str(path))
        return {"spec_path": path.relative_to(self.repo).as_posix(), "abs_path": str(path), "sha256": spec.spec_sha256,
                "bytes": path.stat().st_size, "id": spec.id, "solver": spec.solver, "physics": spec.physics,
                "n_cases": len(spec.cases()), "principal": pdir, "replaced": bool(replaced and overwrite),
                "warnings": []}

    def t_sim_list_specs(self, *, caller, **kw):
        from simlab.spec import load

        who = kw.get("principal")
        root = self.store_root()
        rows = []
        dirs = [_principal_dir(who)] if who else (sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else [])
        for d in dirs:
            for p in sorted((root / d).glob("*.y*ml")) if (root / d).is_dir() else []:
                rec = {"spec_path": p.relative_to(self.repo).as_posix(), "principal": d, "bytes": p.stat().st_size,
                       "sha256": _sha(p)}
                try:
                    s = load(str(p))
                    rec.update(id=s.id, solver=s.solver, physics=s.physics, n_cases=len(s.cases()))
                except Exception as exc:
                    rec["error"] = f"{type(exc).__name__}: {exc}"
                rows.append(rec)
        return rows

    def t_sim_validate(self, *, caller, spec_path):
        from simlab.spec import load

        spec = load(str(self.repo / spec_path))
        self._check_physics(spec.solver, spec.physics)
        return {"id": spec.id, "solver": spec.solver, "physics": spec.physics, "n_cases": len(spec.cases()),
                "cases": spec.cases()}

    # queue
    def _scenario_of_rc(self, exp: dict, rc: str) -> str:
        job = next((j for j in exp["jobs"] if j["rc"] == rc), None)
        return (job or {}).get("scenario") or "ok"

    def t_sim_submit(self, *, caller, spec_path, docker=True, out_root=None, mlflow_uri=None, lease_wait_s=None):
        from simlab.spec import load

        p = Path(spec_path) if os.path.isabs(spec_path) else self.repo / spec_path
        spec = load(str(p))
        self._check_physics(spec.solver, spec.physics)
        with self.lock:
            prev = self.exps.get(spec.id)
            if prev and os.path.abspath(prev["spec_abs"]) != os.path.abspath(str(p)):
                raise ValueError(f"experiment id {spec.id!r} is already used by {prev['spec_abs']}; give this spec its own id")
            exp_dir = self.queue_root() / spec.id
            needs_gpu = float(spec.resources.vram_gb) > 0
            wait = 6 * 3600 if lease_wait_s is None else max(60, min(21600, int(lease_wait_s)))
            exp = {"id": spec.id, "spec_abs": str(p), "spec_rel": spec_path, "exp_dir": str(exp_dir),
                   "queue": "sim" if needs_gpu else "cpu", "needs_gpu": needs_gpu, "submitted_utc": _now(),
                   "solver": spec.solver, "lease_wait_s": wait, "jobs": []}
            dark_after = False
            for case in spec.cases():
                rc = case["case_id"]
                run_dir = exp_dir / rc
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "case.json").write_text(json.dumps(case, indent=2), encoding="utf-8")
                gid = str((case.get("golden_case") or {}).get("case_id") or rc)
                scenario = self.scenarios.get(gid, "ok")
                job = {"job_id": f"{spec.id}--{rc}--run--{secrets.token_hex(3)}", "rc": rc, "gid": gid,
                       "status": "queued", "scenario": scenario, "error": None, "t_running": None,
                       "meta": {"experiment": spec.id, "case_id": rc, "run_dir": str(run_dir), "solver": spec.solver,
                                "needs_gpu": needs_gpu, "state": "queued", "stage": None, "final": True,
                                "updated_utc": _now()}}
                exp["jobs"].append(job)
                if scenario == "input-mismatch":
                    self._rewrite_upload(spec, case)
                if scenario == "dark-after-submit":
                    dark_after = True
            self.exps[spec.id] = exp
            if dark_after:
                self._dark_after = True
        return {"experiment": spec.id, "queue": exp["queue"], "jobs": [j["job_id"] for j in exp["jobs"]],
                "exp_dir": str(exp_dir), "needs_gpu": needs_gpu, "stages": [], "n_cases": len(exp["jobs"])}

    def _rewrite_upload(self, spec, case: dict) -> None:
        stl = ((case.get("golden") or {}).get("files") or {}).get("stl") or {}
        if stl.get("path"):
            target = Path(spec.resolve(stl["path"]))
            if target.is_file():
                with open(target, "ab") as fh:
                    fh.write(b"\nfixture: rewritten after the upload\n")

    def t_sim_status(self, *, caller, experiment_id):
        with self.lock:
            exp = self.exps.get(experiment_id)
        if exp is None:
            raise KeyError(f"unknown experiment {experiment_id!r}")
        for job in exp["jobs"]:
            self._advance(exp, job)
        with self.lock:
            jobs = [{"job_id": j["job_id"], "status": j["status"], "meta": dict(j["meta"]), "error": j["error"]}
                    for j in exp["jobs"]]
        counts: dict = {}
        for j in jobs:
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        return {"experiment": experiment_id, "queue": exp["queue"], "exp_dir": exp["exp_dir"], "spec": exp["spec_abs"],
                "submitted_utc": exp["submitted_utc"], "solver": exp["solver"], "n": len(jobs), "counts": counts,
                "jobs": jobs,
                "done": all(j["status"] in ("finished", "failed", "canceled", "stopped", "unknown") for j in jobs)}

    def _advance(self, exp: dict, job: dict) -> None:
        sc = job["scenario"]
        st, state = job["status"], job["meta"].get("state")
        if st in ("finished", "failed", "canceled", "stopped"):
            return
        if st == "queued":
            job["status"] = "started"
            if exp["needs_gpu"]:
                job["meta"].update(state="waiting_for_gpu", mode=self.capacity.get("mode"),
                                   pool=list(self.capacity.get("pool") or []), available=[], updated_utc=_now())
            else:
                job["meta"].update(state="running", gpu=None, owner="fake-worker (test fixture)", updated_utc=_now())
                job["t_running"] = time.monotonic()
            return
        if state == "waiting_for_gpu":
            if sc == "never-starts":
                return
            job["meta"].update(state="running", gpu=FAKE_JOB_GPU, owner="fake-worker (test fixture)", updated_utc=_now())
            job["t_running"] = time.monotonic()
            return
        if state == "running":
            if sc == "hang":
                return
            if sc.startswith("delay:") and time.monotonic() - (job["t_running"] or 0.0) < float(sc.split(":", 1)[1]):
                return
            self._execute(exp, job)

    def _execute(self, exp: dict, job: dict) -> None:
        """Emulate the simlab worker for an unstaged adapter around the real golden.rigside.run (test fixture)."""
        import simlab.evidence as sev
        from simlab.spec import load

        from aces_engineering.golden import case as case_mod, rigside

        run_dir = Path(job["meta"]["run_dir"])
        if job["scenario"] == "infra-fail":
            (run_dir / "worker.log").write_text("docker: Error response from daemon: No such image (fixture)\n",
                                                encoding="utf-8")
            job.update(status="failed", error="RuntimeError: exited with 125; see " + str(run_dir) + " (fixture)")
            job["meta"].update(state="failed", rc=125, updated_utc=_now())
            return
        spec = load(exp["spec_abs"])
        case = json.loads((run_dir / "case.json").read_text(encoding="utf-8"))
        env_fixture = {"host": "fake-rig (test fixture)", "platform": "fake (test fixture)", "in_container": True,
                       "image": "aces-golden:0.1", "image_digest": FAKE_IMAGE,
                       "cuda_visible_devices": str(FAKE_JOB_GPU) if exp["needs_gpu"] else None,
                       "gpus": [dict(FAKE_GPU)] if exp["needs_gpu"] else [],
                       "packages": {"python": sys.version.split()[0], "note": "test fixture"}}
        started = dt.datetime.now(dt.timezone.utc)
        status, results, error = "FINISHED", None, None
        saved = {k: os.environ.get(k) for k in ("ACES_GOLDEN_FORCE_DEVICE", "SIMLAB_IMAGE", "SIMLAB_IMAGE_DIGEST",
                                                "SIMLAB_PROGRESS_FILE")}
        with _EXEC_LOCK:
            os.environ.update({"ACES_GOLDEN_FORCE_DEVICE": "cpu", "SIMLAB_IMAGE": "aces-golden:0.1",
                               "SIMLAB_IMAGE_DIGEST": FAKE_IMAGE, "SIMLAB_PROGRESS_FILE": str(run_dir / "progress.json")})
            try:
                patches = []
                if job["scenario"] == "design-fail":
                    patches.append(mock.patch.object(case_mod, "run_job", _forced_unconverged))
                if job["scenario"] == "rig-exception":  # an exception inside the case body, as an infrastructure
                    from aces_engineering.golden import conforming as conf_mod, fea as fea_mod  # fault would raise

                    patches.append(mock.patch.object(fea_mod, "_solve_adapter", _rig_exception))
                    patches.append(mock.patch.object(conf_mod, "run_check", _rig_exception))
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    results = rigside.run(case, spec, str(run_dir), spec.solver)
            except Exception:
                status, error = "FAILED", traceback.format_exc()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            finished = dt.datetime.now(dt.timezone.utc)
            files = ((case.get("golden") or {}).get("files") or {})
            inputs = [spec.spec_path] + [spec.resolve(f["path"]) for f in files.values() if isinstance(f, dict) and f.get("path")]
            if spec.geometry.ref:
                inputs.append(spec.resolve(spec.geometry.ref))
            if status == "FINISHED":
                with open(run_dir / "results.json", "w", encoding="utf-8") as fh:
                    json.dump(results, fh, indent=2, default=str)
            with mock.patch.object(sev, "environment", lambda: dict(env_fixture)):
                sev.manifest(spec, case, str(run_dir), inputs, started, finished, status,
                             results if status == "FINISHED" else None, error)
            (run_dir / "status.json").write_text(json.dumps({"status": status, "mlflow_run_id": None,
                                                             "wall_s": (finished - started).total_seconds(),
                                                             "error": error, "stages": []}, indent=2), encoding="utf-8")
            (run_dir / "worker.log").write_text(f"fake simlab worker (test fixture): {spec.solver} {job['rc']} {status}\n"
                                                + (error or ""), encoding="utf-8")
            if job["scenario"] == "file-vanishes":  # a file files.json lists is gone before the desktop pulls it
                listed = json.loads((run_dir / "files.json").read_text(encoding="utf-8")).get("files") or []
                victim = next((f["path"] for f in listed if f["path"].endswith("/fields.npz")),
                              listed[0]["path"] if listed else None)
                if victim:
                    os.unlink(run_dir / victim)
        if status == "FINISHED":
            job.update(status="finished")
            job["meta"].update(state="finished", rc=0, elapsed_s=(finished - started).total_seconds(), updated_utc=_now())
        else:
            job.update(status="failed", error=(error or "")[-800:])
            job["meta"].update(state="failed", rc=1, elapsed_s=(finished - started).total_seconds(), updated_utc=_now())

    def t_sim_cancel(self, *, caller, experiment_id):
        with self.lock:
            exp = self.exps.get(experiment_id)
            if exp is None:
                raise KeyError(f"unknown experiment {experiment_id!r}")
            self.cancel_calls.append(experiment_id)
            out = {}
            for job in exp["jobs"]:
                if job["status"] in ("queued", "deferred", "scheduled"):
                    job["status"] = "canceled"
                    job["meta"]["state"] = "cancelled"
                    out[job["job_id"]] = "canceled"
                elif job["status"] == "started":
                    job["status"] = "stopped"
                    job["meta"]["state"] = "cancelled"
                    out[job["job_id"]] = "stop requested"
                else:
                    out[job["job_id"]] = job["status"]
            return out

    def t_sim_evidence(self, *, caller, experiment_id, company=None, note=None, push=None):
        with self.lock:
            exp = self.exps.get(experiment_id)
        if exp is None:
            raise KeyError(f"unknown experiment {experiment_id!r}")
        if any(j["scenario"] == "evidence-fail" for j in exp["jobs"]):
            raise ValueError("fixture: the evidence bundle could not be written")
        return {"ledger_entry": {"sha256": f"fake-{experiment_id} (test fixture)"}, "hypothesis": None,
                "n_finished": sum(1 for j in exp["jobs"] if j["status"] == "finished"), "note": note}

    def t_sim_results(self, *, caller, experiment_id, case_id=None):
        st = self.t_sim_status(caller=caller, experiment_id=experiment_id)
        rows = []
        for j in st["jobs"]:
            rd = Path(j["meta"]["run_dir"])
            res = json.loads((rd / "results.json").read_text(encoding="utf-8")) if (rd / "results.json").is_file() else None
            rows.append({"case_id": j["meta"]["case_id"], "status": j["status"], "run_dir": str(rd), "results": res,
                         "artifacts": (res or {}).get("artifacts", [])})
        return {"experiment": experiment_id, "done": st["done"], "counts": st["counts"], "cases": rows}


def _forced_unconverged(job: dict, kind: str) -> dict:
    """The design-fail scenario's case outcome (test fixture: no number is produced)."""
    c = job["case"]
    now = _now()
    return {"case_id": c["case_id"], "role": c.get("role"), "status": "FAILED", "design_status": "unconverged",
            "error": "fixture: forced unconverged", "results": None,
            "diagnostics": {"generator": None, "skin": None, "checks": None}, "started_utc": now, "finished_utc": now,
            "wall_s": 0.0, "stages": []}


RIG_EXCEPTION = "cupy.cuda.memory.OutOfMemoryError: out of device memory (test fixture: no device was asked)"


def _rig_exception(*args, **kwargs):
    """The rig-exception scenario: the solve (or the conforming check) raises, as a CUDA out-of-memory on a GPU that
    another process holds would (test fixture)."""
    raise RuntimeError(RIG_EXCEPTION)


def _reset(handler: BaseHTTPRequestHandler) -> None:
    """Close the connection with a TCP reset (SO_LINGER 0)."""
    with contextlib.suppress(OSError):
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    with contextlib.suppress(OSError):
        handler.connection.close()
    handler.close_connection = True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # quiet test output
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(405, b'{"error":"POST only (fixture)"}')

    def do_POST(self) -> None:
        fake: FakeSimlab = self.server.fake
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if fake.dark:
            return _reset(self)
        if fake.latency_s:
            time.sleep(fake.latency_s)
        auth = self.headers.get("Authorization") or ""
        principal = fake.principal_of(auth)
        try:
            msg = json.loads(body.decode("utf-8"))
        except ValueError:
            return self._send(400, b'{"error":"not JSON (fixture)"}')
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        tool = params.get("name") if method == "tools/call" else method
        args = dict(params.get("arguments") or {}) if method == "tools/call" else {}
        shown = {k: (v[:16] if k == "content_b64" and isinstance(v, str) else v) for k, v in args.items()}
        with fake.lock:
            fake.calls.append((str(tool), shown, bool(auth)))
        if principal is None:
            return self._send(401, UNAUTHORIZED)
        drop = False
        if method == "tools/call":
            with fake.lock:
                fake._n_tool_calls += 1
                faulted = fake._fault_now()
            if faulted:
                if fake.fault == "reset":
                    return _reset(self)
                if fake.fault == "drop":
                    drop = True
                else:
                    return self._send(int(fake.fault) if str(fake.fault).isdigit() else 503,
                                      b'{"error":"fixture: service unavailable"}')
        if rid is None:  # a notification
            return self._send(202)
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "simlab", "version": "0.4.0-fake"},
                      "instructions": "fake simlab-mcp (test fixture)"}
        elif method == "tools/list":
            result = {"tools": [{"name": t, "description": f"{t} (fake, test fixture)",
                                 "inputSchema": {"type": "object"}} for t in sorted(allowed_tools(fake.policy))]}
        elif method == "tools/call":
            if tool not in allowed_tools(fake.policy):
                result = {"content": [{"type": "text", "text": f"Unknown tool: {tool}"}], "isError": True}
            else:
                try:
                    res = fake.dispatch(str(tool), args, principal)
                    sc = res if isinstance(res, dict) else {"result": res}
                    result = {"content": [{"type": "text", "text": json.dumps(res, default=str)}],
                              "structuredContent": json.loads(json.dumps(sc, default=str)), "isError": False}
                except Exception as exc:
                    text = f"Error executing tool {tool}: {exc}"
                    result = {"content": [{"type": "text", "text": text}], "isError": True}
        else:
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            return self._send(200, json.dumps(reply).encode("utf-8"))
        if drop:
            return _reset(self)
        reply = {"jsonrpc": "2.0", "id": rid, "result": result}
        self._send(200, json.dumps(reply).encode("utf-8"))
        if fake._dark_after:
            fake._dark_after = False
            fake.go_dark()
