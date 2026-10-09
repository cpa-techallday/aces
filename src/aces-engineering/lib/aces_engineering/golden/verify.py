"""Verification of aces golden: the surrogate's winner is re-solved on the real solver, then checked again.

`run_rounds` (section 10.8): each round fits the surrogate on every real solve so far, searches it, registers the
chosen design as verify case v00k and solves it from scratch (SIMP -> organic skin -> voxel FEA) through
`case.run_cases`. A round passes iff the case finished and converged, the design came from the predicted-feasible
set, every constrained output agrees with its prediction within tol and meets its limit on the real solve, and (with
check_objective) the objective agrees too. A failed round's solve joins the training set of the next round.

`independent_check` (10.9) re-voxelises the verified part at a finer resolution (level 1 gating, level 2 a GPU-only
extra) and compares; `device_parity` (10.10) re-solves the same voxel mask on the other devices (CuPy, Warp) and
requires agreement to 1e-6. `grade` and `finalise` (10.11) turn all of it into validation.json: the winner is
`verified-desktop` only when the level-1 check agrees within tol and the constraints hold at the finer resolution;
every desktop solve is otherwise `preview-grade`, and `rig-verified` is never produced in v0.
"""
from __future__ import annotations

import importlib
import math
import time
from collections import Counter

from . import GRADE_PREVIEW, GRADE_VERIFIED_DESKTOP, NullProgress, SCHEMA, clean

PARITY_TOL = 1e-6
PARITY_OUTPUTS = ("compliance_Nmm", "umax_mm", "vm_max_MPa", "vm_p99_MPa", "mass_voxel_kg")
CHECK_REPORTED = ("compliance_Nmm", "mass_voxel_kg", "vm_max_MPa")
SOLVER = "step-structures stage_solve"
SCOPE = ("desktop voxel FEA (H8, fictitious domain, Jacobi-PCG, FP64, linear static); a desktop result is evidence, "
         "not sign-off; sign-off needs a rig run and a conforming-mesh second discretisation (not in v0)")
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
                "reuse_mask": False, "round": k, "level": None, "seed": seed}
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


def _check_line(entry: dict, h_coarse) -> str:
    if not entry.get("ran"):
        return f"check level {entry['level']} at {_g(entry['h_mm'])} mm: not run: {entry.get('reason')}"
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
    return (f"check level {entry['level']}: {entry.get('case_id')} at {_g(entry['h_mm'])} mm vs {_g(h_coarse)} mm on "
            f"{entry.get('device')}{est} · {gaps or 'no gaps'} · {res}")


def independent_check(run, eff, domain, experiment, loop: dict, levels: list[dict], pool, progress) -> dict:
    """Re-voxelise the final verify case's part.stl at each planned level's h and compare (section 10.9). Level 1
    gates the grade; level 2 (GPU only) is informational. Returns validation.independent_check."""
    progress = progress or NullProgress()
    d = _derived(eff)
    seed = int(d.get("seed") or 0)
    h_coarse = d.get("fea_mm")
    res: dict = {"ran": False, "reason": None, "passed": None, "levels": []}

    def stub(lv: dict, reason: str) -> dict:
        return {"level": lv.get("level"), "case_id": None, "h_mm": lv.get("h_mm"), "device": lv.get("device"),
                "gating": bool(lv.get("gating", lv.get("level") == 1)), "est_s": lv.get("est_s"),
                "budget_s": lv.get("budget_s"), "ran": False, "reason": reason, "rows": [], "passed": None}

    if not (loop or {}).get("passed"):
        res["reason"] = "not run: verification failed"
        res["levels"] = [stub(lv, "not run: verification failed") for lv in levels or []]
        return clean(res)
    final = loop["final_case"]
    coarse = _results(run, None, final) or {}
    for lv in levels or []:
        entry = stub(lv, lv.get("reason") or "")
        if not lv.get("run"):
            res["levels"].append(entry)
            progress.line(_check_line(entry, h_coarse))
            continue
        cid = f"x{int(lv['level']):03d}"
        case = {"case_id": cid, "role": "check", "h_gen_mm": None, "h_fea_mm": lv["h_mm"], "device": lv["device"],
                "backend": "adapter", "source_case": final, "reuse_mask": False, "round": None,
                "level": int(lv["level"]), "seed": seed}
        progress.line(f"check level {lv['level']}: {cid} re-voxelises {final}/part.stl at {_g(lv['h_mm'])} mm on "
                      f"{lv['device']} ({lv.get('reason')})")
        outs = _run_cases(run, eff, domain, experiment, [case], pool=pool if lv["device"] == "cpu" else None,
                          progress=progress, kind="mesh")
        out = outs[0] if outs else None
        fine = _results(run, out, cid)
        entry.update(case_id=cid, ran=True)
        if not (out and out.get("status") == "FINISHED" and fine is not None):
            entry["passed"] = False
            entry["reason"] = _fail_text(cid, out)
        else:
            entry["rows"] = _check_rows(coarse, fine, eff)
            entry["passed"] = _level_passed(entry["rows"])
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
                "seed": seed}
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


