"""Verification of aces golden: the surrogate's winner is re-solved on the real solver, then checked again.

`run_rounds` (section 10.8): each round fits the surrogate on every real solve so far, searches it, registers the
chosen design as verify case v00k and solves it from scratch (SIMP -> organic skin -> voxel FEA) through
`case.run_cases`. A round passes iff the case finished and converged, the design came from the predicted-feasible
set, every constrained output agrees with its prediction within tol and meets its limit on the real solve, and (with
check_objective) the objective agrees too. A failed round's solve joins the training set of the next round.

`independent_check` (10.9) re-voxelises the verified part at a finer resolution (level 1 gating, level 2 a GPU-only
extra; both on the rig with a lane, where a level-1 case the rig does not deliver is solved on this desktop as x101)
and compares; `device_parity` (10.10) re-solves the same voxel mask on the other devices (CuPy, Warp) and requires
agreement to 1e-6; `conforming_block` compares the conforming tet10 second discretisation (golden.conforming, on the
rig as m001 or on this desktop, m101 after the rig did not deliver) with the finest voxel solve of the final part.
`grade` and `finalise` turn all of it into validation.json (section 13 of the rig build spec): preview-grade unless
the level-1 check ran and agrees; capped at preview-grade when the conforming check ran and violates a constraint;
rig-verified when the level-1 check and the conforming check ran on the rig on validated images, agree, the rig ran
this desktop's golden code and every pulled file verified; verified-desktop otherwise.
"""
from __future__ import annotations

import importlib
import math
import time
from collections import Counter

from . import GRADE_PREVIEW, GRADE_RIG, GRADE_VERIFIED_DESKTOP, NullProgress, SCHEMA, clean

PARITY_TOL = 1e-6
PARITY_OUTPUTS = ("compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "mass_voxel_kg")
CHECK_REPORTED = ("compliance_Nmm", "mass_voxel_kg", "vm_max_MPa")
SOLVER = "step-structures stage_solve"
SCOPE = ("voxel FEA (H8, fictitious domain, Jacobi-PCG or multigrid-PCG, FP64, linear static) on this desktop or on a "
         "rig lane, with a conforming tet10 (C3D10, gmsh + CalculiX ccx) second discretisation when it can run. Grades: "
         "preview-grade (every solve on its own), verified-desktop (the finer-resolution level-1 check agrees within "
         "tol and meets every constraint), rig-verified (also: the level-1 check solved on the rig on an image the "
         "golden-check validation names, the conforming check ran on the rig on an image the ccx-check validation "
         "names and agrees within its bands and meets every constraint, the rig ran this desktop's golden code, and "
         "every pulled file verified). A conforming check that violates a constraint caps the grade at preview-grade. "
         "No grade is a sign-off.")
CONFORMING_KEYS = ("ran", "passed", "status", "case_id", "host", "mode", "reason", "h_mm", "source_case", "reference",
                   "record", "mesh", "solver", "bands", "rows", "constraints_met", "rig", "rig_attempt")
SHORT = {"mass_kg": "mass", "mass_voxel_kg": "mass_voxel", "mesh_volume_mm3": "mesh_volume",
         "voxel_volume_mm3": "voxel_volume", "compliance_Nmm": "compliance", "umax_mm": "umax", "vm_max_MPa": "vm_max",
         "vm_p99_MPa": "vm_p99", "vm_p95_MPa": "vm_p95"}
UNIT = {"mass_kg": "kg", "mass_voxel_kg": "kg", "mesh_volume_mm3": "mm3", "voxel_volume_mm3": "mm3",
        "compliance_Nmm": "N mm", "umax_mm": "mm", "vm_max_MPa": "MPa", "vm_p99_MPa": "MPa", "vm_p95_MPa": "MPa"}


def _g(v, digits: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}g}" if math.isfinite(v) else "-"
    return str(v)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _derived(eff: dict) -> dict:
    return eff.get("derived") or {}


def _run_cases(run, eff, domain, experiment, cases, *, pool, progress, kind):
    """B1's case runner, looked up at call time (tests substitute sys.modules[<package>.case])."""
    mod = importlib.import_module(__package__ + ".case")
    return mod.run_cases(run, eff, domain, experiment, cases, pool=pool, progress=progress, kind=kind)


def _image_token(v) -> str | None:
    s = str(v or "").strip()
    return s.split()[0] if s else None


def _short_image(v) -> str:
    tok = _image_token(v) or "-"
    return tok.split(":", 1)[1][:12] if ":" in tok else tok[:12]


# ----------------------------------------------------------------------------- case dicts

def check_case(lv: dict, final_case: str, seed: int, *, lane: str | None = None, case_id: str | None = None,
               fallback_from: str | None = None) -> dict:
    """The case dict of one independent-check level (section 9.4): on the rig (device and host "rig", the lane) or on
    this desktop (host "desktop", no lane); fea_solver "auto" on both. x101 is a desktop level 1 after the rig did not
    deliver x001 (fallback_from "x001")."""
    on_rig = lv.get("host") == "rig" and fallback_from is None
    c = {"case_id": case_id or f"x{int(lv['level']):03d}", "role": "check", "h_gen_mm": None, "h_fea_mm": lv["h_mm"],
         "device": "rig" if on_rig else lv["device"], "backend": "adapter", "source_case": final_case,
         "reuse_mask": False, "round": None, "level": int(lv["level"]), "seed": int(seed),
         "host": "rig" if on_rig else "desktop"}
    if on_rig:
        c["lane"] = lane
    c["fea_solver"] = "auto"
    if fallback_from:
        c["fallback_from"] = fallback_from
    return c


def conforming_case(plan: dict, final_case: str, seed: int, *, lane: str | None = None, case_id: str = "m001",
                    fallback_from: str | None = None, host: str | None = None) -> dict:
    """The case dict of the conforming check (section 9.3/9.4): m001 on the rig (device and host "rig", the lane, 16
    threads, the rig's DOF and time limits) or on this desktop (device "cpu", host "desktop", lane null, the desktop's
    limits); m101 is the desktop run after the rig did not deliver m001."""
    host = host or plan.get("host")
    if host == "rig":
        return {"case_id": case_id, "role": "conforming", "source_case": final_case, "level": 1,
                "h_mm": float(plan["h_mm"]), "device": "rig", "host": "rig", "lane": lane,
                "threads": int(plan["threads"]), "max_dof": int(plan["max_dof"]), "timeout_s": float(plan["timeout_s"]),
                "seed": int(seed)}
    from . import compute

    lim_dof, lim_t = compute._conforming_limits()
    c = {"case_id": case_id, "role": "conforming", "source_case": final_case, "level": 1, "h_mm": float(plan["h_mm"]),
         "device": "cpu", "host": "desktop", "lane": None,
         "threads": int(plan["threads"]) if plan.get("host") == "desktop" and plan.get("threads") else _local_threads(),
         "max_dof": int(lim_dof["desktop"]), "timeout_s": float(lim_t["desktop"]), "seed": int(seed)}
    if fallback_from:
        c["fallback_from"] = fallback_from
    return c


