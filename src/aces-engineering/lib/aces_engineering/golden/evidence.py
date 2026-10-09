"""Evidence of aces golden: the run directory as a simlab experiment, anchored, bundled and chained into two ledgers.

A golden run directory is a valid simlab experiment directory (spec.resolved.json from `experiment()`, cases.json, a
results/status/evidence triple per case), so the vendored `simlab.evidence_bundle` bundles it unchanged. `record()`
follows the pinned order of section 12.2: both ledgers must verify before anything is appended; `anchor()` hashes every
anchored file into evidence/files.sha256; the bundle (plus a `golden` section that carries the anchor's digest, the
validation record, the compute tables and the solver digest, all inside bundle_sha256) is chained into the per-user
simlab ledger under a file lock and verified; a `golden.run` entry pointing at the bundle goes into the aces (OS)
ledger and is verified; the project's aces.yaml gets the evidence reference and the components used.

`audit()` is `aces golden verify`: eleven independent checks that the run directory, the bundle and both ledgers still
say the same thing (section 12.3). Any edit to an anchored file, the bundle or its golden section fails it.
"""
from __future__ import annotations

import contextlib
import csv
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

from . import (EXIT_EVIDENCE, GRADE_PREVIEW, LEDGER_KIND, SCHEMA, VERSION, GoldenError, canonical_json, clean,
               sha256_bytes, sha256_file, stage_wall_sum)

ANCHOR_REL = "evidence/files.sha256"
UNANCHORED_TOP = frozenset({"run.json", "result.json", "log.txt", "events.jsonl", "manifest.sha256", "report.md",
                            "report.html", "report.json"})
COMPONENTS = ("aces-engineering", "gendesign", "simlab", "build123d", "cadquery-ocp")
LOCK_TIMEOUT_S = 30.0
PAYLOAD_MAX = 4096
EXPECTED = "the surrogate's winner meets every constraint on the real solver and agrees within tol"
DIGEST_MODULES = ("simlab.spec", "simlab.evidence", "simlab.evidence_bundle", "simlab.report", "simlab.post",
                  "simlab.registry", "simlab.solvers.step_structures", "simlab.surrogate.tabular", "gendesign.fea3d",
                  "gendesign.evaluators", "gendesign.export", "gendesign.filters", "gendesign.topopt3d",
                  "gendesign.progress")


def _lf_sha256(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes().replace(b"\r\n", b"\n"))


def _first_problem(res: dict) -> str:
    probs = res.get("problems") or []
    if not probs:
        return "no detail"
    p = probs[0]
    where = f" at line {p['line']}" if isinstance(p, dict) and p.get("line") is not None else ""
    return (p.get("problem") if isinstance(p, dict) else str(p)) + where


# ----------------------------------------------------------------------------- snapshot and solver digest

def snapshot_path() -> Path:
    """SNAPSHOT next to the installed lib (/opt/aces/engineering/SNAPSHOT) or in the source tree."""
    import aces_engineering

    return Path(aces_engineering.LIB).parent / "SNAPSHOT"


def parse_snapshot(path: Path) -> dict:
    info: dict = {"source_commit": None, "content_sha256": None, "manifest": {}}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return info
    for line in text.splitlines():
        if line.startswith("source_commit:"):
            rest = line.split(":", 1)[1].strip()
            info["source_commit"] = rest.split()[0] if rest else None
        elif line.startswith("content_sha256:"):
            info["content_sha256"] = line.split(":", 1)[1].strip() or None
        else:
            m = re.match(r"^([0-9a-f]{64}) \*(.+)$", line.strip())
            if m:
                info["manifest"][m.group(2)] = m.group(1)
    return info


def golden_code() -> dict:
    """sha256 over the sorted lines '<relpath> <sha256 of LF-normalised bytes>' of golden/*.py and golden_cli.py
    (relpaths relative to the aces_engineering package; the lines joined with LF plus a final LF)."""
    pkg = Path(__file__).resolve().parent
    lib_pkg = pkg.parent
    files = sorted(pkg.glob("*.py"))
    if (lib_pkg / "golden_cli.py").is_file():
        files.append(lib_pkg / "golden_cli.py")
    lines = sorted(f"{p.relative_to(lib_pkg).as_posix()} {_lf_sha256(p)}" for p in files)
    return {"golden_code_sha256": sha256_bytes(("\n".join(lines) + "\n").encode("utf-8")),
            "golden_code_files": [ln.split(" ", 1)[0] for ln in lines]}


