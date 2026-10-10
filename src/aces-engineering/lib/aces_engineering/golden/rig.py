"""aces golden on a rig lane: the desktop client of simlab-mcp (section 10 of the rig build spec).

With `--lane LANE` (or `[golden] lane` in aces.toml) the finer-resolution independent check (golden-check, a GPU) and the
conforming tet10 check (ccx-check, the rig CPU) run on the ACES Server through simlab-mcp, the only transport: inputs go
up with sim_put_file, the experiment YAML with sim_put_spec, the cases are queued with sim_submit (each GPU case waits
for a lease in the mode GRID CONTROL has set; nothing here switches modes), progress comes from sim_status, and every
record and output file comes back with sim_get_file, chunk by chunk, verified against the sha256 the rig reports and
against the rig's own files.json before anything is written into the run directory. `--rig-sweep` sends the sweep's
designs too (golden-case).

probe() runs in preflight, before a run directory exists: the lane must be in the fabric manifest, simlab-mcp must
answer with the 0.4.0 tools, the rig registry must carry the golden solvers built from this desktop's golden code
(golden.evidence.compute_digest) and the mode must release a simulation GPU. Any refusal is one line (exit 2 for a lane
given with --lane; a warning and a desktop run for a lane from aces.toml). The bearer token comes from
mcp_client.simlab_token() and is only ever sent as a header; records keep only where it came from.

RigSession submits a stage without blocking (uploads, rig/specs/<id>.yaml, sim_put_spec, sim_submit, rig/session.json),
then collects it: polls sim_status every POLL_S, shows state changes and the rig's own progress beacons, pulls each case
as it ends, classifies it (a result: FINISHED, or FAILED with a real design or conforming status; not delivered, with a
rig-* design status: no outcome, NOT_RUN, a rig-* status, an internal error "error", or a file the rig would not hand
over) and writes <case>/rig/ (the rig's records unchanged), <case>/evidence.json (a byte copy of the rig's),
<case>/results.json, <case>/status.json and <case>/rig.json. Every wait is bounded: the budget runs from an
experiment's submission to its last pulled file (then sim_cancel), and UNREACHABLE_S without a successful call ends the
wait (rig-unreachable). sim_submit is never retried blindly. Standard library only at module level (yaml is imported
when an experiment is written).
"""
from __future__ import annotations

import base64
import contextlib
import datetime as _dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import (RIG_STATUSES, SCHEMA, VERSION, NullProgress, _atomic_write, clean, dump_json, sha256_bytes, sha256_file,
               utc_now)

PROBE_TIMEOUT_S = 10.0
HTTP_TIMEOUT_S = 30.0
FILE_TIMEOUT_S = 120.0
CHUNK_BYTES = 2097152
POLL_S = 2.0
BEACON_EVERY_S = 4.0
UNREACHABLE_S = 120.0
DEFAULT_BUDGET_S = 1800.0
CANCEL_BUDGET_S = 10.0
LEASE_WAIT_MIN_S = 60
LEASE_WAIT_MAX_S = 21600
CHECK_CPU_THREADS = 4
DESIGN_CPU_THREADS = 4
CONFORMING_THREADS_RIG = 16
CHECK_WALL_H = 0.5
DESIGN_WALL_H = 0.25
CONFORMING_WALL_H = 1.0
MLFLOW_EXPERIMENT = "aces-golden"

REQUIRED_TOOLS = ("sim_whoami", "sim_list_solvers", "rig_capacity", "sim_put_file", "sim_put_spec", "sim_list_specs",
                  "sim_submit", "sim_status", "sim_get_file")
OPTIONAL_TOOLS = ("sim_cancel", "sim_evidence")
READ_TOOLS = frozenset({"sim_whoami", "sim_list_solvers", "rig_capacity", "sim_list_specs", "sim_status", "sim_get_file",
                        "sim_results", "sim_list_experiments"})
PARAMS_SCHEMA = "aces-golden-rig/1"
GOLDEN_SOLVERS = ("golden-case", "golden-check", "ccx-check")
STAGE_KIND = {"sweep": "design", "check": "mesh", "conf": "conforming"}
KIND_SOLVER = {"design": "golden-case", "mesh": "golden-check", "conforming": "ccx-check"}
EXP_ID_MAX = 41
SPEC_MAX_BYTES = 256 * 1024
RECORDS = ("outcome.json", "files.json", "results.json", "evidence.json", "status.json", "case.json", "worker.log")
FINAL_STATES = ("finished", "failed", "cancelled", "lost")
LOG_TAIL_LINES = 20
_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


class RigError(RuntimeError):
    """simlab-mcp answered with an error, or a reply did not make sense. The message never holds the token."""


class RigUnreachable(RigError):
    """No answer from simlab-mcp (refused, reset, timed out, HTTP 5xx) for UNREACHABLE_S on a read, or once on a write."""


class RigIntegrityError(RigError):
    """A pulled file did not verify twice: its bytes, the sha256 the rig reported and files.json disagree."""


# ----------------------------------------------------------------------------------------------------------- helpers

def clamp_lease_wait(budget_s: float) -> int:
    return max(LEASE_WAIT_MIN_S, min(LEASE_WAIT_MAX_S, int(budget_s)))


def image_token(value) -> str | None:
    """The image id: the first whitespace-separated token of an image digest or id (None for none)."""
    s = str(value or "").strip()
    return s.split()[0] if s else None


def short_image(value, n: int = 12) -> str:
    tok = image_token(value) or "-"
    return tok.split(":", 1)[1][:n] if ":" in tok else tok[:n]


def _when(s) -> _dt.datetime | None:
    if not s:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def _seconds(a, b) -> float | None:
    ta, tb = _when(a), _when(b)
    return (tb - ta).total_seconds() if ta and tb else None