def _local_threads() -> int:
    import os

    try:
        n = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except Exception:
        n = os.cpu_count() or 1
    from .compute import CONFORMING_LOCAL_THREADS_MAX

    return max(1, min(int(n), CONFORMING_LOCAL_THREADS_MAX))


def _results(run, outcome: dict | None, case_id: str) -> dict | None:
    """The metrics of a FINISHED case: the outcome's results, else results.json on disk."""
    if outcome and outcome.get("status") == "FINISHED" and isinstance(outcome.get("results"), dict):
        return outcome["results"]
    if outcome is None or outcome.get("status") == "FINISHED":
        res = run.read_json(f"{case_id}/results.json")
        if isinstance(res, dict):
            return res
    return None


def _fail_text(case_id: str, outcome: dict | None) -> str:
    o = outcome or {}
    err = str(o.get("error") or "").strip().splitlines()
    tail = f": {err[-1][:160]}" if err else ""
    return f"{case_id} did not finish ({o.get('design_status') or o.get('status') or 'no outcome'}){tail}"


def _meets(value, op, limit) -> bool | None:
    if not _finite(value) or op is None or limit is None:
        return None
    return float(value) <= float(limit) if op == "<=" else float(value) >= float(limit)


# ----------------------------------------------------------------------------- compare

def compare(pred: dict, sd: dict, solved: dict | None, eff: dict) -> tuple[list[dict], bool, list[str]]:
    """Predicted vs solved for every output of derived.outputs: one objective row, one row per constraint and a
    reported row for the rest. ok iff every constraint row agrees within tol and meets its limit and, with
    verify.check_objective, the objective row agrees."""
    d = _derived(eff)
    tol = float(d.get("tol") if d.get("tol") is not None else 0.10)
    obj = (d.get("objective") or {}).get("output")
    cons = list(d.get("constraints") or [])
    check_obj = bool((eff.get("verify") or {}).get("check_objective", True))
    outs = list(d.get("outputs") or [])
    pred = pred or {}
    sd = sd or {}
    solved = solved if isinstance(solved, dict) else None

    def row(o: str, role: str, op=None, limit=None) -> dict:
        p, s = pred.get(o), (solved or {}).get(o)
        r = {"output": o, "role": role, "predicted": p, "sd": sd.get(o), "solved": s, "abs_err": None,
             "rel_err": None, "tol": tol, "agree": False, "op": op, "limit": limit, "meets": None}
        if _finite(p) and _finite(s):
            r["abs_err"] = abs(float(p) - float(s))
            r["rel_err"] = r["abs_err"] / max(abs(float(s)), 1e-300)
            r["agree"] = r["rel_err"] <= tol
        if role == "constraint":
            r["meets"] = _meets(s, op, limit)
        return r

    rows = []
    if obj:
        rows.append(row(obj, "objective"))
    for c in cons:
        rows.append(row(c["output"], "constraint", c["op"], c["limit"]))
    seen = {obj} | {c["output"] for c in cons}
    rows += [row(o, "reported") for o in outs if o not in seen]
    reasons = []
    if solved is None:
        reasons.append("the verify case has no solved metrics")
    ok = solved is not None
    # constraint reasons first (the verdict line quotes the first reason, and a constraint is what keeps a part safe)
    for r in rows:
        o = r["output"]
        if r["role"] != "constraint":
            continue
        if r["meets"] is False:
            ok = False
            reasons.append(f"{o} {_g(r['solved'])} does not meet {r['op']} {_g(r['limit'])} on the real solve")
        elif r["meets"] is None and solved is not None:
            ok = False
            reasons.append(f"{o} was not solved")
        if not r["agree"]:
            ok = False
            if r["rel_err"] is not None:
                reasons.append(f"{o} predicted {_g(r['predicted'])} vs solved {_g(r['solved'])}: "
                               f"{100 * r['rel_err']:.1f} % exceeds the {100 * tol:g} % tolerance")
    for r in rows:
        if r["role"] == "objective" and check_obj and not r["agree"]:
            ok = False
            if r["rel_err"] is not None:
                reasons.append(f"objective {r['output']} predicted {_g(r['predicted'])} vs solved "
                               f"{_g(r['solved'])}: {100 * r['rel_err']:.1f} % exceeds the {100 * tol:g} % tolerance")
    return clean(rows), bool(ok), reasons


# ----------------------------------------------------------------------------- rounds

def _round_line(k: int, fit: dict, opt: dict, pick: dict | None) -> str:
    cv = fit.get("cv") or {}
    mape = " · ".join(f"{SHORT.get(o, o)} {_g((cv.get(o) or {}).get('mape_pct'), 3)} %" for o in fit.get("outputs") or [])
    avail = int(opt.get("n_candidates") or 0) - int(opt.get("n_excluded") or 0)
    head = (f"round {k}: GP n={fit.get('n')} · CV MAPE {mape} · {opt.get('n_feasible')} of {avail} candidates "
            f"feasible")
    if fit.get("flags"):
        head += f" · weak: {'; '.join(fit['flags'])}"
    if pick is None:
        return head + " · no candidate left to verify"
    params = " ".join(f"{v} {_g(x)}" for v, x in pick["params"].items())
    return head + (f" · winner {params}" if opt.get("from_feasible") else f" · none feasible: infill at {params}")


def _verify_line(rec: dict) -> str:
    parts = []
    for r in rec.get("rows") or []:
        if r["role"] in ("objective", "constraint"):
            rel = f" ({100 * r['rel_err']:.1f} %)" if r.get("rel_err") is not None else ""
            parts.append(f"{SHORT.get(r['output'], r['output'])} {_g(r['predicted'])} -> {_g(r['solved'])}{rel}")
    verdict = "PASS" if rec.get("passed") else "FAIL"
    why = "" if rec.get("passed") or not rec.get("reasons") else f": {rec['reasons'][0]}"
    return f"round {rec['round']}: {rec['case_id']} predicted -> solved " + " · ".join(parts) + f" · {verdict}{why}"


