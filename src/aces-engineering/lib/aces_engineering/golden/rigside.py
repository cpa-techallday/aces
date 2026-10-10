"""aces golden on the rig: the body of the simlab solvers golden-case, golden-check and ccx-check.

The rig's simlab worker runs a golden case inside the aces-golden image through a thin shim
(simlab/solvers/{golden_case,golden_check,ccx_check}.py in cudax-sim-runtime), which calls run(case, spec, run_dir,
solver). The experiment was written by the desktop (golden.rig): params.golden says which files the desktop uploaded
(path under the spec store, sha256, bytes) and sweep.golden_case is exactly the case the desktop registered.

run() stages the uploaded inputs under <run_dir>/golden (every file confined to the spec store and checked against the
desktop's sha256; a mismatch is a FAILED outcome with design status rig-integrity), then runs the same code the desktop
runs: golden.case.make_job_at + run_job for a design or mesh case (lane rig-gpu, on the GPU the lease gave the
container), or golden.conforming.run_check + case.conforming_outcome for a conforming case (lane rig-cpu). It writes
<run_dir>/outcome.json (the whole outcome plus a rig block: experiment, image, the golden code that ran) and
<run_dir>/files.json (every case output under golden/<case id>/ with its size and sha256), which is what the desktop
pulls and verifies. A FINISHED case returns its results (the worker writes them as results.json); anything else raises,
so the worker records FAILED and outcome.json stays for the desktop.

`python3 -m aces_engineering.golden.rigside --selfcheck` prints one JSON document (the imports the rig solvers need,
ccx and the golden compute digest) and exits 0 when everything is there, 1 otherwise. ACES_GOLDEN_FORCE_DEVICE is for
the desktop's test fake only: it forces the device (cpu) and is recorded in the outcome.

Standard library only at module level: the worker imports the solver stack inside run().
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

from . import clean, dump_json, sha256_file, utc_now

OUTCOME_SCHEMA = "aces-golden-outcome/1"
FILES_SCHEMA = "aces-golden-files/1"
PARAMS_SCHEMA = "aces-golden-rig/1"
FORCE_DEVICE_ENV = "ACES_GOLDEN_FORCE_DEVICE"
KINDS = {"golden-case": "design", "golden-check": "mesh", "ccx-check": "conforming"}
_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_LANES = {"design": "rig-gpu", "mesh": "rig-gpu", "conforming": "rig-cpu"}


class InputError(RuntimeError):
    """The request is not a golden rig case, or an uploaded input is not the file the desktop sent."""


class _Mismatch(Exception):
    pass


def _plain(name: str, what: str) -> str:
    name = str(name)
    if not _NAME.match(name):
        raise _Mismatch(f"{what} {name!r} is not a plain name")
    return name


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _stage_inputs(g: dict, spec, root: Path) -> tuple[dict, dict]:
    """Copy every uploaded file of params.golden.files to its target under `root` and check it against the desktop's
    sha256. Returns (eff, domain). Raises _Mismatch naming the file and both hashes."""
    files = g.get("files") or {}
    if not isinstance(files, dict) or "eff" not in files or "domain" not in files:
        raise _Mismatch("params.golden.files must name at least eff and domain")
    base = os.path.realpath(spec.base_dir)
    root_real = os.path.realpath(str(root))

    def copy(key: str, target_rel: str) -> Path:
        f = files.get(key)
        if not isinstance(f, dict) or not f.get("path"):
            raise _Mismatch(f"params.golden.files.{key} has no path")
        src = os.path.realpath(spec.resolve(str(f["path"])))
        if not _inside(src, base):
            raise _Mismatch(f"{f['path']} resolves outside the spec store ({src})")
        if not os.path.isfile(src):
            raise _Mismatch(f"{f['path']} is not on the rig")
        dst = Path(os.path.normpath(os.path.join(root_real, target_rel)))
        if not _inside(str(dst), root_real):
            raise _Mismatch(f"the target {target_rel!r} of {f['path']} escapes the staging folder")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        got = sha256_file(dst)
        if got != f.get("sha256"):
            raise _Mismatch(f"{f['path']}: sha256 {got} on the rig, {f.get('sha256')} sent by the desktop")
        return dst

    eff_path = copy("eff", "inputs/spec.effective.json")
    dom_path = copy("domain", "domain/domain.json")
    eff = json.loads(eff_path.read_text(encoding="utf-8"))
    domain = json.loads(dom_path.read_text(encoding="utf-8"))
    for key, f in files.items():
        if key in ("eff", "domain"):
            continue
        if key == "mask":
            if domain.get("mask_path") != "domain/mask.npy":
                raise _Mismatch(f"domain.json mask_path is {domain.get('mask_path')!r}, not domain/mask.npy")
            copy(key, "domain/mask.npy")
        elif key == "sdf":
            rel = domain.get("sdf_field_path")
            if not rel or str(rel).startswith(("/", "\\")) or ".." in str(rel).replace("\\", "/").split("/"):
                raise _Mismatch(f"domain.json sdf_field_path is {rel!r}: no field to stage the upload into")
            copy(key, str(rel))
        elif key in ("stl", "mask_src", "mesh_src"):
            cid = _plain((f or {}).get("case_id"), f"params.golden.files.{key}.case_id")
            copy(key, f"{cid}/" + {"stl": "part.stl", "mask_src": "mask.npy", "mesh_src": "mesh.json"}[key])
        else:
            raise _Mismatch(f"params.golden.files has an unknown entry {key!r}")
    return eff, domain


def _new_outcome(gcase: dict, started: str) -> dict:
    return {"case_id": gcase["case_id"], "role": gcase.get("role"), "status": "FAILED", "design_status": "error",
            "error": None, "results": None, "diagnostics": {"generator": None, "skin": None, "checks": None},
            "started_utc": started, "finished_utc": None, "wall_s": None, "stages": []}


def _files_doc(spec, case: dict, gid: str, run_dir: Path) -> dict:
    case_root = run_dir / "golden" / gid
    rows = []
    if case_root.is_dir():
        for p in case_root.rglob("*"):
            if p.is_file() and p.name != "progress.json" and not p.name.endswith(".tmp"):
                rows.append({"path": p.relative_to(run_dir).as_posix(), "bytes": int(p.stat().st_size),
                             "sha256": sha256_file(p)})
    rows.sort(key=lambda r: r["path"])
    return {"schema": FILES_SCHEMA, "experiment_id": spec.id, "rig_case_id": case.get("case_id"),
            "golden_case_id": gid, "root": f"golden/{gid}", "files": rows}


def _last_line(text) -> str:
    lines = [ln for ln in str(text or "").strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else "no error text"


def run(case: dict, spec, run_dir: str, solver: str) -> dict:
    """Run one golden rig case (section 8.2 of the rig build spec). Returns the results of a FINISHED case (artifacts
    as absolute rig paths, plus the rig block); raises InputError for a request that is not a golden case or an input
    that does not match, RuntimeError for any other outcome (outcome.json and files.json are written first)."""
    t_start = time.time()
    started = utc_now()
    if solver not in KINDS:
        raise InputError(f"{solver!r} is not a golden rig solver ({', '.join(KINDS)})")
    kind = KINDS[solver]
    g = case.get("golden") or {}
    if g.get("schema") != PARAMS_SCHEMA:
        raise InputError(f"params.golden.schema is {g.get('schema')!r}, not {PARAMS_SCHEMA}")
    if g.get("kind") != kind:
        raise InputError(f"{solver} runs {kind} cases; params.golden.kind is {g.get('kind')!r}")
    if not isinstance(case.get("golden_case"), dict) or not case["golden_case"].get("case_id"):
        raise InputError("the case carries no golden_case (sweep.golden_case of the experiment)")
    gcase = dict(case["golden_case"])
    gid = str(gcase["case_id"])
    if not _NAME.match(gid):
        raise InputError(f"golden case id {gid!r} is not a plain name")
    # absolute: the simlab worker passes the run directory relative to its cwd (/work), and every path below is
    # handed on (to the conforming mesher child, which runs in another cwd; to the results as absolute rig paths)
    run_dir_p = Path(os.path.abspath(run_dir))
    root = run_dir_p / "golden"
    root.mkdir(parents=True, exist_ok=True)
    forced = os.environ.get(FORCE_DEVICE_ENV) or None
    failure: Exception | None = None
    try:
        eff, domain = _stage_inputs(g, spec, root)
    except _Mismatch as exc:
        outcome = _new_outcome(gcase, started)
        outcome.update(design_status="rig-integrity", error=f"input integrity: {exc}")
        failure = InputError(str(exc))
    else:
        from . import case as case_mod

        if kind in ("design", "mesh"):
            job = case_mod.make_job_at(root, eff, domain, gcase)
            job["case"]["device"] = forced or "gpu"
            job["lane"] = _LANES[kind]
            outcome = case_mod.run_job(job, kind)
        else:
            try:
                from . import conforming

                src = _plain(gcase.get("source_case"), "source_case")
                rec = conforming.run_check(root / src / "part.stl", eff, domain, root / gid, case_id=gid,
                                           size_mm=gcase["h_mm"], where="rig", threads=gcase["threads"],
                                           timeout_s=gcase["timeout_s"], max_dof=gcase["max_dof"],
                                           ccx=os.environ.get("ACES_CCX"))
                outcome = case_mod.conforming_outcome(gcase, rec, lane=_LANES[kind], host="rig")
                if outcome.get("results") is not None:
                    outcome["results"]["artifacts"] = case_mod._artifacts(root / gid)
            except Exception:  # an internal error (a modelling failure is a conforming record, not an exception)
                outcome = _new_outcome(gcase, started)
                outcome.update(error=traceback.format_exc())
    if outcome.get("finished_utc") is None:
        outcome["finished_utc"] = utc_now()
    if outcome.get("wall_s") is None:
        outcome["wall_s"] = time.time() - t_start
    outcome["rig"] = {"experiment": spec.id, "rig_case": case.get("case_id"), "golden_case": gid, "solver": solver,
                      "image": os.environ.get("SIMLAB_IMAGE"), "image_digest": os.environ.get("SIMLAB_IMAGE_DIGEST"),
                      "device_forced": forced, "code": code_digest(),
                      "desktop_golden_compute_sha256": g.get("golden_compute_sha256")}
    dump_json(run_dir_p / "outcome.json", {"schema": OUTCOME_SCHEMA, **clean(outcome)})
    dump_json(run_dir_p / "files.json", _files_doc(spec, case, gid, run_dir_p))
    if failure is not None:
        raise failure
    if outcome.get("status") == "FINISHED" and isinstance(outcome.get("results"), dict):
        return clean({**outcome["results"], "rig": outcome["rig"]})
    raise RuntimeError(f"golden case {gid}: {outcome.get('design_status')}: {_last_line(outcome.get('error'))}")


def code_digest() -> dict:
    """What golden code ran: {"golden_compute_sha256" (golden.evidence.compute_digest), "modules": {"simlab/<rel>.py" |
    "gendesign/<rel>.py": LF sha256} for every module in sys.modules whose file lies under the imported simlab or
    gendesign package directory, "simlab_from", "gendesign_from", "aces_engineering_from", "python"}."""
    from .evidence import _lf_sha256, compute_digest

    out: dict = {"golden_compute_sha256": compute_digest()["golden_compute_sha256"], "modules": {}, "simlab_from": None,
                 "gendesign_from": None, "aces_engineering_from": str(Path(__file__).resolve().parent.parent),
                 "python": sys.version.split()[0]}
    tops: dict[str, Path] = {}
    for top in ("simlab", "gendesign"):
        mod = sys.modules.get(top)
        if mod is not None and getattr(mod, "__file__", None):
            tops[top] = Path(mod.__file__).resolve().parent
            out[f"{top}_from"] = str(tops[top])
    modules = {}
    for name in sorted(list(sys.modules)):
        top = name.split(".")[0]
        if top not in tops:
            continue
        f = getattr(sys.modules.get(name), "__file__", None)
        if not f or not str(f).endswith(".py"):
            continue
        p = Path(f).resolve()
        try:
            rel = p.relative_to(tops[top]).as_posix()
        except ValueError:
            continue
        with contextlib.suppress(OSError):
            modules[f"{top}/{rel}"] = _lf_sha256(p)
    out["modules"] = dict(sorted(modules.items()))
    return out


def _version(mod) -> str:
    return str(getattr(mod, "__version__", None) or "importable")


def selfcheck() -> dict:
    """{"ok", "python", "numpy", "scipy", "trimesh", "embreex", "skimage", "gmsh", "cupy" (import only, no device),
    "ccx" (path, version and sha256 from golden.conforming.available()), "golden_compute_sha256", "problems"}."""
    out: dict = {"ok": False, "python": sys.version.split()[0], "numpy": None, "scipy": None, "trimesh": None,
                 "embreex": None, "skimage": None, "gmsh": None, "cupy": None, "ccx": None,
                 "golden_compute_sha256": None, "problems": []}
    for key in ("numpy", "scipy", "trimesh", "embreex", "skimage", "gmsh", "cupy"):
        try:
            with contextlib.redirect_stdout(sys.stderr):
                mod = importlib.import_module(key)
            out[key] = _version(mod)
        except Exception as exc:  # ImportError, or a native library that does not load
            out["problems"].append(f"{key}: {type(exc).__name__}: {str(exc).strip()[:200]}")
    try:
        from . import conforming

        av = conforming.available()
        out["ccx"] = {"path": av.get("ccx"), "version": av.get("ccx_version"), "sha256": av.get("ccx_sha256")}
        if not av.get("ccx"):
            out["problems"].append(f"ccx: not found ({av.get('reason')})")
    except Exception as exc:
        out["problems"].append(f"ccx: golden.conforming is not usable here ({type(exc).__name__}: {exc})")
    try:
        from .evidence import compute_digest

        out["golden_compute_sha256"] = compute_digest()["golden_compute_sha256"]
    except Exception as exc:
        out["problems"].append(f"golden compute digest: {type(exc).__name__}: {exc}")
    out["ok"] = not out["problems"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m aces_engineering.golden.rigside",
                                 description="The aces golden rig solvers' body; --selfcheck checks the image.")
    ap.add_argument("--selfcheck", action="store_true", help="print one JSON document; exit 0 when the image is complete")
    a = ap.parse_args(argv)
    if not a.selfcheck:
        ap.print_usage(sys.stderr)
        return 2
    res = selfcheck()
    sys.stdout.write(json.dumps(clean(res), indent=1, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