def _last_line(text) -> str:
    lines = [ln for ln in str(text or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip()[:300] if lines else ""


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def user_config_dir() -> Path:
    return Path(os.environ.get("ACES_USER_CONFIG") or (Path.home() / ".config" / "aces"))


def experiment_id(run_id: str, stage: str) -> str:
    """golden-<run_id>-<stage> (sweep, check or conf); at most 41 characters (sibling container names and job ids)."""
    if stage not in STAGE_KIND:
        raise ValueError(f"unknown rig stage {stage!r}")
    eid = f"golden-{run_id}-{stage}"
    if len(eid) > EXP_ID_MAX or not re.fullmatch(r"[A-Za-z0-9_-]+", eid):
        raise RigError(f"experiment id {eid!r} is not a rig experiment id (letters, digits, '-' and '_', at most "
                       f"{EXP_ID_MAX} characters)")
    return eid


class _NoAliasDumper:  # the real class is built on first use, so importing this module does not import yaml
    pass


def spec_text(doc: dict) -> str:
    """The experiment YAML exactly as uploaded: yaml.dump with a SafeDumper that never writes anchors or aliases,
    key order kept, one flow-free block document (section 9.1). At most 256 KB."""
    import yaml

    global _NoAliasDumper
    if not issubclass(_NoAliasDumper, yaml.SafeDumper):
        class NoAlias(yaml.SafeDumper):
            def ignore_aliases(self, data):
                return True

        _NoAliasDumper = NoAlias
    text = yaml.dump(clean(doc), Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True, default_flow_style=False,
                     width=4096)
    if len(text.encode("utf-8")) > SPEC_MAX_BYTES:
        raise RigError(f"the experiment YAML of {doc.get('id')} is {len(text.encode('utf-8'))} bytes; simlab-mcp takes "
                       f"at most {SPEC_MAX_BYTES}")
    return text


# ------------------------------------------------------------------------------------------------------- lane choice

def choose_lane(cli_lane: str | None) -> tuple[str | None, str | None]:
    """(lane id or None, source): `--lane none` is the desktop (source "cli"); `--lane L` is L from "cli"; else
    `[golden] lane` in aces.toml (source "config"; "none" is the desktop); else no lane (None, None)."""
    if cli_lane is not None and str(cli_lane).strip():
        lane = str(cli_lane).strip()
        return (None, "cli") if lane == "none" else (lane, "cli")
    try:
        import aces_engineering

        golden = (aces_engineering.aces_config() or {}).get("golden") or {}
    except (Exception, SystemExit):  # aces_config raises SystemExit on an unreadable aces.toml
        golden = {}
    lane = golden.get("lane") if isinstance(golden, dict) else None
    if isinstance(lane, str) and lane.strip():
        lane = lane.strip()
        return (None, "config") if lane == "none" else (lane, "config")
    return None, None


def needs_for(check_mode: str, conforming_mode: str, rig_sweep: bool) -> dict:
    """What a run sends to the rig: the check levels unless --check off, the conforming check for --conforming auto or
    rig, the sweep's designs with --rig-sweep."""
    return {"sweep": bool(rig_sweep), "check": str(check_mode or "auto") != "off",
            "conforming": str(conforming_mode or "auto") in ("auto", "rig")}


def needed_solvers(needs: dict) -> list[str]:
    out = []
    if needs.get("check"):
        out.append("golden-check")
    if needs.get("conforming"):
        out.append("ccx-check")
    if needs.get("sweep"):
        out.append("golden-case")
    return out


def sent_text(scope: dict) -> str:
    parts = []
    if scope.get("sweep"):
        parts.append("the sweep's designs")
    if scope.get("check"):
        parts.append("the independent check")
    if scope.get("conforming"):
        parts.append("the conforming check")
    if not parts:
        return "nothing"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# ------------------------------------------------------------------------------------------------------------ client

class RigClient:
    """simlab-mcp over mcp_client.Client. Replies are unwrapped (isError -> RigError, {"result": X} -> X, other structured
    content as it is, else the JSON of the text blocks); HTTP 401 is RigError("HTTP 401"). Read-tier calls are retried
    with backoff (1, 2, 4, 8 s) until UNREACHABLE_S has passed without a successful call; sim_submit is never retried
    blindly; file chunks use FILE_TIMEOUT_S."""

    def __init__(self, url: str, token: str | None, *, timeout: float = HTTP_TIMEOUT_S):
        from .. import mcp_client

        self.url = url
        self._mcp = mcp_client
        self._client = mcp_client.Client(url, token, timeout)
        self.timeout = float(timeout)
        self.fail_since: float | None = None
        self.last_error: str | None = None
        self.server: dict = {"name": None, "version": None, "protocol": None}

    def __repr__(self) -> str:
        return f"RigClient({self.url!r})"

    def _request(self, method: str, params: dict | None, *, timeout: float):
        self._client.timeout = float(timeout)
        try:
            res = self._client.request(method, params)
        except self._mcp.MCPError as exc:
            kind, status = getattr(exc, "kind", None), getattr(exc, "status", None)
            if status == 401:
                raise RigError("HTTP 401") from None
            transport = kind in ("network", "protocol") or (kind == "http" and (status is None or status >= 500 or
                                                                             status == 429))
            if transport:
                self.last_error = str(exc)
                if self.fail_since is None:
                    self.fail_since = time.monotonic()
                raise RigUnreachable(str(exc)) from None
            self.fail_since = None
            raise RigError(str(exc)) from None
        self.fail_since = None
        return res

    def initialize(self, *, timeout: float = PROBE_TIMEOUT_S) -> dict:
        self._client.timeout = float(timeout)
        try:
            res = self._client.initialize() or {}
        except self._mcp.MCPError as exc:
            if getattr(exc, "status", None) == 401:
                raise RigError("HTTP 401") from None
            self.last_error = str(exc)
            if self.fail_since is None:
                self.fail_since = time.monotonic()
            raise RigUnreachable(str(exc)) from None
        self.fail_since = None
        srv = res.get("serverInfo") or {}
        self.server = {"name": srv.get("name"), "version": srv.get("version"), "protocol": self._client.protocol}
        return res

    def tools(self, *, timeout: float = PROBE_TIMEOUT_S) -> list[str]:
        res = self._request("tools/list", {}, timeout=timeout) or {}
        return sorted(str(t.get("name")) for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name"))

    @staticmethod
    def unwrap(res) -> Any:
        res = res or {}
        texts = [str(b.get("text", "")) for b in res.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        if res.get("isError"):
            raise RigError(texts[0] if texts else "the tool reported an error without a message")
        sc = res.get("structuredContent")
        if isinstance(sc, dict) and set(sc) == {"result"}:
            return sc["result"]
        if sc is not None:
            return sc
        joined = "".join(texts)
        try:
            return json.loads(joined)
        except ValueError:
            return joined

    def call(self, name: str, args: dict | None = None, *, timeout: float | None = None, retry: bool | None = None):
        retry = (name in READ_TOOLS) if retry is None else bool(retry)
        delays = (1.0, 2.0, 4.0, 8.0)
        k = 0
        while True:
            try:
                res = self._request("tools/call", {"name": name, "arguments": args or {}},
                                    timeout=timeout or self.timeout)
            except RigUnreachable:
                if not retry:
                    raise
                waited = time.monotonic() - (self.fail_since or time.monotonic())
                if waited >= UNREACHABLE_S:
                    raise
                time.sleep(max(0.0, min(delays[min(k, len(delays) - 1)], UNREACHABLE_S - waited)))
                k += 1
                continue
            return self.unwrap(res)

    # ---- the calls the golden path makes ------------------------------------------------------------------------
    def put_file(self, name: str, data: bytes) -> dict:
        """Upload `data` as `name` in CHUNK_BYTES chunks; existed counts as success; an error restarts at offset 0,
        once."""
        sha, total = sha256_bytes(data), len(data)
        for attempt in (1, 2):
            try:
                offset, res = 0, {}
                while True:
                    chunk = data[offset:offset + CHUNK_BYTES]
                    res = self.call("sim_put_file", {"name": name, "content_b64": base64.b64encode(chunk).decode("ascii"),
                                                     "total_bytes": total, "sha256": sha, "offset": offset,
                                                     "overwrite": False}, timeout=FILE_TIMEOUT_S) or {}
                    offset += len(chunk)
                    if res.get("complete"):
                        break
                    if offset >= total:
                        raise RigError(f"{name}: simlab-mcp did not complete the upload at {total} bytes")
                if res.get("sha256") != sha:
                    raise RigError(f"{name}: simlab-mcp stored sha256 {res.get('sha256')}, the upload was {sha}")
                return res
            except RigError:
                if attempt == 2:
                    raise
        raise RigError(f"{name}: upload failed")  # not reached

    def put_spec(self, text: str, name: str, principal: str | None) -> dict:
        """sim_put_spec; "already exists" is success when the stored spec (sim_list_specs) has this sha256. A transport
        error is retried once (create-or-fail makes that safe)."""
        sha = sha256_bytes(text.encode("utf-8"))
        res = None
        for attempt in (1, 2):
            try:
                res = self.call("sim_put_spec", {"content": text, "name": name}) or {}
                break
            except RigUnreachable:
                if attempt == 2:
                    raise
            except RigError as exc:
                if "already exists" not in str(exc):
                    raise
                rows = self.call("sim_list_specs", {"principal": principal} if principal else {}) or []
                hit = next((r for r in rows if isinstance(r, dict) and str(r.get("spec_path", "")).endswith("/" + name)),
                           None)
                if hit and hit.get("sha256") == sha:
                    return {"spec_path": hit.get("spec_path"), "sha256": sha, "existed": True}
                raise RigError(f"{name} already exists on the rig with sha256 {(hit or {}).get('sha256')}, this spec is "
                               f"{sha}") from None
        if (res or {}).get("sha256") != sha:
            raise RigError(f"simlab-mcp stored {name} with sha256 {(res or {}).get('sha256')}, the spec sent was {sha}")
        return res

    def submit(self, spec_path: str, exp_id: str, lease_wait_s: int) -> dict:
        """sim_submit, never retried blindly: after a transport error, sim_status tells whether the experiment is known
        (then the submit counts as done); only an unknown experiment is submitted once more."""
        args = {"spec_path": spec_path, "docker": True, "lease_wait_s": int(lease_wait_s)}
        try:
            return self.call("sim_submit", args, retry=False) or {}
        except RigUnreachable:
            pass
        try:
            st = self.call("sim_status", {"experiment_id": exp_id}) or {}
        except RigUnreachable:
            raise
        except RigError:
            st = None
        if st:
            return {"experiment": exp_id, "queue": st.get("queue"), "jobs": [j.get("job_id") for j in st.get("jobs") or []],
                    "exp_dir": st.get("exp_dir"), "recovered": True}
        return self.call("sim_submit", args, retry=False) or {}

    def cancel(self, exp_id: str) -> Any:
        return self.call("sim_cancel", {"experiment_id": exp_id}, timeout=CANCEL_BUDGET_S, retry=False)

    def get_once(self, exp_id: str, path: str, *, retry: bool = True) -> tuple[bytes | None, str | None, int, str | None]:
        """One pass over a rig file: (bytes, sha256, chunks, problem). The problem is None when every chunk reported the
        first chunk's sha256 and the assembled bytes hash to it."""
        offset, parts, first, chunks = 0, [], None, 0
        while True:
            res = self.call("sim_get_file", {"experiment_id": exp_id, "path": path, "offset": offset,
                                             "max_bytes": CHUNK_BYTES}, timeout=FILE_TIMEOUT_S, retry=retry) or {}
            chunks += 1
            if first is None:
                first = res.get("sha256")
            elif res.get("sha256") != first:
                return None, None, chunks, (f"{path}: the sha256 the rig reported changed between chunks ({first} then "
                                            f"{res.get('sha256')})")
            try:
                data = base64.b64decode(res.get("content_b64") or "", validate=True)
            except (ValueError, TypeError):
                return None, None, chunks, f"{path}: chunk at offset {offset} is not base64"
            if res.get("length") is not None and len(data) != int(res["length"]):
                return None, None, chunks, f"{path}: chunk at offset {offset} has {len(data)} bytes, {res['length']} reported"
            parts.append(data)
            offset += len(data)
            if res.get("eof"):
                break
            if not data:
                return None, None, chunks, f"{path}: no bytes at offset {offset} before the end of the file"
        blob = b"".join(parts)
        got = sha256_bytes(blob)
        if got != first:
            return None, None, chunks, f"{path}: the pulled bytes hash to {got}, the rig reported {first}"
        return blob, got, chunks, None

    def get_file(self, exp_id: str, path: str, *, expect_sha256: str | None = None) -> tuple[bytes, str, int]:
        """A verified rig file: (bytes, sha256, chunks). A mismatch pulls the whole file again once, then raises
        RigIntegrityError naming the file."""
        problem = None
        for _attempt in (1, 2):
            data, sha, chunks, problem = self.get_once(exp_id, path)
            if problem is None and expect_sha256 is not None and sha != expect_sha256:
                problem = f"{path}: the pulled bytes hash to {sha}, files.json says {expect_sha256}"
            if problem is None:
                return data, sha, chunks
        raise RigIntegrityError(problem or f"{path}: did not verify")


# ------------------------------------------------------------------------------------------------------------- probe

def _refuse(lane: str, reason: str) -> str:
    return f"lane '{lane}' cannot be used: {reason}; nothing was submitted"


def _solver_rows(registry: dict) -> dict:
    out = {}
    for sid in GOLDEN_SOLVERS:
        row = registry.get(sid) if isinstance(registry, dict) else None
        if not isinstance(row, dict):
            out[sid] = None
            continue
        g, v = row.get("golden") or {}, row.get("validation") or {}
        out[sid] = {"image": row.get("image"), "validation": v.get("status"), "validation_image_id": v.get("image_id"),
                    "compute_sha256": g.get("compute_sha256")}
    return out


def probe(lane: str, lane_source: str, *, check_mode: str = "auto", conforming_mode: str = "auto",
          rig_sweep: bool = False, compute_sha256: str | None = None, warn: Callable[[str], None] | None = None
          ) -> tuple[dict, RigClient | None]:
    """The lane probe of section 10.5: (record, client). record["ok"] is True when the lane can take this run's rig work;
    otherwise record["refusal"] is the one-line refusal (R1-R12) and nothing was submitted. The client is None when
    the probe never reached simlab-mcp."""
    from .. import mcp_client
    from . import compute

    t0 = time.monotonic()
    token, token_source = mcp_client.simlab_token(warn=warn)
    rec: dict = {"ok": False, "lane": lane, "lane_source": lane_source, "url": None,
                 "manifest": {"path": None, "age_s": None, "stale": None, "mode": None},
                 "server": {"name": None, "version": None, "protocol": None}, "principal": None, "policy": None,
                 "token_source": token_source, "tools": [], "missing_optional": [],
                 "solvers": {sid: None for sid in GOLDEN_SOLVERS},
                 "capacity": {"mode": None, "pool": None, "available": None, "derated": None, "source": None,
                              "errors": None},
                 "notes": [], "refusal": None, "probed_utc": utc_now(), "wall_s": None}
    needs = needs_for(check_mode, conforming_mode, rig_sweep)

    def done(reason: str | None, client: RigClient | None = None) -> tuple[dict, RigClient | None]:
        rec["refusal"] = _refuse(lane, reason) if reason else None
        rec["ok"] = reason is None
        rec["wall_s"] = round(time.monotonic() - t0, 3)
        return clean(rec), (client if reason is None else None)

    path, max_age, data, age, err = compute._read_manifest()
    rec["manifest"]["path"] = str(path)
    if err:
        rec["notes"].append(f"the fabric manifest could not be read ({err})")
    if data is None:
        return done("there is no fabric manifest (aces fabric refresh writes it)")
    mode = data.get("mode")
    rec["manifest"].update(age_s=round(age, 1) if age is not None else None,
                           stale=bool(age is not None and age > max_age),
                           mode=mode.get("name") if isinstance(mode, dict) else mode)
    lanes = [x for x in data.get("lanes") or [] if isinstance(x, dict)]
    found = next((x for x in lanes if x.get("id") == lane), None)
    if found is None:
        ids = [str(x.get("id")) for x in lanes if x.get("kind") == "sim" and x.get("id")]
        return done(f"it is not in the fabric manifest (sim lanes: {', '.join(ids) if ids else 'none'})")
    if found.get("kind") != "sim":
        return done(f"it is a {found.get('kind') or 'untyped'} lane, not a simulation lane")
    url = found.get("endpoint") or ((data.get("server") or {}).get("simlab_mcp"))
    if not url:
        return done("the fabric manifest gives it no simlab-mcp endpoint")
    rec["url"] = url
    if rec["manifest"]["stale"]:
        rec["notes"].append(f"the fabric manifest is {rec['manifest']['age_s']:g} s old (more than {max_age:g} s): "
                            "noted only; the live probe decides")
    if not (needs["check"] or needs["conforming"] or needs["sweep"]):
        return done(f"--lane sends the independent check and the conforming check to the rig, and both are off here "
                    f"(--check {check_mode}, --conforming {conforming_mode})")
    client = RigClient(url, token, timeout=PROBE_TIMEOUT_S)
    cfg = user_config_dir()
    try:
        client.initialize(timeout=PROBE_TIMEOUT_S)
        rec["server"] = dict(client.server)
        tools = client.tools(timeout=PROBE_TIMEOUT_S)
        rec["tools"] = tools
        rec["missing_optional"] = [t for t in OPTIONAL_TOOLS if t not in tools]
        if "sim_whoami" in tools:
            who = client.call("sim_whoami", {}, timeout=PROBE_TIMEOUT_S, retry=False) or {}
            rec["principal"], rec["policy"] = who.get("principal"), who.get("policy")
        missing = [t for t in REQUIRED_TOOLS if t not in tools]
        if missing:
            return done(f"simlab-mcp at {url} (policy {rec['policy'] or 'unknown'}) does not offer {', '.join(missing)}: "
                        "deploy simlab 0.4.0 on the rig (cudax-sim-runtime docs/GOLDEN_RIG.md)")
        registry = client.call("sim_list_solvers", {}, timeout=PROBE_TIMEOUT_S, retry=False) or {}
        rec["solvers"] = _solver_rows(registry)
        cap = client.call("rig_capacity", {}, timeout=PROBE_TIMEOUT_S, retry=False) or {}
        rec["capacity"] = {k: cap.get(k) for k in ("mode", "pool", "available", "derated", "source", "errors")}
    except RigUnreachable as exc:
        return done(f"simlab-mcp at {url} did not answer ({exc})")
    except RigError as exc:
        if str(exc) == "HTTP 401":
            return done(f"simlab-mcp at {url} refused the request (HTTP 401): set ACES_SIMLAB_TOKEN or put "
                        f"ACES_SIMLAB_TOKEN=... in {cfg}/simlab.env")
        return done(f"simlab-mcp at {url} did not answer ({exc})")
    for t in rec["missing_optional"]:
        if t == "sim_cancel":
            rec["notes"].append(f"sim_cancel not offered (policy {rec['policy']}): a spent budget leaves jobs to the "
                                "server's lease wait")
        else:
            rec["notes"].append(f"{t} not offered (policy {rec['policy']}): no rig ledger cross-reference")
    wanted = needed_solvers(needs)
    absent = [s for s in wanted if rec["solvers"].get(s) is None]
    if absent:
        return done(f"the rig registry has no {', '.join(absent)}")
    unrecorded = [s for s in wanted if not rec["solvers"][s].get("compute_sha256")]
    if unrecorded:
        s = unrecorded[0]
        return done(f"the rig registry does not say which golden code {rec['solvers'][s].get('image') or 'aces-golden:0.1'} "
                    f"carries ({s} golden.compute_sha256 is null): build, ship and record the image (docs/GOLDEN_RIG.md)")
    if compute_sha256 is None:
        from .evidence import compute_digest

        compute_sha256 = compute_digest()["golden_compute_sha256"]
    other = [s for s in wanted if rec["solvers"][s].get("compute_sha256") != compute_sha256]
    if other:
        x = str(rec["solvers"][other[0]].get("compute_sha256"))
        return done(f"the rig's aces-golden image carries other golden code (rig {x[:12]}, here {compute_sha256[:12]}): "
                    "build and ship aces-golden from this code and record it in simlab/solvers.d/golden.json")
    gpu_work = needs["check"] or needs["sweep"]
    if gpu_work and not (rec["capacity"].get("pool") or []):
        return done(f"the rig's mode {rec['capacity'].get('mode')} releases no simulation GPU (pool []); mode switching "
                    "is a human act in GRID CONTROL")
    client.timeout = HTTP_TIMEOUT_S
    return done(None, client)


def refusal_reason(rec: dict | None) -> str | None:
    """The bare reason of a probe refusal: rec["refusal"] without "lane 'L' cannot be used: " and "; nothing was
    submitted" (the config-lane warning and the plan quote it)."""
    r = (rec or {}).get("refusal")
    if not r:
        return None
    s = str(r)
    head, tail = f"lane '{(rec or {}).get('lane')}' cannot be used: ", "; nothing was submitted"
    if s.startswith(head):
        s = s[len(head):]
    if s.endswith(tail):
        s = s[:-len(tail)]
    return s


def probe_lines(rec: dict, plan_rig: dict | None = None) -> list[str]:
    """The `rig:` lines of `aces golden compute --lane`."""
    if not rec:
        return []
    out = []
    if rec.get("refusal"):
        out.append(f"rig: {rec['refusal']}")
    srv = rec.get("server") or {}
    out.append(f"rig: lane {rec.get('lane')} ({rec.get('lane_source')}) at {rec.get('url') or '-'} · simlab "
               f"{srv.get('version') or '-'} · principal {rec.get('principal') or '-'} · policy {rec.get('policy') or '-'} "
               f"· token {rec.get('token_source')}")
    tools = rec.get("tools") or []
    if tools:
        out.append(f"rig: {len(tools)} tools" + (f"; missing optional {', '.join(rec.get('missing_optional') or [])}"
                                                 if rec.get("missing_optional") else ""))
    for sid, row in (rec.get("solvers") or {}).items():
        if row is None:
            out.append(f"rig: solver {sid} not registered")
            continue
        out.append(f"rig: solver {sid} · image {row.get('image')} · validation {row.get('validation')} "
                   f"(image {short_image(row.get('validation_image_id'))}) · golden code "
                   f"{str(row.get('compute_sha256') or 'not recorded')[:12]}")
    cap = rec.get("capacity") or {}
    if cap.get("mode") is not None or cap.get("pool") is not None:
        out.append(f"rig: mode {cap.get('mode')} · pool {cap.get('pool')} · available {cap.get('available')}")
    for n in rec.get("notes") or []:
        out.append(f"rig: note: {n}")
    if plan_rig:
        out.append(f"rig: plan: {plan_rig.get('reason')}")
    return out


# ------------------------------------------------------------------------------------------------------ job states

def job_state(job: dict | None) -> str:
    """queued | waiting | running | finished | failed | cancelled | lost | unknown, from the rq status and meta."""
    if not job:
        return "unknown"
    st = str(job.get("status") or "")
    ms = (job.get("meta") or {}).get("state")
    if st in ("queued", "deferred", "scheduled"):
        return "queued"
    if st == "started":
        if ms == "waiting_for_gpu":
            return "waiting"
        if ms == "running":
            return "running"
        return "unknown"
    if st == "finished":
        return "finished"
    if st == "failed":
        return "cancelled" if ms == "cancelled" else "failed"
    if st in ("canceled", "stopped"):
        return "cancelled"
    if st == "unknown":
        return "lost"
    return "unknown"


def _rc_of(job: dict) -> str | None:
    cid = (job.get("meta") or {}).get("case_id")
    if cid:
        return str(cid)
    parts = str(job.get("job_id") or "").split("--")
    return parts[1] if len(parts) >= 3 else None


# --------------------------------------------------------------------------------------------------------- session

class RigSession:
    """One run's work on a rig lane (section 10.6). Built in preflight from a successful probe; bind() attaches the
    run directory once it exists."""

    def __init__(self, probe_rec: dict, client: RigClient, *, budget_s: float = DEFAULT_BUDGET_S, scope: dict,
                 progress=None):
        self.probe = dict(probe_rec)
        self.client = client
        self.lane = probe_rec.get("lane")
        self.lane_source = probe_rec.get("lane_source")
        self.url = probe_rec.get("url")
        self.principal = probe_rec.get("principal")
        self.policy = probe_rec.get("policy")
        self.tools = list(probe_rec.get("tools") or [])
        self.budget_s = float(budget_s)
        self.scope = {"sweep": bool(scope.get("sweep")), "check": bool(scope.get("check")),
                      "conforming": bool(scope.get("conforming"))}
        self.progress = progress or NullProgress()
        self.run = None
        self.eff: dict = {}
        self.domain: dict = {}
        self.run_experiment = None
        self.operator = "unknown"
        self.host = None
        self.uploads: list[dict] = []
        self._uploaded: dict[str, dict] = {}
        self.experiments: dict[str, dict] = {}
        self.events: list[dict] = []
        self.cancelled: list[dict] = []
        self.integrity_error: str | None = None
        self.results: dict[str, dict] = {}
        self.started_utc = utc_now()
        self._t0 = time.monotonic()
        self.finished_utc: str | None = None
        self.wall_s: float | None = None
        self.closed = False

    # ---- set-up ---------------------------------------------------------------------------------------------------
    def bind(self, run, eff: dict, domain: dict, experiment, *, operator: str, host: str | None, progress=None) -> None:
        self.run, self.eff, self.domain, self.run_experiment = run, eff, domain or {}, experiment
        self.operator, self.host = operator or "unknown", host
        if progress is not None:
            self.progress = progress
        self.write_session()

    def offers(self, tool: str) -> bool:
        return tool in self.tools

    def solver_row(self, solver: str) -> dict:
        return dict(((self.probe.get("solvers") or {}).get(solver)) or {})

    # ---- records --------------------------------------------------------------------------------------------------
    def _exp_public(self, e: dict) -> dict:
        return {k: e.get(k) for k in ("id", "stage", "kind", "solver", "spec", "spec_sha256", "spec_path",
                                      "submitted_utc", "finished_utc", "wall_s", "queue", "exp_dir", "lease_wait_s",
                                      "cases", "jobs", "counts", "cancelled", "rig_evidence")}

    def session_doc(self) -> dict:
        p = self.probe
        return clean({
            "schema": SCHEMA["rig-session"], "lane": self.lane, "lane_source": self.lane_source, "url": self.url,
            "token_source": p.get("token_source"), "principal": self.principal, "policy": self.policy,
            "server": p.get("server"), "tools": self.tools, "missing_optional": p.get("missing_optional"),
            "solvers": p.get("solvers"), "capacity": p.get("capacity"), "manifest": p.get("manifest"),
            "budget_s": self.budget_s, "scope": self.scope, "uploads": self.uploads,
            "experiments": [self._exp_public(e) for e in self._ordered()], "events": self.events,
            "cancelled": self.cancelled, "integrity_error": self.integrity_error, "started_utc": self.started_utc,
            "finished_utc": self.finished_utc, "wall_s": self.wall_s})

    def _ordered(self) -> list[dict]:
        return sorted(self.experiments.values(), key=lambda e: e["_order"])

    def write_session(self) -> None:
        if self.run is not None:
            self.run.write_json("rig/session.json", self.session_doc())

    def _event(self, exp: dict | None, case: str | None, state: str, gpu=None, detail: str | None = None,
               line: str | None = None) -> None:
        ev = {"t": round(time.monotonic() - self._t0, 3), "utc": utc_now(), "experiment": (exp or {}).get("id"),
              "case": case, "state": state, "gpu": gpu, "detail": detail}
        self.events.append(ev)
        if line:
            self.progress.line(line)
        self.progress.event("rig", experiment=ev["experiment"], case=case, state=state, gpu=gpu, detail=detail)

    # ---- uploads --------------------------------------------------------------------------------------------------
    def _upload(self, local_rel: str, file_name: str) -> dict:
        """Upload <run>/<local_rel> as golden/<run_id>/<file_name>; returns the params.golden.files entry."""
        name = f"golden/{self.run.run_id}/{file_name}"
        local = self.run.path(*local_rel.split("/"))
        data = Path(local).read_bytes()
        sha = sha256_bytes(data)
        prev = self._uploaded.get(name)
        if prev is None or prev["sha256"] != sha:
            res = self.client.put_file(name, data)
            prev = {"name": name, "local": local_rel, "sha256": sha, "bytes": len(data),
                    "existed": bool(res.get("existed")), "uploaded_utc": utc_now()}
            self._uploaded[name] = prev
            self.uploads.append(prev)
        return {"path": name, "sha256": sha, "bytes": len(data)}

    def _files_for(self, kind: str, source_case: str | None) -> dict:
        files = {"eff": self._upload("inputs/spec.effective.json", "spec.effective.json"),
                 "domain": self._upload("domain/domain.json", "domain.json")}
        if kind == "design":
            files["mask"] = self._upload("domain/mask.npy", "domain.mask.npy")
            if self.domain.get("sdf_field_path"):
                files["sdf"] = self._upload(str(self.domain["sdf_field_path"]), "domain.sdf_field.npy")
        else:
            if not source_case:
                raise RigError(f"a {kind} experiment needs the source case of its part")
            entry = self._upload(f"{source_case}/part.stl", f"{source_case}.part.stl")
            files["stl"] = dict(entry, case_id=source_case)
        return files

    # ---- the experiment document --------------------------------------------------------------------------------
    def _doc(self, stage: str, exp_id: str, cases: list[dict], files: dict, source_case: str | None) -> dict:
        from . import compute
        from .evidence import compute_digest

        kind, solver = STAGE_KIND[stage], KIND_SOLVER[STAGE_KIND[stage]]
        eff, d = self.eff, (self.eff.get("derived") or {})
        run = self.run
        name = eff.get("name") or "spec"
        env = (d.get("grid") or {}).get("envelope_mm") or [0, 0, 0]
        tol = float(d.get("tol") if d.get("tol") is not None else 0.10)
        golden = {"schema": PARAMS_SCHEMA, "kind": kind, "stage": stage, "run_id": run.run_id, "project": run.project,
                  "golden_version": VERSION, "golden_compute_sha256": compute_digest()["golden_compute_sha256"],
                  "desktop_host": self.host, "lane": self.lane, "files": files}
        if stage == "check":
            hs = [float(c["h_fea_mm"]) for c in cases]
            cells = max(compute._cells(env, h) for h in hs)
            desc = (f"aces golden {name} run {run.run_id}: finer-resolution independent check of {source_case}/part.stl "
                    f"on the rig ({', '.join(f'{h:g} mm' for h in hs)})")
            geometry = {"source": "mesh", "ref": files["stl"]["path"]}
            resources = {"vram_gb": max(1.0, round(2 * cells * 700 / 2 ** 30, 1)), "cpu_threads": CHECK_CPU_THREADS,
                         "wall_h": CHECK_WALL_H, "precision": "float64"}
            h1 = next((float(c["h_fea_mm"]) for c in cases if c.get("level") == 1), hs[0])
            expected = (f"the level-1 check at {h1:g} mm agrees with the {float(d.get('fea_mm') or 0):g} mm verify solve "
                        f"within {100 * tol:g} % and meets every constraint")
        elif stage == "conf":
            h = float(cases[0]["h_mm"])
            desc = (f"aces golden {name} run {run.run_id}: conforming tet10 second discretisation of "
                    f"{source_case}/part.stl on the rig CPU ({h:g} mm)")
            geometry = {"source": "mesh", "ref": files["stl"]["path"]}
            resources = {"vram_gb": 0.0, "cpu_threads": CONFORMING_THREADS_RIG, "wall_h": CONFORMING_WALL_H,
                         "precision": "float64"}
            expected = (f"the conforming tet10 solve at {h:g} mm agrees with the finest voxel solve within its bands and "
                        "meets every constraint")
        else:
            cells = max(compute._cells(env, float(d["gen_mm"])), compute._cells(env, float(d["fea_mm"])))
            desc = (f"aces golden {name} run {run.run_id}: {len(cases)} sweep design(s) on the rig (SIMP, organic skin, "
                    f"voxel FEA at {float(d['fea_mm']):g} mm)")
            geometry = {"source": "none"}
            resources = {"vram_gb": max(1.0, round(2 * cells * 700 / 2 ** 30, 1)), "cpu_threads": DESIGN_CPU_THREADS,
                         "wall_h": DESIGN_WALL_H, "precision": "float64"}
            expected = ("every design solves on the rig with this desktop's golden code, as it would on this desktop "
                        "(preview-grade)")
            dom_in = run.read_json("inputs/domain.json") or {}
            golden["domain_step_sha256"] = dom_in.get("step_sha256") or self.domain.get("step_sha256")
        rx = self.run_experiment
        return {
            "id": exp_id, "project": run.project, "owner": self.operator, "physics": "structures", "solver": solver,
            "description": desc, "geometry": geometry, "params": {"golden": golden}, "sweep": {"golden_case": cases},
            "outputs": list(d.get("outputs") or []), "resources": resources, "tracking": {"experiment": MLFLOW_EXPERIMENT},
            "evidence": True, "hypothesis": getattr(rx, "hypothesis", None), "expected": expected,
            "rd_activity": getattr(rx, "rd_activity", None), "company": getattr(rx, "company", None),
            "tags": {"golden": VERSION, "golden_run": run.run_id, "golden_project": run.project, "golden_stage": stage,
                     "golden_lane": str(self.lane), "desktop_host": str(self.host)}}

    # ---- submission -----------------------------------------------------------------------------------------------
    def submit(self, stage: str, cases: list[dict], *, source_case: str | None = None) -> str:
        """Submit one stage without waiting (section 10.6). Any failure makes every case of the experiment not
        delivered ("submission failed: <msg>"). Returns the experiment id."""
        if self.run is None:
            raise RuntimeError("RigSession.submit before bind()")
        if stage in self.experiments:
            raise RigError(f"the {stage} experiment of this run was already submitted")
        exp_id = experiment_id(self.run.run_id, stage)
        kind = STAGE_KIND[stage]
        e = {"id": exp_id, "stage": stage, "kind": kind, "solver": KIND_SOLVER[kind], "spec": f"rig/specs/{exp_id}.yaml",
             "spec_sha256": None, "spec_path": None, "submitted_utc": None, "finished_utc": None, "wall_s": None,
             "queue": None, "exp_dir": None, "lease_wait_s": clamp_lease_wait(self.budget_s),
             "cases": {f"c{i:03d}": c["case_id"] for i, c in enumerate(cases)}, "jobs": {},
             "counts": {"cases": len(cases), "finished": 0, "failed": 0, "delivered": 0, "not_delivered": 0},
             "cancelled": False, "rig_evidence": None,
             "_order": len(self.experiments), "_t_submit": time.monotonic(), "_cases": {c["case_id"]: c for c in cases},
             "_states": {}, "_status": {}, "_collected": False, "_evidence_done": False}
        self.experiments[stage] = e
        for c in cases:
            self.run.register_case(c)
        try:
            files = self._files_for(kind, source_case)
            doc = self._doc(stage, exp_id, cases, files, source_case)
            text = spec_text(doc)
            raw = text.encode("utf-8")
            _atomic_write(self.run.path("rig", "specs", f"{exp_id}.yaml"), raw)
            e["spec_sha256"] = sha256_bytes(raw)
            put = self.client.put_spec(text, f"{exp_id}.yaml", self.principal)
            e["spec_path"] = put.get("spec_path")
            sub = self.client.submit(e["spec_path"], exp_id, e["lease_wait_s"])
            e["submitted_utc"] = utc_now()
            e["queue"], e["exp_dir"] = sub.get("queue"), sub.get("exp_dir")
            jobs = [str(j) for j in sub.get("jobs") or []]
            for j in jobs:
                parts = j.split("--")
                if len(parts) >= 3 and parts[1] in e["cases"]:
                    e["jobs"][parts[1]] = j
            self._event(e, None, "submitted", detail=f"{len(cases)} case(s), lease wait {e['lease_wait_s']} s",
                        line=f"rig: {exp_id} submitted ({', '.join(f'{rc} {g}' for rc, g in e['cases'].items())}) to "
                             f"{self.url} as {self.principal}")
        except (RigError, OSError, ValueError) as exc:  # a local file that cannot be read fails the stage, not the run
            reason = f"submission failed: {exc}"
            self._event(e, None, "not-run", detail=reason, line=f"rig: {exp_id} {reason}")
            for rc, gid in e["cases"].items():
                self._not_delivered(e, rc, gid, "rig-error", reason, job=None)
            e["_collected"] = True
            self._finish_exp(e)
        self.write_session()
        return exp_id

    # ---- collection -----------------------------------------------------------------------------------------------
    def collect(self, stage: str, *, on_case: Callable[[str, dict], None] | None = None) -> dict:
        """Wait for one stage's cases, pull and record each as it ends; returns {golden case id: result}. A result is
        {"case_id", "rig_case", "job_id", "state": "result"|"not-run", "outcome", "design_status", "reason", "rig"}."""
        e = self.experiments.get(stage)
        if e is None:
            return {}
        if e["_collected"]:
            out = {gid: self.results[gid] for gid in e["cases"].values() if gid in self.results}
            if on_case:
                for gid, res in out.items():
                    on_case(gid, res)
            return out
        pending = {rc: gid for rc, gid in e["cases"].items() if gid not in self.results}
        states = e["_states"]
        deadline = e["_t_submit"] + self.budget_s
        last_beacon = time.monotonic()

        def beacons() -> list[Path]:
            return [Path(self.run.root) / gid / "progress.json" for rc, gid in e["cases"].items()
                    if states.get(rc) == "running" and rc in pending]

        completed = False
        try:
            with self.progress.watch(beacons, f"rig {stage}"):
                while pending:
                    try:
                        st = self.client.call("sim_status", {"experiment_id": e["id"]}) or {}
                    except RigUnreachable as exc:
                        self._lost_contact(e, pending, str(exc), on_case)
                        break
                    except RigError as exc:
                        reason = f"sim_status failed for {e['id']}: {exc}"
                        for rc in list(pending):
                            gid = pending.pop(rc)
                            res = self._not_delivered(e, rc, gid, "rig-error", reason, job=None)
                            if on_case:
                                on_case(gid, res)
                        break
                    e["_status"] = {k: st.get(k) for k in ("queue", "exp_dir", "submitted_utc", "counts")}
                    e["queue"] = st.get("queue") or e["queue"]
                    e["exp_dir"] = st.get("exp_dir") or e["exp_dir"]
                    jobs = {}
                    for j in st.get("jobs") or []:
                        rc = _rc_of(j) if isinstance(j, dict) else None
                        if rc:
                            jobs[rc] = j
                            e["jobs"].setdefault(rc, j.get("job_id"))
                    for rc in list(pending):
                        job = jobs.get(rc)
                        state = job_state(job)
                        if state != states.get(rc):
                            states[rc] = state
                            self._state_line(e, rc, pending[rc], state, job)
                        if state in FINAL_STATES:
                            gid = pending.pop(rc)
                            try:
                                res = self._pull_case(e, rc, gid, job, state)
                            except RigUnreachable as exc:
                                pending[rc] = gid
                                self._lost_contact(e, pending, str(exc), on_case)
                                pending.clear()
                                break
                            except RigError as exc:  # last resort: a refusal while pulling is "not delivered" (10.7)
                                res = self._not_delivered(e, rc, gid, "rig-error",
                                                          f"{gid} ({rc}) could not be pulled from the rig: {exc}",
                                                          job=job)
                            if on_case:
                                on_case(gid, res)
                    if not pending:
                        break
                    now = time.monotonic()
                    if now >= deadline:
                        self._budget_spent(e, pending, on_case)
                        break
                    if now - last_beacon >= BEACON_EVERY_S:
                        self._beacons(e, pending)
                        last_beacon = now
                    time.sleep(max(0.0, min(POLL_S, deadline - time.monotonic())))
            completed = True
        finally:
            if completed:  # an interrupted collection stays open, so cancel_all still cancels it
                e["_collected"] = True
                self._finish_exp(e)
            self.write_session()
        self.record_evidence(stage)
        return {gid: self.results[gid] for gid in e["cases"].values() if gid in self.results}

    def run_cases(self, cases: list[dict], kind: str, *, on_done: Callable[[dict, dict], None]) -> None:
        """--rig-sweep: submit the design cases as the sweep experiment and report each through on_done(case, outcome)
        as it comes back (golden.case.run_cases records them through its finish path)."""
        if kind != "design":
            raise ValueError("RigSession.run_cases runs the sweep's design cases only")
        by_id = {c["case_id"]: c for c in cases}
        self.submit("sweep", cases)
        self.collect("sweep", on_case=lambda gid, res: on_done(by_id[gid], res["outcome"]))

    def _state_line(self, e: dict, rc: str, gid: str, state: str, job: dict | None) -> None:
        meta = (job or {}).get("meta") or {}
        gpu = meta.get("gpu")
        if state == "waiting":
            detail = f"mode {meta.get('mode')}, pool {meta.get('pool')}, available {meta.get('available')}"
            line = f"rig: {gid} ({rc}) waiting for a GPU lease ({detail})"
        elif state == "running":
            detail = f"GPU {gpu}" if gpu is not None else "the rig CPU"
            line = f"rig: {gid} ({rc}) running on {detail}"
        elif state == "queued":
            detail, line = "queued", f"rig: {gid} ({rc}) queued"
        elif state in FINAL_STATES:
            detail, line = (job or {}).get("status"), None  # the pull reports the end
        else:
            detail, line = (job or {}).get("status"), f"rig: {gid} ({rc}) {state} (rq status {(job or {}).get('status')})"
        self._event(e, gid, state, gpu=gpu, detail=detail, line=line)

    def _beacons(self, e: dict, pending: dict) -> None:
        for rc, gid in pending.items():
            if e["_states"].get(rc) != "running":
                continue
            try:
                data, _sha, _n, problem = self.client.get_once(e["id"], f"{rc}/golden/{gid}/progress.json", retry=False)
            except RigError:
                continue
            if problem is None and data:
                with contextlib.suppress(OSError):
                    _atomic_write(self.run.path(gid, "progress.json"), data)

    def _budget_spent(self, e: dict, pending: dict, on_case) -> None:
        note = self._cancel(e, f"rig budget {self.budget_s:g} s spent")
        for rc in list(pending):
            gid = pending.pop(rc)
            state = e["_states"].get(rc) or "queued"
            res = self._not_delivered(e, rc, gid, "rig-timeout",
                                      f"the rig budget of {self.budget_s:g} s ran out while {gid} ({rc}) was {state}; "
                                      f"{note}", job=None)
            if on_case:
                on_case(gid, res)

    def _cancel(self, e: dict, reason: str) -> str:
        if not self.offers("sim_cancel"):
            note = (f"sim_cancel not offered (policy {self.policy}): jobs end at the server's lease wait of "
                    f"{e['lease_wait_s']} s")
            self.cancelled.append({"experiment": e["id"], "utc": utc_now(), "reason": reason, "sim_cancel": False,
                                   "reply": None, "note": note})
            self._event(e, None, "cancel-unavailable", detail=note, line=f"rig: {e['id']}: {note}")
            return note
        try:
            reply = self.client.cancel(e["id"])
            note = "cancelled through sim_cancel"
        except RigError as exc:
            reply = {"error": str(exc)}
            note = f"sim_cancel failed ({exc})"
        e["cancelled"] = True
        self.cancelled.append({"experiment": e["id"], "utc": utc_now(), "reason": reason, "sim_cancel": True,
                               "reply": reply, "note": note})
        self._event(e, None, "cancelled", detail=f"{reason}; {note}", line=f"rig: {e['id']} {reason}; {note}")
        return note

    def _lost_contact(self, e: dict, pending: dict, last_error: str, on_case) -> None:
        reason = (f"lost contact with simlab-mcp at {self.url} for {UNREACHABLE_S:g} s ({last_error}); jobs may still "
                  f"run on the rig: experiment {e['id']}")
        self._event(e, None, "unreachable", detail=reason, line=f"rig: {reason}")
        if self.offers("sim_cancel"):  # one best-effort cancel, no retries
            try:
                reply = self.client.cancel(e["id"])
            except RigError as exc:
                reply = {"error": str(exc)}
            self.cancelled.append({"experiment": e["id"], "utc": utc_now(), "reason": "lost contact",
                                   "sim_cancel": True, "reply": reply, "note": "best effort"})
            e["cancelled"] = True
        for rc in list(pending):
            gid = pending.pop(rc)
            res = self._not_delivered(e, rc, gid, "rig-unreachable", reason, job=None)
            if on_case:
                on_case(gid, res)

    def cancel_all(self, reason: str) -> None:
        """Ctrl-C or an error: one sim_cancel per experiment that is not collected yet, within CANCEL_BUDGET_S in all."""
        t_end = time.monotonic() + CANCEL_BUDGET_S
        for e in self._ordered():
            if e["_collected"] or not e.get("submitted_utc"):
                continue
            if time.monotonic() >= t_end:
                self.cancelled.append({"experiment": e["id"], "utc": utc_now(), "reason": reason, "sim_cancel": False,
                                       "reply": None, "note": f"not attempted: the {CANCEL_BUDGET_S:g} s cancel budget "
                                                              "was spent"})
                continue
            self._cancel(e, reason)
        self.write_session()

    # ---- pulling --------------------------------------------------------------------------------------------------
    def _get_optional(self, exp_id: str, path: str) -> tuple[bytes, int] | None:
        """A record that may be absent: None when the rig has no such file; RigIntegrityError when it does not verify."""
        try:
            data, sha, chunks, problem = self.client.get_once(exp_id, path)
        except RigUnreachable:
            raise
        except RigError:
            return None
        if problem is not None:  # pulled again once, then an integrity error naming the file
            data, sha, chunks, problem = self.client.get_once(exp_id, path)
            if problem is not None:
                raise RigIntegrityError(problem)
        return data, chunks

    def _pull_case(self, e: dict, rc: str, gid: str, job: dict | None, state: str) -> dict:
        exp_id = e["id"]
        records: dict[str, bytes] = {}
        pulled: list[tuple[dict, bytes, int, str]] = []
        outcome = files_doc = None
        integrity = pull_error = None
        try:
            got = self._get_optional(exp_id, f"{rc}/outcome.json")
            if got is not None:
                records["outcome.json"] = got[0]
                outcome = json.loads(got[0].decode("utf-8"))
                fd = self._get_optional(exp_id, f"{rc}/files.json")
                if fd is None:
                    raise RigIntegrityError(f"{rc}/files.json is missing next to {rc}/outcome.json")
                records["files.json"] = fd[0]
                files_doc = json.loads(fd[0].decode("utf-8"))
                prefix = f"golden/{gid}/"
                if files_doc.get("golden_case_id") not in (None, gid) or outcome.get("case_id") != gid:
                    raise RigIntegrityError(f"{rc} holds case {outcome.get('case_id')}, the session mapped it to {gid}")
                for entry in files_doc.get("files") or []:
                    p = str(entry.get("path") or "")
                    rel = p[len(prefix):] if p.startswith(prefix) else None
                    if not rel or not all(_NAME.match(part) for part in rel.split("/")):
                        raise RigIntegrityError(f"{rc}/files.json lists {p!r}, which is not a file under {prefix}")
                    try:
                        data, sha, chunks = self.client.get_file(exp_id, f"{rc}/{p}",
                                                                 expect_sha256=entry.get("sha256"))
                    except (RigUnreachable, RigIntegrityError):
                        raise
                    except RigError as exc:
                        # the rig refused a file its own files.json lists (gone, not a regular file, changed while
                        # read, an unknown experiment after a redis reset, a smaller chunk limit): the case is not
                        # delivered (section 10.7), never an internal error; the records below are still pulled
                        pull_error = f"{rc}/{p} could not be pulled: {exc}"
                        break
                    pulled.append((entry, data, chunks, rel))
            names = (("results.json",) if state == "finished" else ()) + ("evidence.json", "status.json", "case.json",
                                                                         "worker.log")
            for name in names:
                got = self._get_optional(exp_id, f"{rc}/{name}")
                if got is not None:
                    records[name] = got[0]
            if "case.json" in records:
                rig_case = json.loads(records["case.json"].decode("utf-8"))
                if rig_case.get("case_id") != rc or (rig_case.get("golden_case") or {}).get("case_id") != gid:
                    raise RigIntegrityError(f"{rc}/case.json is case {rig_case.get('case_id')} of golden case "
                                            f"{(rig_case.get('golden_case') or {}).get('case_id')}, the session mapped "
                                            f"{rc} to {gid}")
        except RigIntegrityError as exc:
            integrity = str(exc)
        except (ValueError, UnicodeDecodeError) as exc:
            integrity = f"{rc}: a pulled record is not valid JSON ({exc})"
        pulled_utc = utc_now()
        evidence = None
        if "evidence.json" in records:
            with contextlib.suppress(ValueError, UnicodeDecodeError):
                evidence = json.loads(records["evidence.json"].decode("utf-8"))
        rig_status = None
        if "status.json" in records:
            with contextlib.suppress(ValueError, UnicodeDecodeError):
                rig_status = json.loads(records["status.json"].decode("utf-8"))
        if integrity is not None:
            self.integrity_error = self.integrity_error or integrity
            return self._not_delivered(e, rc, gid, "rig-integrity", f"rig integrity: {integrity}", job=job,
                                       records=records, outcome=outcome, evidence=evidence, pulled_utc=pulled_utc)
        if pull_error is not None:
            return self._not_delivered(e, rc, gid, "rig-error", pull_error, job=job, records=records, outcome=outcome,
                                       evidence=evidence, pulled_utc=pulled_utc)
        if outcome is None:
            log = records.get("worker.log", b"").decode("utf-8", "replace")
            tail = "\n".join(log.strip().splitlines()[-LOG_TAIL_LINES:]) if log.strip() else ""
            jid = (job or {}).get("job_id") or e["jobs"].get(rc)
            if state == "cancelled":
                status, why = "rig-cancelled", f"the rig job {jid} was cancelled before {gid} wrote an outcome"
            elif state == "finished":
                status, why = "rig-error", f"the rig job {jid} finished without an outcome.json"
            else:
                status = "rig-error"
                why = f"the rig job {jid} {'was lost' if state == 'lost' else 'failed'} without an outcome.json"
                err = _last_line((job or {}).get("error"))
                if err:
                    why += f" ({err})"
            if tail:
                why += f"; worker.log ends:\n{tail}"
            return self._not_delivered(e, rc, gid, status, why, job=job, records=records, outcome=None,
                                       evidence=evidence, pulled_utc=pulled_utc)
        ost, dst = outcome.get("status"), str(outcome.get("design_status") or "error")
        if ost == "FAILED" and dst in RIG_STATUSES:
            if dst == "rig-integrity":
                self.integrity_error = self.integrity_error or f"{gid}: {_last_line(outcome.get('error'))}"
            return self._not_delivered(e, rc, gid, dst, str(outcome.get("error") or dst), job=job, records=records,
                                       outcome=outcome, evidence=evidence, pulled_utc=pulled_utc)
        if ost == "FAILED" and dst == "error":
            # design status "error" is the catch-all for an exception inside the case (case._run, rigside, or
            # conforming.run_check's own record): on the rig that is as likely an infrastructure fault (a CUDA
            # out-of-memory on the leased GPU, an image that cannot import cupy) as a code bug, so the case is not
            # delivered and falls back to this desktop (sections 10.9 and 13.3), where a code bug fails again and
            # grades as it would have. The real design and conforming statuses (unconverged, detached-load,
            # bc-mapping-failed, equilibrium, ...) stay results. A deviation from the 10.7 table (finding 3 of the
            # rig review).
            return self._not_delivered(e, rc, gid, "rig-error",
                                       f"the rig case {gid} stopped on an internal error: "
                                       f"{_last_line(outcome.get('error')) or 'no error text'}",
                                       job=job, records=records, outcome=outcome, evidence=evidence,
                                       pulled_utc=pulled_utc)
        if ost == "NOT_RUN" or ost not in ("FINISHED", "FAILED"):
            return self._not_delivered(e, rc, gid, "rig-error",
                                       f"the rig did not run {gid} ({ost} {dst}: {_last_line(outcome.get('error'))})",
                                       job=job, records=records, outcome=outcome, evidence=evidence,
                                       pulled_utc=pulled_utc)
        return self._record_result(e, rc, gid, job, outcome, records, pulled, evidence, rig_status, pulled_utc)

    def _local_artifacts(self, arts, gid: str) -> list[str]:
        out = []
        marker = f"/golden/{gid}/"
        for a in arts or []:
            s = str(a).replace("\\", "/")
            if marker in s:
                out.append(str(Path(self.run.root) / gid / Path(*s.split(marker, 1)[1].split("/"))))
            else:
                out.append(str(a))
        return out

    def _record_result(self, e: dict, rc: str, gid: str, job, outcome: dict, records: dict, pulled: list, evidence,
                       rig_status, pulled_utc: str) -> dict:
        run = self.run
        files = []
        for entry, data, chunks, rel in pulled:
            _atomic_write(run.path(gid, *rel.split("/")), data)
            files.append({"rig": f"{rc}/{entry['path']}", "local": f"{gid}/{rel}", "bytes": len(data),
                          "sha256": sha256_bytes(data), "chunks": chunks})
        for name, data in records.items():
            _atomic_write(run.path(gid, "rig", name), data)
        if "evidence.json" in records:
            _atomic_write(run.path(gid, "evidence.json"), records["evidence.json"])
        finished = outcome.get("status") == "FINISHED" and isinstance(outcome.get("results"), dict)
        local_outcome = dict(outcome)
        local_outcome.pop("schema", None)
        local_outcome.pop("rig", None)
        if finished:
            res = dict(outcome["results"])
            res["artifacts"] = self._local_artifacts(res.get("artifacts"), gid)
            dump_json(run.path(gid, "results.json"), res)
            local_outcome["results"] = res
        else:
            local_outcome["results"] = None
        dump_json(run.path(gid, "status.json"), {
            "status": outcome.get("status"), "design_status": outcome.get("design_status"),
            "mlflow_run_id": (rig_status or {}).get("mlflow_run_id"), "wall_s": outcome.get("wall_s"),
            "error": outcome.get("error"), "stages": [s.get("stage") for s in outcome.get("stages") or []],
            "diagnostics": outcome.get("diagnostics"),
            "rig": {"experiment_id": e["id"], "rig_case_id": rc, "job_id": (job or {}).get("job_id") or e["jobs"].get(rc),
                    "lane": self.lane}})
        rj = self._rig_json(e, rc, gid, job, "result", outcome.get("design_status"), None, outcome, evidence, files,
                            records, pulled_utc)
        dump_json(run.path(gid, "rig.json"), rj)
        size = sum(f["bytes"] for f in files)
        run_s = rj.get("run_s")
        t = f"{run_s:.1f} s" if isinstance(run_s, (int, float)) else "-"
        verb = "finished" if finished else f"failed ({outcome.get('design_status')})"
        self._event(e, gid, "result", gpu=rj.get("gpu"), detail=f"{outcome.get('status')} {outcome.get('design_status')}",
                    line=f"rig: {gid} ({rc}) {verb} in {t}; pulled {len(files)} files ({_mb(size)})")
        res = {"case_id": gid, "rig_case": rc, "job_id": rj.get("job_id"), "state": "result", "outcome": local_outcome,
               "design_status": outcome.get("design_status"), "reason": None, "rig": rj}
        self.results[gid] = res
        return res

    def _not_delivered(self, e: dict, rc: str, gid: str, status: str, reason: str, *, job, records: dict | None = None,
                       outcome: dict | None = None, evidence=None, pulled_utc: str | None = None) -> dict:
        run = self.run
        for name, data in (records or {}).items():
            with contextlib.suppress(OSError):
                _atomic_write(run.path(gid, "rig", name), data)
        if "evidence.json" in (records or {}):
            # <gid>/evidence.json is a byte copy of the rig's whenever it pulled cleanly (section 10.8), delivered or
            # not: the audit's R4 asks for it and the bundle takes the case's image and GPU facts from it
            with contextlib.suppress(OSError):
                _atomic_write(run.path(gid, "evidence.json"), records["evidence.json"])
        now = utc_now()
        case = e["_cases"].get(gid) or {"case_id": gid}
        oc = {"case_id": gid, "role": case.get("role"), "status": "FAILED", "design_status": status, "error": reason,
              "results": None, "diagnostics": {"generator": None, "skin": None, "checks": None},
              "started_utc": now, "finished_utc": now, "wall_s": 0.0, "stages": []}
        jid = (job or {}).get("job_id") or e["jobs"].get(rc)
        dump_json(run.path(gid, "status.json"), {
            "status": "FAILED", "design_status": status, "mlflow_run_id": None, "wall_s": None, "error": reason,
            "stages": [], "diagnostics": oc["diagnostics"],
            "rig": {"experiment_id": e["id"], "rig_case_id": rc, "job_id": jid, "lane": self.lane}})
        rj = self._rig_json(e, rc, gid, job, "not-run", status, reason, outcome, evidence, [], records or {},
                            pulled_utc)
        dump_json(run.path(gid, "rig.json"), rj)
        first = str(reason).splitlines()[0] if reason else status
        self._event(e, gid, "not-run", gpu=rj.get("gpu"), detail=f"{status}: {first}",
                    line=f"rig: {gid} ({rc}) not delivered: {status}: {first}")
        res = {"case_id": gid, "rig_case": rc, "job_id": jid, "state": "not-run", "outcome": oc, "design_status": status,
               "reason": reason, "rig": rj}
        self.results[gid] = res
        return res

    def _rig_json(self, e: dict, rc: str, gid: str, job, state: str, design_status, reason, outcome, evidence,
                  files: list, records: dict, pulled_utc) -> dict:
        env = (evidence or {}).get("environment") or {}
        gpus = env.get("gpus") or []
        g0 = gpus[0] if gpus and isinstance(gpus[0], dict) else {}
        orig = (outcome or {}).get("rig") or {}
        digest = env.get("image_digest") or orig.get("image_digest")
        meta = (job or {}).get("meta") or {}
        st = e.get("_status") or {}
        submitted = st.get("submitted_utc") or e.get("submitted_utc")
        started, finished = (evidence or {}).get("started_utc"), (evidence or {}).get("finished_utc")
        code = orig.get("code") if isinstance(orig.get("code"), dict) else None
        return clean({
            "schema": SCHEMA["rig-case"], "case_id": gid, "lane": self.lane, "url": self.url, "experiment_id": e["id"],
            "rig_case_id": rc, "job_id": (job or {}).get("job_id") or e["jobs"].get(rc),
            "job_status": (job or {}).get("status"), "state": state, "design_status": design_status, "reason": reason,
            "queue": st.get("queue") or e.get("queue"), "gpu": meta.get("gpu"), "owner": meta.get("owner"),
            "gpu_name": g0.get("name"), "gpu_uuid": g0.get("uuid"), "image": env.get("image") or orig.get("image"),
            "image_id": image_token(digest), "image_digest": digest, "submitted_utc": submitted, "started_utc": started,
            "finished_utc": finished, "wait_s": _seconds(submitted, started), "run_s": (evidence or {}).get("wall_s"),
            "pulled_utc": pulled_utc, "files": files,
            "records": {name: (sha256_bytes(records[name]) if name in records else None) for name in RECORDS},
            "code": ({"golden_compute_sha256": code.get("golden_compute_sha256"), "modules": code.get("modules") or {}}
                     if code else None),
            "device_forced": orig.get("device_forced")})

    def _finish_exp(self, e: dict) -> None:
        e["finished_utc"] = utc_now()
        e["wall_s"] = round(time.monotonic() - e["_t_submit"], 3)
        rs = [self.results[g] for g in e["cases"].values() if g in self.results]
        fin = sum(1 for r in rs if (r.get("outcome") or {}).get("status") == "FINISHED")
        dl = sum(1 for r in rs if r["state"] == "result")
        n = len(e["cases"])
        e["counts"] = {"cases": n, "finished": fin, "failed": n - fin, "delivered": dl, "not_delivered": n - dl}

    # ---- the rig ledger cross-reference ----------------------------------------------------------------------------
    def record_evidence(self, stage: str | None = None) -> None:
        """sim_evidence for every collected experiment (once), when the tool is offered; never fatal."""
        from simlab.evidence_bundle import canonical_sha256

        for e in self._ordered():
            if stage is not None and e["stage"] != stage:
                continue
            if e["_evidence_done"] or not e["_collected"] or not e.get("submitted_utc"):
                continue
            e["_evidence_done"] = True
            if not self.offers("sim_evidence"):
                e["rig_evidence"] = {"error": f"sim_evidence not offered (policy {self.policy})"}
                continue
            note = f"aces golden {self.run.run_id} {e['stage']} from {self.host}"
            try:
                res = self.client.call("sim_evidence", {"experiment_id": e["id"], "note": note}, retry=False) or {}
                entry = res.get("ledger_entry") if isinstance(res, dict) else None
                if not isinstance(entry, dict):
                    raise RigError("sim_evidence returned no ledger_entry")
                e["rig_evidence"] = {"bundle_sha256": entry.get("sha256"), "ledger_entry_sha256": canonical_sha256(entry)}
            except RigError as exc:
                e["rig_evidence"] = {"error": str(exc)}
        self.write_session()

    def close(self) -> None:
        self.finished_utc = utc_now()
        self.wall_s = round(time.monotonic() - self._t0, 3)
        self.closed = True
        self.write_session()

    # ---- what the verifier reads ----------------------------------------------------------------------------------
    def result(self, gid: str) -> dict | None:
        return self.results.get(gid)

    def level_block(self, gid: str, solver: str = "golden-check") -> dict | None:
        """validation.json independent_check.levels[].rig for a delivered rig case (None otherwise)."""
        r = self.results.get(gid)
        if not r or r["state"] != "result":
            return None
        rj, sv = r["rig"], self.solver_row(solver)
        return {"lane": self.lane, "experiment": rj.get("experiment_id"), "rig_case": rj.get("rig_case_id"),
                "job_id": rj.get("job_id"), "gpu": rj.get("gpu"), "gpu_name": rj.get("gpu_name"),
                "gpu_uuid": rj.get("gpu_uuid"), "image": rj.get("image"), "image_id": rj.get("image_id"),
                "solver": solver, "solver_validation": sv.get("validation"),
                "validation_image_id": sv.get("validation_image_id"), "wait_s": rj.get("wait_s"),
                "run_s": rj.get("run_s")}

    def conforming_block(self, gid: str) -> dict | None:
        r = self.results.get(gid)
        if not r or r["state"] != "result":
            return None
        rj, sv = r["rig"], self.solver_row("ccx-check")
        return {"experiment": rj.get("experiment_id"), "rig_case": rj.get("rig_case_id"), "job_id": rj.get("job_id"),
                "image": rj.get("image"), "image_id": rj.get("image_id"), "solver_validation": sv.get("validation"),
                "validation_image_id": sv.get("validation_image_id")}

    def validation_block(self) -> dict:
        """validation.json "rig" (section 14.3); rig/session.json must be final (close() first)."""
        p = self.run.path("rig", "session.json")
        codes = []
        for r in self.results.values():
            code = (r.get("rig") or {}).get("code")
            if r.get("state") == "result" and code:
                codes.append(code)
        return clean({
            "lane": self.lane, "url": self.url, "scope": self.scope, "token_source": self.probe.get("token_source"),
            "principal": self.principal, "policy": self.policy, "server": self.probe.get("server"),
            "session": "rig/session.json", "session_sha256": sha256_file(p) if Path(p).is_file() else None,
            "solvers": self.probe.get("solvers"), "experiments": experiments_summary(self.session_doc()),
            "code_identity": code_identity(codes), "integrity_error": self.integrity_error})


def experiments_summary(session: dict) -> list[dict]:
    out = []
    for e in (session or {}).get("experiments") or []:
        c = e.get("counts") or {}
        out.append({"id": e.get("id"), "stage": e.get("stage"), "solver": e.get("solver"), "n_cases": c.get("cases"),
                    "n_finished": c.get("finished"), "n_failed": c.get("failed")})
    return out


# ---------------------------------------------------------------------------------------------------- code identity

def desktop_package_dirs() -> dict:
    out = {}
    for top in ("simlab", "gendesign"):
        try:
            mod = __import__(top)
        except Exception:
            continue
        if getattr(mod, "__file__", None):
            out[top] = Path(mod.__file__).resolve().parent
    return out


def code_identity(codes: list[dict], *, desktop_compute: str | None = None, desktop_modules: dict | None = None) -> dict:
    """validation.rig.code_identity (section 13.1): ok when every rig outcome ran this desktop's golden compute digest
    and every simlab/ and gendesign/ module it imported hashes (LF) like the file at the same path under this
    desktop's imported packages (a missing file is a mismatch). desktop_modules records the desktop digests used, so
    the audit can re-derive ok from the pulled outcomes alone. Pass desktop_compute/desktop_modules to re-derive."""
    from .evidence import _lf_sha256, compute_digest

    if desktop_compute is None:
        desktop_compute = compute_digest()["golden_compute_sha256"]
    fixed = desktop_modules is not None
    desk: dict = dict(desktop_modules or {})
    dirs = {} if fixed else desktop_package_dirs()
    mismatches: list[dict] = []
    rig_vals = sorted({str(c.get("golden_compute_sha256")) for c in codes if c})
    for v in rig_vals:
        if v != desktop_compute:
            mismatches.append({"path": "golden_compute_sha256", "desktop": desktop_compute, "rig": v})
    seen: set = set()
    for c in codes:
        for path, sha in sorted((c.get("modules") or {}).items()):
            if path not in desk and not fixed:
                top, _, rel = str(path).partition("/")
                base = dirs.get(top)
                local = base.joinpath(*rel.split("/")) if base is not None and rel else None
                try:
                    desk[path] = _lf_sha256(local) if local is not None and local.is_file() else None
                except OSError:
                    desk[path] = None
            if desk.get(path) != sha and (path, sha) not in seen:
                seen.add((path, sha))
                mismatches.append({"path": path, "desktop": desk.get(path), "rig": sha})
    checked = sorted({p for c in codes for p in (c.get("modules") or {})})
    return {"ok": bool(codes) and not mismatches,
            "golden_compute_sha256": {"desktop": desktop_compute, "rig": rig_vals},
            "modules_checked": len(checked), "mismatches": mismatches,
            "desktop_modules": {p: desk.get(p) for p in checked}}
