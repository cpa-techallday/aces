"""B2 unit tests: the grade ladder of the rig build spec, section 13 (golden.verify.grade / derive, and the exit-code
rule of --strict, --require-grade and a rig integrity error in golden.pipeline). Every validation block below is a
hand-built test input in the shapes of section 14.3; no number here is a result of a solve.
Run: python -m unittest discover -s tests -p "test_g4_grades.py"."""
from __future__ import annotations

import copy
import unittest

import fake_simlab

from aces_engineering import golden
from aces_engineering.golden import pipeline, verify

IMG = fake_simlab.FAKE_IMAGE  # "sha256:fake-image (test fixture)"
OTHER = "sha256:0123456789abcdef0123 (test fixture)"
EFF = {"derived": {"tol": 0.10, "fea_mm": 4.0}}
LOOP = {"passed": True, "final_case": "v001"}
BASE = "the 2 mm check agrees with the 4 mm solve within 10 % and meets every constraint"
CONF_OK = "the conforming tet10 second discretisation (m001, 2 mm, rig) agrees within its bands and meets every constraint"


def l1(*, host="rig", case_id="x001", passed=True, validation="pass", vimg=IMG, img=IMG, attempt=None) -> dict:
    rig = None
    if host == "rig":
        rig = {"lane": "sim/pool", "experiment": "golden-run-check", "rig_case": "c000", "job_id": "job (test input)",
               "gpu": 2, "gpu_name": "fake GPU (test fixture)", "gpu_uuid": "GPU-fake-0001", "image": "aces-golden:0.1",
               "image_id": img, "solver": "golden-check", "solver_validation": validation,
               "validation_image_id": vimg, "wait_s": 0.0, "run_s": 0.0}
    rows = [{"output": "mass_kg", "role": "objective", "coarse": 1.0, "fine": 1.0, "rel_gap": 0.0, "tol": 0.1,
             "agree": True, "op": None, "limit": None, "meets_fine": None},
            {"output": "umax_mm", "role": "constraint", "coarse": 0.05, "fine": 0.052, "rel_gap": 0.04, "tol": 0.1,
             "agree": True, "op": "<=", "limit": 0.06, "meets_fine": True}]
    return {"level": 1, "case_id": case_id, "h_mm": 2.0, "device": "rig" if host == "rig" else "cpu", "gating": True,
            "est_s": None, "budget_s": 60.0, "ran": True, "reason": "test input", "rows": rows, "passed": passed,
            "host": host, "fea_solver": "pcg", "rig": rig, "rig_attempt": attempt}


def check(level: dict) -> dict:
    return {"ran": True, "reason": "level 1 ran", "passed": level["passed"], "levels": [level]}


def conf(*, host="rig", status="ok", passed=True, met=True, validation="pass", vimg=IMG, img=IMG, attempt=None,
         case_id="m001", reason=None) -> dict:
    rows = [{"output": "mass_kg", "role": "objective", "voxel": 1.0, "conforming": 1.0, "rel_gap": 0.0, "band": 0.01,
             "agree": True, "op": None, "limit": None, "meets_conforming": None},
            {"output": "umax_mm", "role": "constraint", "voxel": 0.052, "conforming": 0.07 if not met else 0.053,
             "rel_gap": 0.0, "band": 0.1, "agree": passed, "op": "<=", "limit": 0.06, "meets_conforming": met}]
    rig = None
    if host == "rig":
        rig = {"experiment": "golden-run-conf", "rig_case": "c000", "job_id": "job (test input)",
               "image": "aces-golden:0.1", "image_id": img, "solver_validation": validation, "validation_image_id": vimg}
    return {"ran": status not in ("ccx-not-found", "too-large"), "passed": passed if status == "ok" else False,
            "status": status, "case_id": case_id, "host": host, "mode": "auto",
            "reason": reason or ("agrees within its bands and meets every constraint" if passed else
                                 "compliance_Nmm conforming 1.3 vs voxel 1 (+30.0 %) exceeds the 10 % band (test input)"),
            "h_mm": 2.0, "source_case": "v001", "reference": {"case_id": "x001", "h_mm": 2.0, "host": host},
            "record": f"{case_id}/conforming.json", "mesh": None, "solver": None, "bands": None, "rows": rows,
            "constraints_met": met if status == "ok" else None, "rig": rig, "rig_attempt": attempt}