def solver_digest() -> dict:
    """What solved this run: the vendored SNAPSHOT (commit, content digest), where simlab and gendesign were actually
    imported from, and whether every imported simlab.*/gendesign.* module file (LF-normalised) hashes to its SNAPSHOT
    manifest line. On the workstation, where they come from a cudax-sim-runtime checkout, the answer may be either."""
    import importlib

    snap_path = snapshot_path()
    snap = parse_snapshot(snap_path)
    out: dict = {"source_commit": snap.get("source_commit"), "content_sha256": snap.get("content_sha256"),
                 "snapshot_path": str(snap_path), "snapshot_present": snap_path.is_file(),
                 "simlab_imported_from": None, "gendesign_imported_from": None, "matches_snapshot": False,
                 "mismatches": [], "modules_checked": 0}
    for name in DIGEST_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # a module that cannot import is reported, not fatal
            out["mismatches"].append({"module": name, "problem": f"import failed: {type(exc).__name__}"})
    tops = {}
    for top in ("simlab", "gendesign"):
        mod = sys.modules.get(top)
        if mod is not None and getattr(mod, "__file__", None):
            tops[top] = Path(mod.__file__).resolve().parent
            out[f"{top}_imported_from"] = str(tops[top])
    manifest = snap.get("manifest") or {}
    checked = 0
    for name in sorted(list(sys.modules)):
        top = name.split(".")[0]
        if top not in tops:
            continue
        mod = sys.modules.get(name)
        f = getattr(mod, "__file__", None)
        if not f or not str(f).endswith(".py"):
            continue
        p = Path(f).resolve()
        try:
            rel = f"{top}/{p.relative_to(tops[top]).as_posix()}"
        except ValueError:
            out["mismatches"].append({"module": name, "problem": f"imported from outside {tops[top]}"})
            continue
        checked += 1
        want = manifest.get(rel)
        got = _lf_sha256(p)
        if want is None:
            out["mismatches"].append({"module": rel, "problem": "not in SNAPSHOT"})
        elif want != got:
            out["mismatches"].append({"module": rel, "problem": "content differs", "sha256": got, "snapshot": want})
    out["modules_checked"] = checked
    out["matches_snapshot"] = bool(manifest) and checked > 0 and not out["mismatches"]
    out.update(golden_code())
    return out


# ----------------------------------------------------------------------------- experiment

def _gpu_planned(plan: dict) -> bool:
    if ((plan or {}).get("sweep") or {}).get("device") == "gpu":
        return True
    return any(p.get("device") == "gpu" for p in (plan or {}).get("parity") or [])


def experiment(eff: dict, run, project: dict, operator: str, *, plan: dict, detected: dict):
    """The simlab Experiment this run is (spec.resolved.json): what makes the run directory bundle-able."""
    from simlab.spec import Experiment

    d = eff.get("derived") or {}
    project = project or {}
    gen = eff.get("generator") or {}
    spec_path = Path(run.path("inputs", "spec.effective.json"))
    dom = run.read_json("inputs/domain.json") or {}
    step_sha = dom.get("step_sha256")
    if not step_sha and Path(run.path("inputs", "domain.step")).is_file():
        step_sha = sha256_file(run.path("inputs", "domain.step"))
    gpu = (detected or {}).get("gpu") or {}
    vram = round(float(gpu.get("mem_total_mib") or 0) / 1024.0, 1) if _gpu_planned(plan) else 0.0
    sweep_plan = (plan or {}).get("sweep") or {}
    threads = int(sweep_plan.get("jobs") or 1) if sweep_plan.get("device", "cpu") == "cpu" else 1
    commit = parse_snapshot(snapshot_path()).get("source_commit") or "unknown"
    params = {
        "generator": {"kind": gen.get("kind"), "fixed": d.get("fixed"), "iterations": gen.get("iterations"),
                      "change_tol": gen.get("change_tol"), "move": gen.get("move"), "solver_tol": gen.get("solver_tol"),
                      "skin": gen.get("skin"), "infill": gen.get("infill")},
        "material": eff.get("material"),
        "resolution": {k: d.get(k) for k in ("gen_mm", "fea_mm", "field_h_mm", "check_factor", "check_levels",
                                             "stress_exclude_mm", "patch_keep_in_mm")},
        "grid": d.get("grid"),
        "supports": eff.get("supports"), "loads": eff.get("loads"),
        "constraints": d.get("constraints"), "objective": d.get("objective"), "tol": d.get("tol"),
        "sweep": {"n": d.get("n"), "seed": d.get("seed"), "sampler": "lhs-random-cd"},
        "solver": eff.get("solver"),
    }
    desc = f"aces golden {eff.get('name')}: {eff.get('description') or ''}".strip().rstrip(":")
    return Experiment(
        id=run.exp_id, project=run.project, owner=operator or "unknown", physics="structures", solver="step-structures",
        description=desc,
        geometry={"source": "step", "ref": "domain.step", "sha256": step_sha},
        params=clean(params),
        sweep={k: [float(v[0]), float(v[1])] for k, v in (d.get("variables") or {}).items()},
        outputs=list(d.get("outputs") or []),
        resources={"precision": "float64", "cpu_threads": threads, "vram_gb": vram, "wall_h": 0.05},
        hypothesis=eff.get("hypothesis") or project.get("hypothesis") or None,
        expected=EXPECTED,
        rd_activity=project.get("rd_activity") or None,
        company=project.get("company") or None,
        tags={"golden": VERSION, "generator": "simp-organic+gyroid" if gen.get("infill") else "simp-organic",
              "domain_source": str((eff.get("domain") or {}).get("source")), "sampler": "lhs-random-cd",
              "grade": GRADE_PREVIEW, "vendored_snapshot": str(commit)[:12]},
        spec_path=str(spec_path),
        spec_sha256=sha256_file(spec_path) if spec_path.is_file() else None,
    )