def run_rounds(run, eff, domain, experiment, pool, plan, progress) -> dict:
    """The surrogate -> optimise -> verify loop. Returns {"rounds", "passed", "outcome", "final_round", "final_case",
    "winner_case", "winner_params"}; outcome is "pass", "infeasible" (the last round had no feasible candidate) or
    "verification-failed"."""
    from . import optimise as opt_mod
    from . import surrogate as sur_mod

    progress = progress or NullProgress()
    d = _derived(eff)
    rounds_max = int(d.get("rounds") or (eff.get("optimise") or {}).get("rounds") or 3)
    seed = int(d.get("seed") if d.get("seed") is not None else (eff.get("sweep") or {}).get("seed") or 0)
    device = ((plan or {}).get("verify") or {}).get("device") or "cpu"
    loop = {"rounds": [], "passed": False, "outcome": None, "final_round": None, "final_case": None,
            "winner_case": None, "winner_params": None}
    exclude: list[dict] = []
    last_from_feasible = None
    for k in range(1, rounds_max + 1):
        t0 = time.perf_counter()
        sur, fit = sur_mod.fit(run, eff, round_no=k, seed=seed)
        opt = opt_mod.search(sur, eff, fit, run, round_no=k, seed=seed, exclude=exclude)
        pick = opt_mod.chosen(opt)
        progress.line(_round_line(k, fit, opt, pick))
        progress.event("note", stage="loop", msg=f"round {k} search", data={"n_feasible": opt.get("n_feasible")})
        if pick is None:
            loop["rounds"].append({
                "round": k, "surrogate": f"surrogate/round-{k}/cv.json", "optimise": f"optimise/round-{k}.json",
                "case_id": None, "params": None, "from_feasible": False, "h_fea_mm": d.get("fea_mm"), "device": device,
                "solver": SOLVER, "rows": [], "status": "not-run", "converged": False, "passed": False,
                "reasons": ["no candidate left after excluding the designs already verified"],
                "wall_s": round(time.perf_counter() - t0, 3)})
            last_from_feasible = False
            break
        case_id = f"v{k:03d}"
        case = {"case_id": case_id, "role": "verify", **pick["params"], "h_gen_mm": d.get("gen_mm"),
                "h_fea_mm": d.get("fea_mm"), "device": device, "backend": "adapter", "source_case": None,
                "reuse_mask": False, "round": k, "level": None, "seed": seed, "fea_solver": "pcg"}
        outcomes = _run_cases(run, eff, domain, experiment, [case], pool=pool if device == "cpu" else None,
                              progress=progress, kind="design")
        out = outcomes[0] if outcomes else None
        solved = _results(run, out, case_id)
        finished = bool(out and out.get("status") == "FINISHED" and solved is not None)
        rows, ok, reasons = compare(pick["pred"], pick["sd"], solved if finished else None, eff)
        converged = bool(finished and solved.get("converged", True) is not False)
        from_feasible = bool(opt.get("from_feasible"))
        if not finished:
            reasons.insert(0, _fail_text(case_id, out))
        if not from_feasible:
            reasons.insert(0, "no candidate was predicted feasible with the margins: the closest infeasible design "
                              "was solved as infill")
        passed = finished and converged and from_feasible and ok
        rec = {"round": k, "surrogate": f"surrogate/round-{k}/cv.json", "optimise": f"optimise/round-{k}.json",
               "case_id": case_id, "params": pick["params"], "from_feasible": from_feasible,
               "h_fea_mm": d.get("fea_mm"), "device": device, "solver": SOLVER, "rows": rows,
               "status": (out or {}).get("status") or "FAILED", "design_status": (out or {}).get("design_status"),
               "converged": converged, "passed": bool(passed), "reasons": [] if passed else reasons,
               "wall_s": round(time.perf_counter() - t0, 3)}
        loop["rounds"].append(clean(rec))
        progress.line(_verify_line(rec))
        exclude.append({"case_id": case_id, "params": pick["params"]})
        last_from_feasible = from_feasible
        loop["final_round"] = k
        loop["final_case"] = case_id
        if passed:
            loop.update(passed=True, winner_case=case_id, winner_params=dict(pick["params"]))
            break
    if loop["passed"]:
        loop["outcome"] = "pass"
    elif last_from_feasible is False:
        loop["outcome"] = "infeasible"
    else:
        loop["outcome"] = "verification-failed"
    return clean(loop)


# ----------------------------------------------------------------------------- independent check

def _check_rows(coarse: dict, fine: dict, eff: dict) -> list[dict]:
    d = _derived(eff)
    tol = float(d.get("tol") if d.get("tol") is not None else 0.10)
    obj = (d.get("objective") or {}).get("output")
    cons = list(d.get("constraints") or [])

    def row(o, role, op=None, limit=None) -> dict:
        c, f = coarse.get(o), fine.get(o)
        r = {"output": o, "role": role, "coarse": c, "fine": f, "rel_gap": None, "tol": tol, "agree": False, "op": op,
             "limit": limit, "meets_fine": None}
        if _finite(c) and _finite(f):
            r["rel_gap"] = (float(f) - float(c)) / max(abs(float(c)), 1e-300)
            r["agree"] = abs(r["rel_gap"]) <= tol
        if role == "constraint":
            r["meets_fine"] = _meets(f, op, limit)
        return r

    rows = []
    if obj:
        rows.append(row(obj, "objective"))
    for c in cons:
        rows.append(row(c["output"], "constraint", c["op"], c["limit"]))
    seen = {obj} | {c["output"] for c in cons}
    rows += [row(o, "reported") for o in CHECK_REPORTED if o not in seen]
    return rows


def _level_passed(rows: list[dict]) -> bool:
    """Every gating row (objective, constraints) agrees within tol, and every constraint is met at the finer h."""
    gating = [r for r in rows if r["role"] in ("objective", "constraint")]
    if not gating:
        return False
    for r in gating:
        if not r["agree"]:
            return False
        if r["role"] == "constraint" and r["meets_fine"] is not True:
            return False
    return True


def _where(entry: dict) -> str:
    if entry.get("host") == "rig":
        rb = entry.get("rig") or {}
        gpu = f" GPU {rb['gpu']}" if rb.get("gpu") is not None else ""
        name = f" ({rb['gpu_name']})" if rb.get("gpu_name") else ""
        return f"the rig{gpu}{name}"
    return str(entry.get("device"))