def rig_block(*, ok=True, integrity=None, mismatches=None) -> dict:
    return {"lane": "sim/pool", "url": "http://127.0.0.1:1/mcp", "scope": {"sweep": False, "check": True,
                                                                            "conforming": True},
            "token_source": "not set", "principal": "anonymous", "policy": "full", "server": {}, "session":
                "rig/session.json", "session_sha256": "0" * 64, "solvers": {}, "experiments": [],
            "code_identity": {"ok": ok, "golden_compute_sha256": {"desktop": "a" * 64, "rig": ["a" * 64]},
                              "modules_checked": 3, "mismatches": list(mismatches or []), "desktop_modules": {}},
            "integrity_error": integrity}


class _Guarded(unittest.TestCase):
    def tearDown(self):
        self.assertEqual(fake_simlab.BLOCKED, [])  # nothing tried to leave the loopback


class Ladder(_Guarded):
    def grade(self, level, conforming=None, rig=None, loop=LOOP):
        return verify.grade(loop, check(level) if level is not None else None, EFF, conforming=conforming, rig=rig)

    def test_constants(self):
        self.assertEqual(golden.GRADES, ("preview-grade", "verified-desktop", "rig-verified"))
        self.assertEqual(golden.GRADE_RANK, {"preview-grade": 0, "verified-desktop": 1, "rig-verified": 2})
        self.assertIn("rig-verified", verify.SCOPE)
        self.assertIn("No grade is a sign-off", verify.SCOPE)

    def test_step_1_verification_failed(self):
        self.assertEqual(self.grade(l1(), conf(), rig_block(), loop={"passed": False}),
                         (golden.GRADE_PREVIEW, ["check skipped: verification failed"]))

    def test_step_2_level_1_did_not_run(self):
        lv = dict(l1(), ran=False, passed=None, rows=[], case_id=None,
                  reason="not run: the rig did not deliver x001: rig-timeout (the rig budget ran out)")
        self.assertEqual(self.grade(lv, conf(), rig_block()),
                         (golden.GRADE_PREVIEW, ["check skipped: the rig did not deliver x001: rig-timeout (the rig "
                                                 "budget ran out)"]))

    def test_step_3_level_1_does_not_agree(self):
        lv = l1(passed=False)
        lv["rows"][1].update(fine=0.07, rel_gap=0.4, agree=False, meets_fine=False)
        g, why = self.grade(lv, conf(), rig_block())
        self.assertEqual(g, golden.GRADE_PREVIEW)
        self.assertEqual(why, ["umax_mm +40.0 % between 4 and 2 mm exceeds 10 %", "umax_mm 0.07 exceeds 0.06 at 2 mm"])

    def test_step_4_the_conforming_cap_on_the_rig_and_on_the_desktop(self):
        for host in ("rig", "desktop"):
            with self.subTest(host):
                g, why = self.grade(l1(), conf(host=host, passed=False, met=False), rig_block())
                self.assertEqual((g, why), (golden.GRADE_PREVIEW, [
                    f"the conforming second discretisation violates umax_mm 0.07 <= 0.06 (m001, tet10 2 mm, {host})"]))
        # a conforming check that did not finish (status not ok) never caps
        g, _ = self.grade(l1(), conf(status="equilibrium", passed=False, met=False), rig_block())
        self.assertEqual(g, golden.GRADE_VERIFIED_DESKTOP)

    def test_step_5_rig_verified(self):
        self.assertEqual(self.grade(l1(), conf(), rig_block()),
                         (golden.GRADE_RIG, [f"{BASE}, solved on the rig", CONF_OK]))

    def test_step_6_first_failing_condition(self):
        cases = {
            "a: level 1 on this desktop after the rig": (
                dict(level=l1(host="desktop", case_id="x101", attempt={"case_id": "x001", "design_status": "rig-timeout",
                                                                       "reason": "test input"})),
                "the level-1 check ran on this desktop (x101: the rig did not deliver x001: rig-timeout)"),
            "b: golden-check pending": (dict(level=l1(validation="pending")),
                                        "golden-check validation is pending in the rig registry"),
            "b: golden-check validated on another image": (
                dict(level=l1(vimg=OTHER)),
                "golden-check was validated on image 0123456789ab, this run used fake-image"),
            "c: conforming on this desktop": (
                dict(conforming=conf(host="desktop", case_id="m101",
                                     attempt={"case_id": "m001", "design_status": "rig-error", "reason": "test input"})),
                "the conforming check did not run on the rig (m101 ran on this desktop: the rig did not deliver m001: "
                "rig-error)"),
            "c: conforming not run": (
                dict(conforming=verify.conforming_not_run("not run: the rig did not deliver m001: rig-error (test "
                                                          "input)", mode="auto", h_mm=2.0, case_id="m001",
                                                          host="rig")),
                "the conforming check did not run on the rig (the rig did not deliver m001: rig-error (test input))"),
            "c: conforming does not agree": (
                dict(conforming=conf(passed=False)),
                "the conforming check does not agree (compliance_Nmm conforming 1.3 vs voxel 1 (+30.0 %) exceeds the "
                "10 % band (test input))"),
            "c: conforming failed on the rig": (
                dict(conforming=conf(status="equilibrium", passed=False, reason="equilibrium: residual 1e-3 (test "
                                                                                 "input)")),
                "the conforming check failed on the rig (equilibrium: residual 1e-3 (test input))"),
            "d: ccx-check pending": (dict(conforming=conf(validation="pending")),
                                     "ccx-check validation is pending in the rig registry"),
            "d: ccx-check validated on another image": (
                dict(conforming=conf(vimg=OTHER)),
                "ccx-check was validated on image 0123456789ab, this run used fake-image"),
            "e: code identity": (
                dict(rig=rig_block(ok=False, mismatches=[{"path": "simlab/worker.py", "desktop": "b" * 64,
                                                          "rig": "c" * 64}])),
                "code identity failed (simlab/worker.py: this desktop bbbbbbbbbbbb, the rig cccccccccccc)"),
            "e: no code identity at all": (dict(rig=rig_block(ok=False)),
                                           "code identity failed (no rig outcome carried a code identity)"),
            "f: integrity": (dict(rig=rig_block(integrity="x001: c000/golden/x001/mask.npy: sha256 differs (test "
                                                          "input)")),
                             "rig integrity error (x001: c000/golden/x001/mask.npy: sha256 differs (test input))"),
        }
        for label, (kw, reason) in cases.items():
            with self.subTest(label):
                args = {"level": l1(), "conforming": conf(), "rig": rig_block(), **kw}
                g, why = self.grade(args["level"], args["conforming"], args["rig"])
                self.assertEqual((g, why), (golden.GRADE_VERIFIED_DESKTOP, [BASE, f"not rig-verified: {reason}"]))

    def test_step_6_without_a_lane(self):
        # no lane and no conforming check: today's reason only
        none = verify.conforming_not_run("not run: no conforming check")
        self.assertEqual(self.grade(l1(host="desktop"), none, None), (golden.GRADE_VERIFIED_DESKTOP, [BASE]))
        # the conforming check ran here: one more reason, the first failing condition
        g, why = self.grade(l1(host="desktop"), conf(host="desktop"), None)
        self.assertEqual((g, why), (golden.GRADE_VERIFIED_DESKTOP, [
            BASE, "not rig-verified: the level-1 check ran on this desktop (x001: no rig lane was used)"]))

    def test_rig_failure_order(self):
        """Every condition failing at once reports (a) first; fixing them one by one walks b to f."""
        lv, cf, rb = l1(validation="pending"), conf(validation="pending"), rig_block(ok=False, integrity="x (test input)")
        order = []
        for fix in (lambda: None, lambda: lv["rig"].update(solver_validation="pass"),
                    lambda: cf["rig"].update(solver_validation="pass"), lambda: rb["code_identity"].update(ok=True),
                    lambda: rb.update(integrity_error=None)):
            fix()
            order.append(verify.rig_failure(lv, cf, rb))
        self.assertEqual(order, ["golden-check validation is pending in the rig registry",
                                 "ccx-check validation is pending in the rig registry",
                                 "code identity failed (no rig outcome carried a code identity)",
                                 "rig integrity error (x (test input))", None])