# ----------------------------------------------------------------------------- anchor

def anchored_files(root: Path) -> list[str]:
    """Run-relative POSIX paths of every anchored file (section 7): everything except run.json, result.json, log.txt,
    events.jsonl, manifest.sha256 and report.* at the top, everything under evidence/, every progress.json, every
    *.tmp. Sorted."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == "evidence" or rel_dir.startswith("evidence/"):
            dirnames[:] = []
            continue
        dirnames.sort()
        for fn in filenames:
            if rel_dir == "." and fn in UNANCHORED_TOP:
                continue
            if fn == "progress.json" or fn.endswith(".tmp"):
                continue
            out.append(fn if rel_dir == "." else f"{rel_dir}/{fn}")
    return sorted(out)


def anchor(run) -> str:
    """Write evidence/files.sha256 (sha256sum binary format, sorted by path, LF) and return its own sha256."""
    root = Path(run.root)
    lines = [f"{sha256_file(root / rel)} *{rel}\n" for rel in anchored_files(root)]
    path = run.write_text(ANCHOR_REL, "".join(lines))
    return sha256_file(path)


def parse_sums(path: Path) -> list[tuple[str, str]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([0-9a-f]{64}) [ *](.+)$", line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# ----------------------------------------------------------------------------- record

@contextlib.contextmanager
def ledger_lock(root: Path, timeout_s: float = LOCK_TIMEOUT_S):
    """Exclusive lock on <root>/ledger.lock (fcntl.flock on POSIX, msvcrt.locking of byte 0 on Windows), retried for
    up to `timeout_s`. Golden runs take it around build_bundle + write_bundle; `aces-evidence build` does not."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "ledger.lock"
    fh = open(path, "a+b")
    locked = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise GoldenError(EXIT_EVIDENCE, f"could not lock {path} within {timeout_s:g} s: another run is "
                                      "writing the simlab ledger", hint="retry when it has finished") from None
                time.sleep(0.2)
        yield path
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _argv(run) -> list:
    tool = (run.read_json("run.json") or {}).get("tool") or {}
    return list(tool.get("argv") or sys.argv)


def _winner_record(run) -> dict | None:
    for label in ("winner", "candidate"):
        w = run.read_json(f"{label}/{label}.json")
        if isinstance(w, dict):
            return w
    return None


def _compute_used_text(run, plan: dict | None) -> list[str]:
    """What actually solved: the CPU pool always; a GPU and Warp only when a solve record says so."""
    jobs = ((plan or {}).get("sweep") or {}).get("jobs") or 1
    used = [f"cpu/numpy x{jobs}"]
    gpu_names, warp = [], False
    cases = []
    try:
        cases = run.cases() or []
    except Exception:
        cases = run.read_json("cases.json", []) or []
    for c in cases:
        s = run.read_json(f"{c.get('case_id')}/solve.json") or {}
        dev = s.get("device")
        if dev and dev != "cpu/numpy" and not str(dev).startswith("cpu"):
            if c.get("backend") == "warp":
                warp = True
            elif dev not in gpu_names:
                gpu_names.append(str(dev))
    used += [f"{n} (cupy)" for n in gpu_names]
    if warp:
        comp = run.read_json("compute.json") or {}
        used.append(f"warp {((comp.get('warp') or {}).get('version')) or '?'} apply_K")
    return used


def golden_section(run, eff: dict, files_sha256: str, *, verdict: str, outcome: str) -> dict:
    """The bundle's `golden` section (aces-golden-bundle/1), built from the records on disk."""
    d = eff.get("derived") or {}
    validation = run.read_json("validation.json") or {}
    comp = run.read_json("compute.json") or {}
    used = (run.read_json("compute_used.json") or {}).get("rows") or []
    sweep = run.read_json("sweep.json") or {}
    dom_in = run.read_json("inputs/domain.json") or {}
    dom = run.read_json("domain/domain.json") or {}
    surr, opts = [], []
    k = 1
    while True:
        cv = run.read_json(f"surrogate/round-{k}/cv.json")
        op = run.read_json(f"optimise/round-{k}.json")
        if cv is None and op is None:
            break
        if cv is not None:
            surr.append(cv)
        if op is not None:
            opts.append({key: op.get(key) for key in ("round", "method", "n_candidates", "n_feasible", "from_feasible",
                                                      "winner")})
        k += 1
    w = _winner_record(run)
    winner = None
    if w:
        winner = {"label": w.get("label"), "case_id": w.get("case_id"), "params": w.get("params"),
                  "stl_sha256": (w.get("stl") or {}).get("sha256"), "metrics": w.get("metrics")}

    def fsha(rel: str):
        p = Path(run.path(*rel.split("/")))
        return sha256_file(p) if p.is_file() else None

    return clean({
        "schema": SCHEMA["bundle"], "run_id": run.run_id, "project": run.project, "run_dir": str(run.root),
        "tool": {"name": "aces-golden", "version": VERSION, "argv": _argv(run)}, "seed": d.get("seed"),
        "inputs": {"spec": {"path": "inputs/spec.json", "sha256": fsha("inputs/spec.json")},
                   "spec_effective_sha256": fsha("inputs/spec.effective.json"),
                   "domain": {"source": (eff.get("domain") or {}).get("source"), "ref": d.get("domain_ref"),
                              "step": "inputs/domain.step",
                              "step_sha256": dom_in.get("step_sha256") or fsha("inputs/domain.step"),
                              "mask_sha256": dom.get("mask_sha256")}},
        "resolution": {k2: d.get(k2) for k2 in ("gen_mm", "fea_mm", "field_h_mm", "check_factor", "check_levels",
                                                "stress_exclude_mm")},
        "sweep": {"n": sweep.get("n"), "n_ok": sweep.get("n_ok"), "sampler": sweep.get("sampler"),
                  "variables": sweep.get("variables"), "csv_sha256": sweep.get("csv_sha256"),
                  "json_sha256": fsha("sweep.json")},
        "surrogate": surr, "optimise": opts, "validation": validation,
        "compute_detected": comp, "compute_used": used, "solver_digest": solver_digest(), "winner": winner,
        "grade": validation.get("grade") or GRADE_PREVIEW, "preview_grade": True,
        "verdict": verdict or validation.get("verdict"), "outcome": outcome or validation.get("outcome"),
        "files_sha256": files_sha256,
    })