def _check_line(entry: dict, h_coarse) -> str:
    if not entry.get("ran"):
        return f"check level {entry['level']} at {_g(entry['h_mm'])} mm: not run: {_not_run_text(entry.get('reason'))}"
    est = (f" · estimate {_g(entry.get('est_s'), 3)} s vs budget {_g(entry.get('budget_s'))} s"
           if entry.get("est_s") is not None else "")
    gaps = " · ".join(f"{SHORT.get(r['output'], r['output'])} {100 * r['rel_gap']:+.1f} %"
                      for r in entry.get("rows") or [] if r["role"] in ("objective", "constraint")
                      and r.get("rel_gap") is not None)
    if entry.get("passed"):
        res = "agrees -> verified-desktop" if entry.get("gating") else "agrees (informational)"
    else:
        res = ("does not agree -> preview-grade" if entry.get("gating") else "does not agree (informational)")
        if not entry.get("rows"):
            res = f"{entry.get('reason')} -> preview-grade" if entry.get("gating") else entry.get("reason")
    solver = f" ({entry['fea_solver']})" if entry.get("fea_solver") not in (None, "auto") else ""
    return (f"check level {entry['level']}: {entry.get('case_id')} at {_g(entry['h_mm'])} mm vs {_g(h_coarse)} mm on "
            f"{_where(entry)}{solver}{est} · {gaps or 'no gaps'} · {res}")


def _not_run_text(reason) -> str:
    s = str(reason or "")
    return s[len("not run: "):] if s.startswith("not run: ") else s


def _stub_level(lv: dict, reason: str) -> dict:
    return {"level": lv.get("level"), "case_id": None, "h_mm": lv.get("h_mm"), "device": lv.get("device"),
            "gating": bool(lv.get("gating", lv.get("level") == 1)), "est_s": lv.get("est_s"),
            "budget_s": lv.get("budget_s"), "ran": False, "reason": reason, "rows": [], "passed": None,
            "host": None, "fea_solver": None, "rig": None, "rig_attempt": None}


def _attempt(got: dict | None, case_id: str) -> dict:
    """rig_attempt {"case_id", "design_status", "reason"} of a rig case that did not come back as a usable result."""
    if got is None:
        return {"case_id": case_id, "design_status": "rig-error",
                "reason": f"{case_id} was not submitted to the rig"}
    why = str(got.get("reason") or (got.get("outcome") or {}).get("error") or "").strip()
    return {"case_id": case_id, "design_status": got.get("design_status"),
            "reason": why.splitlines()[0][:300] if why else None}


def _score(entry: dict, out: dict | None, fine: dict | None, coarse: dict, eff: dict, cid: str) -> None:
    entry["fea_solver"] = (fine or {}).get("fea_solver") or "auto"
    if not (out and out.get("status") == "FINISHED" and fine is not None):
        entry["passed"] = False
        entry["reason"] = _fail_text(cid, out)
    else:
        entry["rows"] = _check_rows(coarse, fine, eff)
        entry["passed"] = _level_passed(entry["rows"])


def _desktop_level(run, eff, domain, experiment, lv: dict, final: str, seed: int, coarse: dict, pool, progress, *,
                   case_id: str | None = None, fallback_from: str | None = None) -> dict:
    case = check_case(lv, final, seed, case_id=case_id, fallback_from=fallback_from)
    cid = case["case_id"]
    progress.line(f"check level {lv['level']}: {cid} re-voxelises {final}/part.stl at {_g(lv['h_mm'])} mm on "
                  f"{lv['device']} ({lv.get('reason')})")
    outs = _run_cases(run, eff, domain, experiment, [case], pool=pool if lv["device"] == "cpu" else None,
                      progress=progress, kind="mesh")
    out = outs[0] if outs else None
    entry = _stub_level(lv, lv.get("reason") or "")
    entry.update(case_id=cid, ran=True, host="desktop")
    _score(entry, out, _results(run, out, cid), coarse, eff, cid)
    return entry


def _rig_level(run, eff, domain, experiment, lv: dict, final: str, seed: int, coarse: dict, pool, progress, *,
               got: dict | None, rig) -> dict:
    """One level planned on the rig: the delivered result, or for level 1 the desktop fallback x101 (section 10.9)."""
    cid = f"x{int(lv['level']):03d}"
    out = (got or {}).get("outcome")
    if got is not None and got.get("state") == "result":
        entry = _stub_level(lv, lv.get("reason") or "")
        entry.update(case_id=cid, ran=True, device="rig", host="rig", est_s=None, rig=rig.level_block(cid))
        _score(entry, out, _results(run, out, cid), coarse, eff, cid)
        return entry
    attempt = _attempt(got, cid)
    fb = lv.get("fallback") if int(lv["level"]) == 1 else None
    if fb and fb.get("run"):
        progress.line(f"check level 1: the rig did not deliver {cid} ({attempt['design_status']}); this desktop runs "
                      "level 1 as x101")
        entry = _desktop_level(run, eff, domain, experiment, fb, final, seed, coarse, pool, progress, case_id="x101",
                               fallback_from=cid)
        entry["rig_attempt"] = attempt
        return entry
    why = f"the rig did not deliver {cid}: {attempt['design_status']}"
    if attempt.get("reason"):
        why += f" ({attempt['reason']})"
    if fb is not None:
        why += f"; this desktop cannot run level 1 instead: {_not_run_text(fb.get('reason'))}"
    entry = _stub_level(lv, f"not run: {why}")
    entry.update(rig_attempt=attempt, est_s=None)
    return entry


def independent_check(run, eff, domain, experiment, loop: dict, levels: list[dict], pool, progress, *,
                      rig=None) -> dict:
    """Re-voxelise the final verify case's part.stl at each planned level's h and compare (section 10.9). Level 1
    gates the grade; level 2 is informational. Levels planned on the rig (host "rig") are collected from the rig
    session's check experiment; a level-1 case the rig does not deliver is solved on this desktop as x101 when the
    desktop plan can run it (level 2 never falls back). Returns validation.independent_check."""
    progress = progress or NullProgress()
    d = _derived(eff)
    seed = int(d.get("seed") or 0)
    h_coarse = d.get("fea_mm")
    res: dict = {"ran": False, "reason": None, "passed": None, "levels": []}
    if not (loop or {}).get("passed"):
        res["reason"] = "not run: verification failed"
        res["levels"] = [_stub_level(lv, "not run: verification failed") for lv in levels or []]
        return clean(res)
    final = loop["final_case"]
    coarse = _results(run, None, final) or {}
    collected: dict = {}
    if rig is not None and any(lv.get("run") and lv.get("host") == "rig" for lv in levels or []):
        collected = rig.collect("check") or {}
    for lv in levels or []:
        if not lv.get("run"):
            entry = _stub_level(lv, lv.get("reason") or "")
        elif lv.get("host") == "rig":
            entry = _rig_level(run, eff, domain, experiment, lv, final, seed, coarse, pool, progress,
                               got=collected.get(f"x{int(lv['level']):03d}"), rig=rig)
        else:
            entry = _desktop_level(run, eff, domain, experiment, lv, final, seed, coarse, pool, progress)
        res["levels"].append(entry)
        progress.line(_check_line(entry, h_coarse))
    l1 = next((e for e in res["levels"] if e.get("level") == 1), None)
    res["ran"] = any(e.get("ran") for e in res["levels"])
    if l1 is not None and l1.get("ran"):
        res["passed"] = bool(l1.get("passed"))
        res["reason"] = l1.get("reason") or "level 1 ran"
    else:
        why = (l1 or {}).get("reason") or "no level-1 check planned"
        res["reason"] = why if str(why).startswith("not run") else f"not run: {why}"
        res["passed"] = None
    return clean(res)