class Derive(_Guarded):
    V01 = {  # a validation.json in the v0.1 shape (aces-golden 0.1.0: no conforming or rig block; levels without host)
        "schema": "aces-golden-validation/1", "tol": 0.10, "resolution_mm": {"generator": 4.0, "field": 2.0, "solve": 4.0,
                                                                             "check": [2.0, 1.0]},
        "rounds": [{"round": 1, "case_id": "v001", "passed": True}], "outcome": "pass", "passed": True,
        "independent_check": {"ran": True, "reason": "level 1 ran", "passed": True, "levels": [
            {"level": 1, "case_id": "x001", "h_mm": 2.0, "device": "cpu", "gating": True, "est_s": 8.0, "budget_s": 60.0,
             "ran": True, "reason": "test input", "passed": True,
             "rows": [{"output": "umax_mm", "role": "constraint", "coarse": 0.05, "fine": 0.052, "rel_gap": 0.04,
                       "tol": 0.1, "agree": True, "op": "<=", "limit": 0.06, "meets_fine": True}]}]},
        "device_parity": [], "grade": "verified-desktop", "grade_reasons": [BASE], "verdict": "PASS"}

    def test_v01_files_derive_as_before(self):
        self.assertEqual(verify.derive(self.V01), ("PASS", golden.GRADE_VERIFIED_DESKTOP, [BASE]))
        failed = copy.deepcopy(self.V01)
        failed["independent_check"]["levels"][0].update(passed=False)
        failed["independent_check"]["levels"][0]["rows"][0].update(fine=0.07, meets_fine=False)
        self.assertEqual(verify.derive(failed)[1:], (golden.GRADE_PREVIEW, ["umax_mm 0.07 exceeds 0.06 at 2 mm"]))
        thin = dict(self.V01, outcome="thin-sweep", rounds=[])
        self.assertEqual(verify.derive(thin), ("THIN", golden.GRADE_PREVIEW, ["check skipped: thin sweep"]))

    def test_new_blocks_derive(self):
        val = copy.deepcopy(self.V01)
        val["independent_check"] = check(l1())
        val.update(conforming=conf(), rig=rig_block())
        self.assertEqual(verify.derive(val), ("PASS", golden.GRADE_RIG, [f"{BASE}, solved on the rig", CONF_OK]))
        val["conforming"] = conf(passed=False, met=False)
        self.assertEqual(verify.derive(val)[1], golden.GRADE_PREVIEW)