def record(run, eff: dict, project: dict, aces, *, operator: str, note: str | None, verdict: str,
           outcome: str) -> dict:
    """Anchor the run, chain its bundle into the simlab ledger, record golden.run in the aces ledger, update the
    project. Returns evidence/ledgers.json; raises GoldenError(EXIT_EVIDENCE) on any ledger failure (with nothing
    appended when a ledger was already broken; with the replay command when only the aces record failed)."""
    import aces_engineering
    import simlab.evidence_bundle as eb

    from aces_engineering.evidence_cli import verify_result

    from . import report as report_mod

    if not os.environ.get("SIMLAB_EVIDENCE_ROOT"):
        aces_engineering.prepare_env()
    root = os.path.abspath(os.environ["SIMLAB_EVIDENCE_ROOT"])
    project = project or {}
    pname = project.get("name") or run.project
    if aces is None:
        raise GoldenError(EXIT_EVIDENCE, "the aces command is not available to record golden.run: nothing was "
                          "appended", hint="install aces-base >= 0.1.0-3, or set ACES_CMD", stage="evidence")
    # 1. both ledgers must verify before anything is appended
    try:
        pre = verify_result(root)
    except Exception as exc:  # an unparsable ledger line
        pre = {"entries": -1, "ok": False, "problems": [{"problem": f"unreadable ({type(exc).__name__}: {exc})"}]}
    if pre.get("entries") and not pre.get("ok"):
        raise GoldenError(EXIT_EVIDENCE, f"the simlab ledger does not verify before this run ({_first_problem(pre)}): "
                          "nothing was appended", hint=f"aces-evidence verify {root}", stage="evidence")
    try:
        av = aces.ledger_verify()
    except Exception as exc:
        raise GoldenError(EXIT_EVIDENCE, f"the aces ledger does not verify before this run ({exc}): nothing was "
                          "appended", hint="aces ledger verify", stage="evidence") from None
    if not av.get("ok"):
        raise GoldenError(EXIT_EVIDENCE, f"the aces ledger does not verify before this run ({av.get('message')}): "
                          "nothing was appended", hint="aces ledger verify", stage="evidence")
    # 2. anchor
    try:
        files_sha = anchor(run)
    except OSError as exc:
        raise GoldenError(EXIT_EVIDENCE, f"could not anchor the run's files ({exc}): nothing was appended",
                          stage="evidence") from None
    # 3. bundle under the lock
    vline = report_mod.verdict_line(report_mod.assemble(run), evidence=False)
    full_note = vline + (f" · {note}" if note else "")
    company = project.get("company") or None
    golden = golden_section(run, eff, files_sha, verdict=verdict, outcome=outcome)
    with ledger_lock(Path(root)):
        eb.EVIDENCE_ROOT = root
        try:
            bundle = eb.build_bundle(str(run.root), operator, company, note=full_note)
        except Exception as exc:  # not a simlab experiment directory, an unreadable record
            raise GoldenError(EXIT_EVIDENCE, f"the simlab bundle could not be built ({type(exc).__name__}: {exc}): "
                              "nothing was appended", stage="evidence") from None
        bundle["golden"] = golden
        try:
            entry = eb.write_bundle(bundle)
        except Exception as exc:
            raise GoldenError(EXIT_EVIDENCE, f"writing the simlab bundle failed ({type(exc).__name__}: {exc}); the "
                              "ledger may hold a partial entry", hint=f"aces-evidence verify {root}",
                              stage="evidence") from None
        entries = eb.read_ledger()
    sha = entry["sha256"]
    position = next((i + 1 for i in range(len(entries) - 1, -1, -1) if entries[i].get("sha256") == sha), len(entries))
    # 4. verify the chain with the new head
    post = verify_result(root)
    simlab_doc = {"root": root, "path": entry["path"], "record": entry.get("record"),
                  "ledger": os.path.join(root, "ledger.jsonl"), "entry": position, "entries": post.get("entries"),
                  "sha256": sha, "previous": entry.get("previous"),
                  "verify": {"ok": bool(post.get("ok")), "problems": post.get("problems") or [],
                             "relocated": post.get("relocated", 0), "head": post.get("head")}}
    ledgers = {"schema": SCHEMA["ledgers"], "simlab": simlab_doc, "aces": None, "project": None}
    if not post.get("ok") or post.get("head") != sha:
        run.write_json("evidence/ledgers.json", ledgers)
        raise GoldenError(EXIT_EVIDENCE, f"the simlab ledger does not verify after appending bundle {sha[:12]} "
                          f"({_first_problem(post) if not post.get('ok') else 'its head is another bundle'})",
                          hint=f"aces-evidence verify {root}", stage="evidence")
    # 5. copies of the bundle and its contemporaneous record
    shutil.copyfile(entry["path"], run.path("evidence", "bundle.json"))
    if entry.get("record") and os.path.isfile(entry["record"]):
        shutil.copyfile(entry["record"], run.path("evidence", "record.md"))
    # 6. the aces ledger entry
    validation = run.read_json("validation.json") or {}
    w = _winner_record(run)
    comp = run.read_json("compute.json") or {}
    payload = clean({
        "schema": SCHEMA["ledger"], "project": run.project, "run_id": run.run_id, "run_dir": str(run.root),
        "host": socket.gethostname(), "verdict": verdict, "outcome": outcome,
        "grade": validation.get("grade") or GRADE_PREVIEW, "bundle_sha256": sha, "files_sha256": files_sha,
        "simlab_ledger": {"path": os.path.join(root, "ledger.jsonl"), "entry": position, "sha256": sha,
                          "previous": entry.get("previous")},
        "spec_sha256": golden["inputs"]["spec_effective_sha256"],
        "solver_digest": golden["solver_digest"].get("content_sha256"),
        "golden_code_sha256": golden["solver_digest"].get("golden_code_sha256"),
        "winner": {"params": w.get("params")} if w else None,
        "compute": _compute_used_text(run, comp.get("plan")),
        "tool": f"aces-golden {VERSION}",
    })
    if len(canonical_json(payload).encode("utf-8")) > PAYLOAD_MAX:
        payload["run_dir"] = os.path.basename(str(run.root))
    payload_path = run.write_json("evidence/aces-ledger-payload.json", payload)
    replay = f"aces ledger record golden.run --json {payload_path}"
    try:
        rec = aces.ledger_record(LEDGER_KIND, Path(payload_path))
        av2 = aces.ledger_verify()
    except Exception as exc:
        ledgers["aces"] = {"path": None, "n": None, "entries": None, "hash": None, "prev": None,
                           "verify": {"ok": False, "message": str(exc)}, "error": str(exc), "replay": replay}
        run.write_json("evidence/ledgers.json", ledgers)
        raise GoldenError(EXIT_EVIDENCE, f"the aces ledger record failed ({exc}); the simlab bundle {sha[:12]} is "
                          f"recorded as entry {position}", hint=f"replay with: {replay}", stage="evidence") from None
    ledgers["aces"] = {"path": rec.get("path"), "n": rec.get("n"), "entries": av2.get("entries"), "hash": rec.get("hash"),
                       "prev": rec.get("prev"), "verify": {"ok": bool(av2.get("ok")), "message": av2.get("message")}}
    if not av2.get("ok"):
        ledgers["aces"]["replay"] = replay
        run.write_json("evidence/ledgers.json", ledgers)
        raise GoldenError(EXIT_EVIDENCE, f"the aces ledger does not verify after recording golden.run "
                          f"({av2.get('message')})", hint="aces ledger verify", stage="evidence")
    # 7. the project workspace (warnings only)
    messages = []
    ref = f"golden/{run.run_id} bundle:{sha[:16]} {verdict}"
    try:
        aces.add_evidence(pname, ref)
    except Exception as exc:
        messages.append(f"add-evidence failed: {exc}")
    comps = list(COMPONENTS)
    used = payload.get("compute") or []
    if any(u.endswith("(cupy)") for u in used):
        comps.append("cupy")
    if any(u.startswith("warp ") for u in used):
        comps.append("warp-lang")
    added = []
    for c in comps:
        try:
            aces.add_component(pname, c)
            added.append(c)
        except Exception as exc:
            messages.append(f"add-component {c} failed: {exc}")
    ledgers["project"] = {"name": pname, "evidence_ref": ref, "components": added, "messages": messages}
    # 8.
    run.write_json("evidence/ledgers.json", clean(ledgers))
    return clean(ledgers)