# ----------------------------------------------------------------------------- device parity

def device_parity(run, eff, domain, experiment, loop: dict, plan: dict, progress) -> list[dict]:
    """Re-solve the final verify case's voxel mask (reused, not re-voxelised) on each planned device and backend and
    compare compliance, umax, vm_max, vm_p99 and the voxel mass with the verify solve; above 1e-6 relative is a
    disagreement (a correctness problem, so the verdict is FAIL)."""
    progress = progress or NullProgress()
    if not (loop or {}).get("passed"):
        return []
    d = _derived(eff)
    seed = int(d.get("seed") or 0)
    final = loop["final_case"]
    ref = _results(run, None, final) or {}
    out_list = []
    for p in (plan or {}).get("parity") or []:
        cid = p["case_id"]
        case = {"case_id": cid, "role": "parity", "h_gen_mm": None, "h_fea_mm": d.get("fea_mm"), "device": p["device"],
                "backend": p["backend"], "source_case": final, "reuse_mask": True, "round": None, "level": None,
                "seed": seed, "fea_solver": "pcg"}
        outs = _run_cases(run, eff, domain, experiment, [case], pool=None, progress=progress, kind="mesh")
        out = outs[0] if outs else None
        got = _results(run, out, cid)
        rec = {"case_id": cid, "device": p["device"], "backend": p["backend"], "per_output": {}, "max_rel_diff": None,
               "tol": PARITY_TOL, "passed": False}
        if not (out and out.get("status") == "FINISHED" and got is not None):
            rec["error"] = _fail_text(cid, out)
        else:
            per = {}
            for o in PARITY_OUTPUTS:
                if _finite(got.get(o)) and _finite(ref.get(o)):
                    per[o] = abs(float(got[o]) - float(ref[o])) / max(abs(float(ref[o])), 1e-300)
            rec["per_output"] = per
            if per:
                rec["max_rel_diff"] = max(per.values())
                rec["passed"] = rec["max_rel_diff"] <= PARITY_TOL
            else:
                rec["error"] = "no comparable outputs in the parity solve"
        out_list.append(clean(rec))
        if rec["max_rel_diff"] is not None:
            worst = max(rec["per_output"], key=rec["per_output"].get)
            progress.line(f"parity {cid} {p['device']}/{p['backend']}: max rel diff {rec['max_rel_diff']:.1e} on "
                          f"{SHORT.get(worst, worst)} · {'agrees' if rec['passed'] else 'DISAGREES'}")
        else:
            progress.line(f"parity {cid} {p['device']}/{p['backend']}: {rec.get('error')}")
    return out_list


# ----------------------------------------------------------------------------- conforming second discretisation

def conforming_not_run(reason: str, *, mode: str | None = None, h_mm=None, source_case: str | None = None,
                       case_id: str | None = None, host: str | None = None, rig_attempt: dict | None = None) -> dict:
    """validation.json "conforming" when no conforming check ran (every key present, section 14.3)."""
    return {"ran": False, "passed": None, "status": None, "case_id": case_id, "host": host, "mode": mode,
            "reason": reason, "h_mm": h_mm, "source_case": source_case, "reference": None, "record": None,
            "mesh": None, "solver": None, "bands": None, "rows": [], "constraints_met": None, "rig": None,
            "rig_attempt": rig_attempt}


def _run_conforming(run, eff, domain, experiment, case: dict, progress) -> dict:
    """B2's case.run_conforming_case, looked up at call time like _run_cases."""
    mod = importlib.import_module(__package__ + ".case")
    return mod.run_conforming_case(run, eff, domain, experiment, case, progress=progress)


def local_conforming(run, eff, domain, experiment, loop: dict, plan: dict | None, progress) -> dict | None:
    """Stage 6 step 5: the conforming check on this desktop as m001 when plan.conforming puts it here. Returns
    {"case", "outcome"} for conforming_block(local=...), or None when it does not run here."""
    plan = plan or {}
    if not (loop or {}).get("passed") or plan.get("host") != "desktop":
        return None
    d = _derived(eff)
    case = conforming_case(plan, loop["final_case"], int(d.get("seed") or 0), case_id="m001", host="desktop")
    progress = progress or NullProgress()
    progress.line(f"conforming check: m001 meshes {loop['final_case']}/part.stl with tet10 at {_g(case['h_mm'])} mm "
                  f"on this desktop ({case['threads']} threads)")
    return {"case": case, "outcome": _run_conforming(run, eff, domain, experiment, case, progress)}


def _reference(run, eff: dict, check: dict | None, final: str) -> tuple[dict | None, dict | None]:
    """The finest voxel solve of the final part that finished (section 12.7): x002, else the level-1 entry (x001 or
    x101), else the final verify case; (results, {"case_id", "h_mm", "host"})."""
    entries = [e for e in (check or {}).get("levels") or [] if e.get("ran") and e.get("case_id")]
    for e in sorted(entries, key=lambda e: -int(e.get("level") or 0)):
        res = _results(run, None, e["case_id"])
        if res is not None:
            return res, {"case_id": e["case_id"], "h_mm": e.get("h_mm"), "host": e.get("host") or "desktop"}
    res = _results(run, None, final)
    if res is not None:
        return res, {"case_id": final, "h_mm": _derived(eff).get("fea_mm"), "host": "desktop"}
    return None, None


def _mesh_of(rec: dict | None) -> dict | None:
    m = (rec or {}).get("mesh")
    if not isinstance(m, dict):
        return None
    return {k: m.get(k) for k in ("route", "elements", "nodes", "dof", "min_sicn", "volume_rel_err")}


def _solver_of(rec: dict | None) -> dict | None:
    s = (rec or {}).get("solver")
    if not isinstance(s, dict):
        return None
    return {"name": s.get("tool") or "CalculiX ccx", "version": s.get("version"), "sha256": s.get("sha256"),
            "threads": s.get("threads"), "linear_solver": s.get("linear_solver")}