class ExitCode(_Guarded):
    """_Pipeline.final_code: --strict means verified-desktop or better; --require-grade GRADE; with both the higher
    requirement wins; a rig integrity error is folded in like the evidence error (6 when nothing worse)."""

    def code(self, grade, *, strict=False, require=None, verdict="PASS", integrity=None, evidence=None):
        p = pipeline._Pipeline.__new__(pipeline._Pipeline)
        p.opts = pipeline.Options(strict=strict, require_grade=require)
        p.validation = {"verdict": verdict, "grade": grade, "grade_reasons": [f"why {grade} (test input)"],
                        "verdict_reasons": ["stub reason (test input)"],
                        "rig": rig_block(integrity=integrity) if integrity else None}
        p.evidence_error = evidence
        p.thin_message = None
        return p.final_code()[:2]

    def test_strict_and_require_grade(self):
        P, D, R = golden.GRADE_PREVIEW, golden.GRADE_VERIFIED_DESKTOP, golden.GRADE_RIG
        self.assertEqual(self.code(P), (0, "verification passed (preview-grade)"))
        self.assertEqual(self.code(P, strict=True), (1, "strict: grade preview-grade (why preview-grade (test input))"))
        self.assertEqual(self.code(D, strict=True)[0], 0)
        self.assertEqual(self.code(R, strict=True)[0], 0)  # rig-verified is better than verified-desktop
        self.assertEqual(self.code(D, require=R),
                         (1, "required grade rig-verified: grade verified-desktop (why verified-desktop (test input))"))
        self.assertEqual(self.code(R, require=R), (0, "verification passed (rig-verified)"))
        self.assertEqual(self.code(P, require=P)[0], 0)
        self.assertEqual(self.code(D, strict=True, require=R)[1][:28], "required grade rig-verified:")
        self.assertEqual(self.code(P, strict=True, require=P)[1][:7], "strict:")  # strict is the higher requirement
        self.assertEqual(self.code(P, require=R, verdict="FAIL")[0], 1)
        self.assertEqual(self.code(P, require=R, verdict="FAIL")[1], "verification failed: stub reason (test input)")

    def test_integrity_error(self):
        self.assertEqual(self.code(golden.GRADE_RIG, integrity="x001: mask.npy (test input)"),
                         (6, "rig integrity error: x001: mask.npy (test input)"))
        code, msg = self.code(golden.GRADE_PREVIEW, verdict="FAIL", integrity="x001: mask.npy (test input)")
        self.assertEqual(code, 1)
        self.assertTrue(msg.endswith("; rig integrity error too: x001: mask.npy (test input)"), msg)
        code, msg = self.code(golden.GRADE_PREVIEW, strict=True, integrity="x001 (test input)")
        self.assertEqual(code, 1)
        ev = golden.GoldenError(golden.EXIT_EVIDENCE, "stub ledger failure (test input)")
        code, msg = self.code(golden.GRADE_RIG, integrity="x001 (test input)", evidence=ev)
        self.assertEqual((code, msg), (6, "rig integrity error: x001 (test input); evidence failed too: stub ledger "
                                          "failure (test input)"))


if __name__ == "__main__":
    unittest.main()