# ----------------------------------------------------------------------------- audit

def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def resolve_key(obj, key: str):
    """Value at a dotted key ("a.b.0.c"); 'sum:stages.wall_s:name1,name2' sums wall_s over the named run.json stages."""
    if key.startswith("sum:stages.wall_s:"):
        names = [n for n in key.split(":", 2)[2].split(",") if n]
        return stage_wall_sum((obj or {}).get("stages"), names)  # the report's own helper: equal bit for bit
    cur = obj
    for part in key.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise KeyError(key) from None
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(key)
            cur = cur[part]
        else:
            raise KeyError(key)
    return cur


def resolve_pointer(doc, pointer: str):
    """RFC 6901 JSON pointer."""
    cur = doc
    if pointer in ("", "/"):
        return cur
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _check_files(run) -> tuple[bool, str]:
    p = Path(run.path("evidence", "files.sha256"))
    if not p.is_file():
        return False, "no evidence/files.sha256 (the run was never anchored)"
    bad = []
    sums = parse_sums(p)
    for want, rel in sums:
        f = Path(run.root) / rel
        if not f.is_file():
            bad.append(f"{rel} missing")
        elif sha256_file(f) != want:
            bad.append(f"{rel} changed")
    if bad:
        return False, f"{len(bad)} of {len(sums)} anchored files differ: " + "; ".join(bad[:8])
    return True, f"{len(sums)} anchored files match"