# ----------------------------------------------------------------------------- grade, verdict, validation.json

def _grade_core(loop_passed: bool, check: dict | None, tol: float, h_fea) -> tuple[str, list[str]]:
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
    if l1.get("passed"):
        return GRADE_VERIFIED_DESKTOP, [f"the {_g(l1.get('h_mm'))} mm check agrees with the {_g(h_fea)} mm solve within "
                                        f"{100 * tol:g} % and meets every constraint"]
    reasons = []
    for r in l1.get("rows") or []:
        if r.get("role") not in ("objective", "constraint"):
            continue
        if not r.get("agree") and r.get("rel_gap") is not None:
            reasons.append(f"{r['output']} {100 * r['rel_gap']:+.1f} % between {_g(h_fea)} and {_g(l1.get('h_mm'))} mm "
                           f"exceeds {100 * tol:g} %")
        if r.get("meets_fine") is False:
            word = "exceeds" if r.get("op") == "<=" else "is below"
            reasons.append(f"{r['output']} {_g(r.get('fine'))} {word} {_g(r.get('limit'))} at {_g(l1.get('h_mm'))} mm")
    if not reasons:
        reasons.append(f"check {l1.get('case_id')} failed: {l1.get('reason') or 'no comparable metrics'}")
    return GRADE_PREVIEW, reasons


def grade(loop: dict, check: dict, eff: dict) -> tuple[str, list[str]]:
    """verified-desktop iff the level-1 check ran and passed; preview-grade otherwise, with the reasons."""
    d = _derived(eff)
    tol = float(d.get("tol") if d.get("tol") is not None else 0.10)
    return _grade_core(bool((loop or {}).get("passed")), check, tol, d.get("fea_mm"))


def _verdict_core(outcome: str, loop_passed: bool, parity: list[dict]) -> str:
    if outcome == "thin-sweep":
        return "THIN"
    if outcome == "infeasible":
        return "INFEASIBLE"
    if loop_passed and all(p.get("passed") for p in parity or []):
        return "PASS"
    return "FAIL"


def derive(validation: dict) -> tuple[str, str, list[str]]:
    """(verdict, grade, grade_reasons) re-derived from a validation.json (the audit's check)."""
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
                             res.get("solve"))
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
             grade_: tuple[str, list[str]] | None, *, outcome: str | None = None) -> dict:
    """Write validation.json and return it. A THIN run calls finalise(run, eff, None, None, [], None,
    outcome="thin-sweep"); FAIL and INFEASIBLE runs pass check=None and parity=[] (not run: verification failed)."""
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
                             "ran": False, "reason": why, "rows": [], "passed": None} for lv in planned]}
    if outcome == "thin-sweep":
        g, g_why = GRADE_PREVIEW, ["check skipped: thin sweep"]
    elif grade_ is None:
        g, g_why = grade(loop, check, eff)
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
        "independent_check": check, "device_parity": parity,
        "grade": g, "grade_reasons": g_why, "verdict": verdict, "verdict_reasons": reasons,
        "scope": SCOPE,
    }
    if thin is not None:
        doc["thin"] = thin
    doc = clean(doc)
    run.write_json("validation.json", doc)
    return doc