def _conforming_line(blk: dict) -> str:
    cid = blk.get("case_id") or "-"
    if not blk.get("ran"):
        return f"conforming check: {cid} not run: {_not_run_text(blk.get('reason'))}"
    where = "the rig CPU" if blk.get("host") == "rig" else "this desktop"
    mesh = blk.get("mesh") or {}
    size = f" · {mesh.get('elements')} C3D10, {mesh.get('dof')} DOF" if mesh.get("elements") is not None else ""
    ref = blk.get("reference") or {}
    vs = f" vs {ref.get('case_id')} at {_g(ref.get('h_mm'))} mm" if ref.get("case_id") else ""
    gaps = " · ".join(f"{SHORT.get(r['output'], r['output'])} {100 * r['rel_gap']:+.1f} %"
                      for r in blk.get("rows") or [] if r.get("role") in ("objective", "constraint", "gating")
                      and _finite(r.get("rel_gap")))
    if blk.get("passed"):
        res = "agrees within its bands and meets every constraint"
    elif blk.get("status") == "ok":
        res = f"does not agree: {blk.get('reason')}"
    else:
        res = f"failed: {blk.get('reason')}"
    return (f"conforming check: {cid} tet10 at {_g(blk.get('h_mm'))} mm on {where}{vs}{size}"
            f"{' · ' + gaps if gaps else ''} · {res}")


def _conforming_from(run, eff: dict, check: dict | None, case: dict, oc: dict | None, *, mode, rig_blk,
                     attempt) -> dict:
    cid = case["case_id"]
    rec = run.read_json(f"{cid}/conforming.json")
    rec = rec if isinstance(rec, dict) else None
    status = str((rec or {}).get("status") or (oc or {}).get("design_status") or "error")
    ran = (oc or {}).get("status") != "NOT_RUN"
    blk = {"ran": ran, "passed": None, "status": status, "case_id": cid, "host": case.get("host"), "mode": mode,
           "reason": None, "h_mm": case.get("h_mm"), "source_case": case.get("source_case"), "reference": None,
           "record": f"{cid}/conforming.json" if rec is not None else None, "mesh": _mesh_of(rec),
           "solver": _solver_of(rec), "bands": None, "rows": [], "constraints_met": None, "rig": rig_blk,
           "rig_attempt": attempt}
    if status != "ok" or rec is None:
        msg = str((rec or {}).get("message") or (oc or {}).get("error") or "").strip()
        first = msg.splitlines()[-1][:300] if msg else ""
        blk["reason"] = f"{status}: {first}" if first and not first.startswith(f"{status}:") else (first or status)
        blk["passed"] = False if ran else None
        return blk
    ref, ref_meta = _reference(run, eff, check, str(case.get("source_case")))
    blk["reference"] = ref_meta
    try:
        from . import conforming

        blk["bands"] = conforming.bands(eff)
        rows, ok, reasons = conforming.compare(ref or {}, rec, eff)
    except Exception as exc:  # a comparison that cannot run is a failed check, never a crash of the run
        rows, ok, reasons = [], False, [f"the comparison could not run ({type(exc).__name__}: {exc})"]
    blk["rows"] = list(rows or [])
    blk["passed"] = bool(ok)
    cons = [r for r in blk["rows"] if r.get("role") == "constraint"]
    blk["constraints_met"] = all(r.get("meets_conforming") is True for r in cons) if blk["rows"] else None
    blk["reason"] = ("agrees within its bands and meets every constraint" if ok
                     else (reasons[0] if reasons else "does not agree"))
    return blk


def conforming_block(run, eff, domain, experiment, loop: dict, check: dict | None, plan: dict | None, progress, *,
                     rig=None, local: dict | None = None) -> dict:
    """validation.json "conforming" (section 14.3): the conforming tet10 second discretisation of the final part,
    compared with the finest voxel solve that finished (golden.conforming.compare and bands). On the rig it collects
    m001 from the conf experiment; a case the rig does not deliver, or a NOT_RUN outcome, runs on this desktop as
    m101 in mode auto when this desktop can run it; a FAILED conforming check never falls back. On this desktop it
    takes the m001 run by local_conforming (or runs it now)."""
    progress = progress or NullProgress()
    plan = dict(plan or {})
    mode, h = plan.get("mode"), plan.get("h_mm")
    if not (loop or {}).get("passed"):
        return conforming_not_run("not run: verification failed", mode=mode, h_mm=h)
    seed = int(_derived(eff).get("seed") or 0)
    final = loop["final_case"]
    host = plan.get("host")
    if host is None:
        blk = conforming_not_run(f"not run: {_not_run_text(plan.get('reason')) or 'no conforming check planned'}",
                                 mode=mode, h_mm=h, source_case=final)
        progress.line(_conforming_line(blk))
        return clean(blk)
    attempt = rig_blk = None
    if host == "rig":
        got = (rig.collect("conf") or {}).get("m001") if rig is not None else None
        out = (got or {}).get("outcome") or {}
        # delivered: a FINISHED or FAILED conforming outcome; a rig-* status or a NOT_RUN outcome is not delivered
        delivered = got is not None and got.get("state") == "result" and out.get("status") in ("FINISHED", "FAILED")
        case, oc = conforming_case(plan, final, seed, lane=getattr(rig, "lane", None)), out
        if delivered:
            rig_blk = rig.conforming_block("m001")
        else:
            attempt = _attempt(got, "m001")
            if mode == "auto" and plan.get("fallback") == "desktop":
                progress.line(f"conforming check: the rig did not deliver m001 ({attempt['design_status']}); this "
                              "desktop runs it as m101")
                case = conforming_case(plan, final, seed, case_id="m101", fallback_from="m001", host="desktop")
                oc = _run_conforming(run, eff, domain, experiment, case, progress)
            else:
                why = f"the rig did not deliver m001: {attempt['design_status']}"
                if attempt.get("reason"):
                    why += f" ({attempt['reason']})"
                if mode == "auto":
                    why += "; this desktop cannot run it instead (golden.conforming is not usable here)"
                blk = conforming_not_run(f"not run: {why}", mode=mode, h_mm=h, source_case=final, case_id="m001",
                                         host="rig", rig_attempt=attempt)
                progress.line(_conforming_line(blk))
                return clean(blk)
    else:
        if local is None:
            local = local_conforming(run, eff, domain, experiment, loop, plan, progress)
        case, oc = local["case"], local["outcome"]
    blk = _conforming_from(run, eff, check, case, oc, mode=mode, rig_blk=rig_blk, attempt=attempt)
    progress.line(_conforming_line(blk))
    return clean(blk)


# ----------------------------------------------------------------------------- grade, verdict, validation.json

def _sha12(v) -> str:
    return str(v)[:12] if v else "missing"