def _check_bundle(run) -> tuple[bool, str]:
    from simlab.evidence_bundle import canonical_sha256

    b = run.read_json("evidence/bundle.json")
    led = run.read_json("evidence/ledgers.json") or {}
    if not isinstance(b, dict):
        return False, "no evidence/bundle.json"
    problems = []
    self_hash = canonical_sha256({k: v for k, v in b.items() if k != "bundle_sha256"})
    if self_hash != b.get("bundle_sha256"):
        problems.append(f"bundle self-hash {self_hash[:12]} != bundle_sha256 {str(b.get('bundle_sha256'))[:12]}")
    if b.get("bundle_sha256") != (led.get("simlab") or {}).get("sha256"):
        problems.append("bundle_sha256 differs from ledgers.json simlab.sha256")
    anchor_file = Path(run.path("evidence", "files.sha256"))
    got = sha256_file(anchor_file) if anchor_file.is_file() else None
    if (b.get("golden") or {}).get("files_sha256") != got:
        problems.append("golden.files_sha256 differs from sha256(evidence/files.sha256)")
    src = (led.get("simlab") or {}).get("path")
    note = ""
    if src and os.path.isfile(src):
        if Path(src).read_bytes() != Path(run.path("evidence", "bundle.json")).read_bytes():
            problems.append(f"evidence/bundle.json differs from {src}")
        else:
            note = "; the copy equals the bundle in the simlab root"
    else:
        note = "; the simlab-root bundle is not on this machine (copy not compared)"
    if problems:
        return False, "; ".join(problems)
    return True, f"bundle {self_hash[:12]} hashes to itself, matches the ledger record and the anchor{note}"


def _evidence_root(run) -> str:
    led = run.read_json("evidence/ledgers.json") or {}
    root = (led.get("simlab") or {}).get("root")
    if root and os.path.isdir(root):
        return root
    if not os.environ.get("SIMLAB_EVIDENCE_ROOT"):
        import aces_engineering

        aces_engineering.prepare_env()
    return os.path.abspath(os.environ["SIMLAB_EVIDENCE_ROOT"])


def _check_simlab(run) -> tuple[bool, str]:
    import simlab.evidence_bundle as eb

    from aces_engineering.evidence_cli import verify_result

    led = (run.read_json("evidence/ledgers.json") or {}).get("simlab") or {}
    b = run.read_json("evidence/bundle.json") or {}
    root = _evidence_root(run)
    res = verify_result(root)
    if not res.get("ok"):
        return False, f"the simlab ledger at {root} does not verify ({_first_problem(res)})"
    entries = eb.read_ledger()
    pos = led.get("entry")
    if not isinstance(pos, int) or not (1 <= pos <= len(entries)):
        return False, f"recorded simlab position {pos} is not in the ledger ({len(entries)} entries)"
    if entries[pos - 1].get("sha256") != b.get("bundle_sha256"):
        return False, f"simlab ledger entry {pos} names {str(entries[pos - 1].get('sha256'))[:12]}, not this bundle"
    return True, (f"simlab ledger verifies ({res.get('entries')} entries, {res.get('relocated', 0)} relocated); "
                  f"entry {pos} is this bundle")


def _check_aces(run, aces) -> tuple[bool, str]:
    if aces is None:
        return False, "the aces command is not available to verify the aces ledger"
    led = (run.read_json("evidence/ledgers.json") or {}).get("aces") or {}
    b = run.read_json("evidence/bundle.json") or {}
    av = aces.ledger_verify()
    if not av.get("ok"):
        return False, f"the aces ledger does not verify ({av.get('message')})"
    n = led.get("n")
    for e in aces.ledger_find(LEDGER_KIND) or []:
        pl = e.get("payload") or {}
        if e.get("n") == n and pl.get("bundle_sha256") == b.get("bundle_sha256") and pl.get("run_id") == run.run_id:
            return True, f"aces ledger verifies ({av.get('entries')} entries); entry {n} is golden.run for this bundle"
    return False, f"aces ledger entry {n} does not name bundle {str(b.get('bundle_sha256'))[:12]} and run {run.run_id}"


def _num_equal(text: str, value) -> bool:
    if isinstance(value, bool):
        return text.strip().lower() == ("true" if value else "false")
    if isinstance(value, (int, float)):
        try:
            return float(text) == float(value)
        except ValueError:
            return False
    return True


def _check_sweep(run) -> tuple[bool, str]:
    sj = run.read_json("sweep.json")
    csv_path = Path(run.path("sweep.csv"))
    if not isinstance(sj, dict) or not csv_path.is_file():
        return True, "not run: no sweep.csv / sweep.json in this run"
    problems = []
    if sha256_file(csv_path) != sj.get("csv_sha256"):
        problems.append("sweep.csv sha256 differs from sweep.json csv_sha256")
    cases = {}
    try:
        cases = {c.get("case_id"): c for c in (run.cases() or [])}
    except Exception:
        cases = {c.get("case_id"): c for c in (run.read_json("cases.json", []) or [])}
    names = list((sj.get("variables") or {}).keys())
    checked = 0
    with open(csv_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") != "FINISHED":
                continue
            cid = row.get("case_id")
            res = run.read_json(f"{cid}/results.json")
            if not isinstance(res, dict):
                problems.append(f"{cid}: FINISHED in sweep.csv but has no results.json")
                continue
            checked += 1
            for col, text in row.items():
                if col in ("case_id", "status") or text in (None, ""):
                    continue
                if col in names:
                    ref = (res.get("design") or {}).get(col, (cases.get(cid) or {}).get(col))
                elif col in res and isinstance(res[col], (int, float, bool)):
                    ref = res[col]
                else:
                    continue
                if ref is not None and not _num_equal(text, ref):
                    problems.append(f"{cid}.{col}: sweep.csv {text} != results.json {ref}")
    if problems:
        return False, "; ".join(problems[:8])
    return True, f"sweep.csv matches its digest and {checked} FINISHED rows equal their results.json"


def _check_models(run, use_model: bool) -> tuple[bool, str]:
    b = run.read_json("evidence/bundle.json") or {}
    copies = (b.get("golden") or {}).get("surrogate") or []
    problems, n = [], 0
    k = 1
    while True:
        cv = run.read_json(f"surrogate/round-{k}/cv.json")
        if cv is None:
            break
        n += 1
        copy = next((c for c in copies if c.get("round") == k), None)
        if copy is None or canonical_json(copy) != canonical_json(cv):
            problems.append(f"round {k}: cv.json differs from the bundle's copy")
        if use_model:
            from . import surrogate as sur_mod

            op = run.read_json(f"optimise/round-{k}.json") or {}
            pt = op.get("winner") or op.get("closest_infeasible")
            if pt:
                sur = sur_mod.load(Path(run.path("surrogate", f"round-{k}")))
                mus, _ = sur_mod.predict(sur, [pt["params"]])
                for o, want in (pt.get("pred") or {}).items():
                    got = mus[0].get(o)
                    if got is None or abs(got - want) > 1e-9 * max(abs(want), 1e-300):
                        problems.append(f"round {k}: the saved model predicts {o} {got!r}, the record says {want!r}")
        k += 1
    if problems:
        return False, "; ".join(problems[:8])
    if n == 0:
        return None, "not run: no surrogate rounds in this run"  # None: shown as INFO, neither a pass nor a failure
    if not use_model:
        return True, f"{n} cv.json match the bundle; models not loaded (--no-model)"
    return True, f"{n} saved surrogate(s) reproduce their recorded predictions to 1e-9; cv.json match the bundle"


def _check_verdict(run) -> tuple[bool, str]:
    from . import verify as verify_mod

    val = run.read_json("validation.json")
    if not isinstance(val, dict):
        return False, "no validation.json"
    verdict, grade, _ = verify_mod.derive(val)
    problems = []
    if (val.get("verdict"), val.get("grade")) != (verdict, grade):
        problems.append(f"validation.json says {val.get('verdict')}/{val.get('grade')}, re-derived {verdict}/{grade}")
    res = run.read_json("result.json")
    if not isinstance(res, dict):
        problems.append("no result.json")
    elif (res.get("verdict"), res.get("grade")) != (verdict, grade):
        problems.append(f"result.json says {res.get('verdict')}/{res.get('grade')}")
    g = (run.read_json("evidence/bundle.json") or {}).get("golden") or {}
    if (g.get("verdict"), g.get("grade")) != (verdict, grade):
        problems.append(f"the bundle says {g.get('verdict')}/{g.get('grade')}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{verdict} / {grade} re-derived from validation.json; result.json and the bundle agree"


def _check_report(run) -> tuple[bool, str]:
    rep = run.read_json("report.json")
    if not isinstance(rep, dict):
        return False, "no report.json"
    problems, n = [], 0
    cache: dict = {}
    for p in rep.get("provenance") or []:
        n += 1
        try:
            have = resolve_pointer(rep, p["pointer"])
        except (KeyError, IndexError, ValueError, TypeError):
            problems.append(f"{p.get('pointer')}: does not resolve in report.json")
            continue
        src = p.get("source")
        if src not in cache:
            cache[src] = run.read_json(src) if src else None
        try:
            want = resolve_key(cache[src], p.get("key") or "")
        except (KeyError, TypeError):
            problems.append(f"{p.get('pointer')}: {src}#{p.get('key')} does not resolve")
            continue
        if not _same(have, want):
            problems.append(f"{p.get('pointer')} = {have!r} but {src}#{p.get('key')} = {want!r}")
    if problems:
        return False, f"{len(problems)} of {n} provenance pointers fail: " + "; ".join(problems[:6])
    return True, f"{n} provenance pointers resolve to their sources"


def _check_compute(run) -> "tuple[bool | None, str]":
    from . import compute as compute_mod

    # compute_used.json is re-derived with the current compute.py, so the comparison only means something while the
    # golden code is the code that wrote the run; after an upgrade an untampered old run must not fail this check
    recorded = (((run.read_json("evidence/bundle.json") or {}).get("golden") or {}).get("solver_digest") or {}) \
        .get("golden_code_sha256")
    now = golden_code()["golden_code_sha256"]
    if recorded and recorded != now:
        return None, (f"not re-derived: the golden code changed since the run (recorded {str(recorded)[:12]}, now "
                      f"{now[:12]}); the other checks still cover the run's files and records")
    problems = compute_mod.rederive_problems(run)
    if problems:
        return False, "; ".join(problems[:6])
    n = len((run.read_json("compute_used.json") or {}).get("rows") or [])
    return True, f"{n} compute-used rows re-derive from their sources"


def _check_manifest(run) -> tuple[bool, str]:
    p = Path(run.path("manifest.sha256"))
    if not p.is_file():
        return False, "no manifest.sha256"
    bad = []
    sums = parse_sums(p)
    for want, rel in sums:
        f = Path(run.root) / rel
        if not f.is_file():
            bad.append(f"{rel} missing")
        elif sha256_file(f) != want:
            bad.append(f"{rel} changed")
    if bad:
        return False, f"{len(bad)} of {len(sums)} files differ from manifest.sha256: " + "; ".join(bad[:8])
    return True, f"{len(sums)} files match manifest.sha256"


def _check_resolve(run, resolve: bool) -> "tuple[bool | None, str]":
    if not resolve:
        return None, "not run (pass --resolve to re-solve the winner on this CPU)"
    import importlib
    import platform

    w = _winner_record(run)
    if not w:
        return False, "no winner/ or candidate/ record to re-solve"
    label, cid = w.get("label"), w.get("case_id")
    res = run.read_json(f"{cid}/results.json") or {}
    h = res.get("elem_mm") or res.get("h_fea_mm") or ((run.read_json(f"{cid}/case.json") or {}).get("h_fea_mm"))
    stl = Path(run.path(label, f"{label}.stl"))
    domain = run.read_json("domain/domain.json") or {}
    eff = run.read_json("inputs/spec.effective.json") or {}
    comp = run.read_json("compute.json") or {}
    same = comp.get("host") == socket.gethostname() and comp.get("platform") == platform.platform()
    tol = 1e-9 if same else 1e-6
    fea = importlib.import_module(__package__ + ".fea")
    with tempfile.TemporaryDirectory(prefix="golden-resolve-") as td:
        out = fea.solve_mesh(stl, domain, eff, Path(td), h_mm=float(h), device="cpu")
    got = (out or {}).get("results") or {}
    if not got:
        return False, f"the re-solve did not finish ({(out or {}).get('status')}: {(out or {}).get('message')})"
    bad = []
    for o in ("mass_kg", "mass_voxel_kg", "compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "vm_p95_MPa"):
        a, b = got.get(o), res.get(o)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            rel = abs(float(a) - float(b)) / max(abs(float(b)), 1e-300)
            if rel > tol:
                bad.append(f"{o} {a!r} vs {b!r} ({rel:.2e})")
    if bad:
        return False, f"re-solve at {h} mm differs beyond {tol:g}: " + "; ".join(bad)
    return True, f"{label}.stl re-solved at {h} mm on this CPU matches {cid} within {tol:g}"


def audit(run, aces, *, use_model: bool = True, resolve: bool = False) -> dict:
    """The eleven checks of section 12.3 ({"ok", "checks": [{"name", "ok", "detail"}]}); ok iff every check passes.
    Loading the surrogate models unpickles joblib files: audit only run directories you trust (or use_model=False)."""
    plan = [("files", lambda: _check_files(run)), ("bundle", lambda: _check_bundle(run)),
            ("simlab-ledger", lambda: _check_simlab(run)), ("aces-ledger", lambda: _check_aces(run, aces)),
            ("sweep", lambda: _check_sweep(run)), ("surrogate", lambda: _check_models(run, use_model)),
            ("verdict", lambda: _check_verdict(run)), ("report", lambda: _check_report(run)),
            ("compute", lambda: _check_compute(run)), ("manifest", lambda: _check_manifest(run)),
            ("resolve", lambda: _check_resolve(run, resolve))]
    checks = []
    for name, fn in plan:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"the check raised {type(exc).__name__}: {exc}"
        # ok None: the check did not run (no --resolve, no surrogate rounds); reported, never counted as a pass
        checks.append({"name": name, "ok": None if ok is None else bool(ok), "detail": detail})
    return {"ok": all(c["ok"] is not False for c in checks), "checks": checks}