def _first_mismatch(ci: dict) -> str:
    mm = (ci or {}).get("mismatches") or []
    if not mm:
        return "no rig outcome carried a code identity"
    m = mm[0]
    return f"{m.get('path')}: this desktop {_sha12(m.get('desktop'))}, the rig {_sha12(m.get('rig'))}"


def _validation_problem(solver: str, validation, validation_image, image) -> str | None:
    if validation != "pass":
        return f"{solver} validation is {validation or 'missing'} in the rig registry"
    if _image_token(image) is None or _image_token(validation_image) != _image_token(image):
        return f"{solver} was validated on image {_short_image(validation_image)}, this run used {_short_image(image)}"
    return None


def _conforming_elsewhere(c: dict) -> str:
    att = c.get("rig_attempt") or {}
    if c.get("ran") and c.get("host") == "desktop":
        if att:
            return (f"{c.get('case_id')} ran on this desktop: the rig did not deliver {att.get('case_id')}: "
                    f"{att.get('design_status')}")
        return f"{c.get('case_id')} ran on this desktop"
    why = _not_run_text(c.get("reason")) or "no conforming check"
    return why


def rig_failure(l1: dict | None, conforming: dict | None, rig: dict | None) -> str | None:
    """The first failing condition a-f of rig-verified (section 13.1), as the text after "not rig-verified: "; None
    when every condition holds."""
    l1 = l1 or {}
    c = conforming or {}
    if l1.get("host") != "rig":
        att = l1.get("rig_attempt") or {}
        if att:
            return (f"the level-1 check ran on this desktop ({l1.get('case_id')}: the rig did not deliver "
                    f"{att.get('case_id')}: {att.get('design_status')})")
        why = "no rig lane was used" if rig is None else "the check was not sent to the rig"
        return f"the level-1 check ran on this desktop ({l1.get('case_id')}: {why})"
    rb = l1.get("rig") or {}
    bad = _validation_problem("golden-check", rb.get("solver_validation"), rb.get("validation_image_id"),
                              rb.get("image_id"))
    if bad:
        return bad
    if not c.get("ran") or c.get("host") != "rig":
        return f"the conforming check did not run on the rig ({_conforming_elsewhere(c)})"
    if not c.get("passed"):
        if c.get("status") != "ok":
            return f"the conforming check failed on the rig ({c.get('reason') or c.get('status')})"
        return f"the conforming check does not agree ({c.get('reason') or c.get('status')})"
    cb = c.get("rig") or {}
    bad = _validation_problem("ccx-check", cb.get("solver_validation"), cb.get("validation_image_id"),
                              cb.get("image_id"))
    if bad:
        return bad
    ci = (rig or {}).get("code_identity") or {}
    if not ci.get("ok"):
        return f"code identity failed ({_first_mismatch(ci)})"
    if (rig or {}).get("integrity_error"):
        return f"rig integrity error ({rig['integrity_error']})"
    if rig is None:
        return "no rig lane was used"
    return None


def _grade_core(loop_passed: bool, check: dict | None, tol: float, h_fea, *, conforming: dict | None = None,
                rig: dict | None = None) -> tuple[str, list[str]]:
    """Section 13.1, steps 1 to 6. conforming and rig are the validation.json blocks (None in files written before
    they existed, which then derive exactly as before)."""
    if not loop_passed:
        return GRADE_PREVIEW, ["check skipped: verification failed"]
    levels = (check or {}).get("levels") or []
    l1 = next((e for e in levels if e.get("level") == 1), None)
    if not check or l1 is None or not l1.get("ran"):
        why = (l1 or {}).get("reason") or (check or {}).get("reason") or "no independent check"
        why = str(why)
        if why.startswith("not run: "):
            why = why[len("not run: "):]
        return GRADE_PREVIEW, [f"check skipped: {why}"]
    if not l1.get("passed"):
        reasons = []
        for r in l1.get("rows") or []:
            if r.get("role") not in ("objective", "constraint"):
                continue
            if not r.get("agree") and r.get("rel_gap") is not None:
                reasons.append(f"{r['output']} {100 * r['rel_gap']:+.1f} % between {_g(h_fea)} and "
                               f"{_g(l1.get('h_mm'))} mm exceeds {100 * tol:g} %")
            if r.get("meets_fine") is False:
                word = "exceeds" if r.get("op") == "<=" else "is below"
                reasons.append(f"{r['output']} {_g(r.get('fine'))} {word} {_g(r.get('limit'))} at "
                               f"{_g(l1.get('h_mm'))} mm")
        if not reasons:
            reasons.append(f"check {l1.get('case_id')} failed: {l1.get('reason') or 'no comparable metrics'}")
        return GRADE_PREVIEW, reasons
    c = conforming or {}
    if c.get("status") == "ok" and c.get("constraints_met") is False:
        cons = [r for r in c.get("rows") or [] if r.get("role") == "constraint"]
        r = next((r for r in cons if r.get("meets_conforming") is False),
                 next((r for r in cons if r.get("meets_conforming") is not True), {}))
        return GRADE_PREVIEW, [f"the conforming second discretisation violates {r.get('output')} "
                               f"{_g(r.get('conforming'))} {r.get('op')} {_g(r.get('limit'))} ({c.get('case_id')}, "
                               f"tet10 {_g(c.get('h_mm'))} mm, {c.get('host')})"]
    base = (f"the {_g(l1.get('h_mm'))} mm check agrees with the {_g(h_fea)} mm solve within {100 * tol:g} % and meets "
            "every constraint")
    fail = rig_failure(l1, conforming, rig)
    if fail is None:
        return GRADE_RIG, [f"{base}, solved on the rig",
                           f"the conforming tet10 second discretisation ({c.get('case_id')}, {_g(c.get('h_mm'))} mm, "
                           "rig) agrees within its bands and meets every constraint"]
    reasons = [base]
    if rig is not None or c.get("ran"):
        reasons.append(f"not rig-verified: {fail}")
    return GRADE_VERIFIED_DESKTOP, reasons


def grade(loop: dict, check: dict, eff: dict, *, conforming: dict | None = None,
          rig: dict | None = None) -> tuple[str, list[str]]:
    """preview-grade, verified-desktop or rig-verified with the reasons (section 13.1)."""
    d = _derived(eff)
    tol = float(d.get("tol") if d.get("tol") is not None else 0.10)
    return _grade_core(bool((loop or {}).get("passed")), check, tol, d.get("fea_mm"), conforming=conforming, rig=rig)


def _verdict_core(outcome: str, loop_passed: bool, parity: list[dict]) -> str:
    if outcome == "thin-sweep":
        return "THIN"
    if outcome == "infeasible":
        return "INFEASIBLE"
    if loop_passed and all(p.get("passed") for p in parity or []):
        return "PASS"
    return "FAIL"


def derive(validation: dict) -> tuple[str, str, list[str]]:
    """(verdict, grade, grade_reasons) re-derived from a validation.json (the audit's check). Files written before
    the conforming and rig blocks existed derive exactly as before."""
    rounds = validation.get("rounds") or []
    loop_passed = any(r.get("passed") for r in rounds)
    outcome = validation.get("outcome") or ""
    if outcome == "verification-failed" and loop_passed:
        outcome = "pass"  # a parity disagreement turned a passing loop into a FAIL
    verdict = _verdict_core(outcome, loop_passed, validation.get("device_parity") or [])
    if outcome == "thin-sweep":
        g, why = GRADE_PREVIEW, ["check skipped: thin sweep"]
    else:
        res = validation.get("resolution_mm") or {}
        g, why = _grade_core(loop_passed, validation.get("independent_check"), float(validation.get("tol") or 0.10),
                             res.get("solve"), conforming=validation.get("conforming"), rig=validation.get("rig"))
    return verdict, g, why


def _thin_info(run, eff) -> dict:
    sweep = run.read_json("sweep.json") or {}
    rows = sweep.get("rows") or []
    n = sweep.get("n", len(rows))
    n_ok = sweep.get("n_ok", sum(1 for r in rows if r.get("status") == "FINISHED"))
    bad = Counter(r.get("design_status") for r in rows if r.get("status") != "FINISHED" and r.get("design_status"))
    run_doc = run.read_json("run.json") or {}
    argv = ((run_doc.get("tool") or {}).get("argv")) or []
    allow_thin = "--allow-thin" in argv
    need = 3 if allow_thin else int((eff.get("surrogate") or {}).get("min_points") or 8)
    return {"n_ok": n_ok, "n": n, "need": need, "allow_thin": allow_thin,
            "most_common": bad.most_common(1)[0][0] if bad else None}


def finalise(run, eff, loop: dict | None, check: dict | None, parity: list[dict],
             grade_: tuple[str, list[str]] | None, *, outcome: str | None = None, conforming: dict | None = None,
             rig: dict | None = None) -> dict:
    """Write validation.json and return it. A THIN run calls finalise(run, eff, None, None, [], None,
    outcome="thin-sweep"); FAIL and INFEASIBLE runs pass check=None and parity=[] (not run: verification failed).
    conforming is the conforming block (a not-run block when None) and rig the rig block (None without a lane)."""
    d = _derived(eff)
    loop = loop or {"rounds": [], "passed": False, "outcome": None, "final_round": None, "final_case": None,
                    "winner_case": None, "winner_params": None}
    loop_passed = bool(loop.get("passed"))
    outcome = outcome or loop.get("outcome") or ("pass" if loop_passed else "verification-failed")
    parity = list(parity or [])
    planned = [lv for lv in d.get("check_levels") or []]
    if check is None:
        why = ("not run: thin sweep" if outcome == "thin-sweep" else
               "not run: verification failed" if not loop_passed else "not run: no independent check")
        check = {"ran": False, "reason": why, "passed": None,
                 "levels": [{"level": lv.get("level"), "case_id": None, "h_mm": lv.get("h_mm"), "device": None,
                             "gating": lv.get("level") == 1, "est_s": None, "budget_s": d.get("check_budget_s"),
                             "ran": False, "reason": why, "rows": [], "passed": None, "host": None,
                             "fea_solver": None, "rig": None, "rig_attempt": None} for lv in planned]}
    if conforming is None:
        why = ("not run: thin sweep" if outcome == "thin-sweep" else
               "not run: verification failed" if not loop_passed else "not run: no conforming check")
        conforming = conforming_not_run(why)
    if outcome == "thin-sweep":
        g, g_why = GRADE_PREVIEW, ["check skipped: thin sweep"]
    elif grade_ is None:
        g, g_why = grade(loop, check, eff, conforming=conforming, rig=rig)
    else:
        g, g_why = grade_[0], list(grade_[1])
    verdict = _verdict_core(outcome, loop_passed, parity)
    reasons: list[str] = []
    thin = None
    rounds = loop.get("rounds") or []
    last = rounds[-1] if rounds else {}
    if verdict == "THIN":
        thin = _thin_info(run, eff)
        reasons.append(f"only {thin['n_ok']} of {thin['n']} designs finished: "
                       f"{thin['most_common'] or 'no failed design status recorded'}")
    elif verdict == "INFEASIBLE":
        reasons.append(f"no candidate predicted feasible with the margins (round {loop.get('final_round') or len(rounds)})")
        reasons += [r for r in last.get("reasons") or [] if not r.startswith("no candidate was predicted feasible")]
    elif verdict == "FAIL":
        if loop_passed:
            outcome = "verification-failed"
            for p in parity:
                if not p.get("passed"):
                    if p.get("max_rel_diff") is not None:
                        reasons.append(f"device disagreement on {p.get('device')}/{p.get('backend')}: max rel diff "
                                       f"{p['max_rel_diff']:.3g}")
                    else:
                        reasons.append(f"device parity {p.get('case_id')} on {p.get('device')}/{p.get('backend')} "
                                       f"failed: {p.get('error') or 'no result'}")
        else:
            reasons += list(last.get("reasons") or []) or ["verification failed"]
    doc = {
        "schema": SCHEMA["validation"], "tol": d.get("tol"), "kappa": d.get("kappa"),
        "cv_margin": bool((eff.get("optimise") or {}).get("cv_margin", True)),
        "check_objective": bool((eff.get("verify") or {}).get("check_objective", True)),
        "objective": d.get("objective"), "constraints": d.get("constraints"),
        "resolution_mm": {"generator": d.get("gen_mm"), "field": d.get("field_h_mm"), "solve": d.get("fea_mm"),
                          "check": [lv.get("h_mm") for lv in check.get("levels") or []] or
                          [lv.get("h_mm") for lv in planned]},
        "rounds": rounds, "outcome": outcome, "passed": verdict == "PASS",
        "final_round": loop.get("final_round"), "final_case": loop.get("final_case"),
        "winner_case": loop.get("winner_case"), "winner_params": loop.get("winner_params"),
        "independent_check": check, "device_parity": parity, "conforming": conforming, "rig": rig,
        "grade": g, "grade_reasons": g_why, "verdict": verdict, "verdict_reasons": reasons,
        "scope": SCOPE,
    }
    if thin is not None:
        doc["thin"] = thin
    doc = clean(doc)
    run.write_json("validation.json", doc)
    return doc
