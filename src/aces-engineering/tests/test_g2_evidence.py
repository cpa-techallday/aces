"""B2 unit tests: golden.evidence (experiment, solver digest, compute digest, anchor, record into both ledgers, the
thirteen-check audit, tamper detection, a pre-broken ledger). The run directory is synthetic: its "solves" come from the fake runner of
test_g2_verify (an analytic stand-in, test input), but the surrogate, optimiser, validation, compute table, simlab
bundle, simlab ledger and report are the real code paths. The aces ledger is an in-process stand-in with aces-base's
record format and hash chain; TestRealAcesCli repeats the record and audit through B3's AcesCli and the aces-base CLI
when they are available (ACES_CMD = this Python running src/aces-base) and is skipped otherwise.
Run: python -m unittest discover -s tests -p "test_g2_evidence.py"."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import test_g2_compute as tc
import test_g2_verify as tv

from aces_engineering import golden
from aces_engineering.golden import compute, evidence, report, verify

ACES_BASE = Path(__file__).resolve().parents[2] / "aces-base"
GENESIS = "0" * 64


class FakeAces:
    """In-process stand-in for golden.acescli.AcesCli over aces-base's ledger format (test fixture): the same record
    fields and the same hash chain (sha256 of prev + canonical JSON of the record without its hash)."""

    def __init__(self, state_dir):
        self.path = Path(state_dir) / "ledger.jsonl"
        self.evidence: list[tuple[str, str]] = []
        self.components: list[tuple[str, str]] = []
        self.fail_record = False

    @staticmethod
    def _hash(prev: str, rec: dict) -> str:
        return hashlib.sha256((prev + json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
                              .encode("utf-8")).hexdigest()

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def ledger_verify(self) -> dict:
        prev = GENESIS
        es = self.entries()
        for n, e in enumerate(es, 1):
            if e.get("prev") != prev or self._hash(prev, {k: v for k, v in e.items() if k != "hash"}) != e.get("hash"):
                return {"ok": False, "entries": n, "message": f"entry {n}: hash does not match", "path": str(self.path)}
            prev = e["hash"]
        return {"ok": True, "entries": len(es), "message": "chain intact" if es else "ledger empty", "path": str(self.path)}

    def ledger_record(self, kind: str, payload_path) -> dict:
        if self.fail_record:
            raise RuntimeError("aces: ledger record refused (test input)")
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        es = self.entries()
        prev = es[-1]["hash"] if es else GENESIS
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
               "operator": getpass.getuser(), "host": socket.gethostname(), "payload": payload, "prev": prev}
        rec["hash"] = self._hash(prev, rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"recorded": True, "n": len(es) + 1, "kind": kind, "ts": rec["ts"], "hash": rec["hash"], "prev": prev,
                "path": str(self.path)}

    def ledger_find(self, kind: str) -> list[dict]:
        return [dict(e, n=i + 1) for i, e in enumerate(self.entries()) if e.get("kind") == kind]

    def add_evidence(self, name: str, ref: str) -> None:
        self.evidence.append((name, ref))

    def add_component(self, name: str, component: str) -> None:
        self.components.append((name, component))


def build_run(root, *, aces, evidence_roles=(), record=True, charts=False, parity_bias=0.0,
              run_id="20261009T000000Z-b2test"):
    """A complete synthetic golden run directory (test input solves), recorded into the ledgers and reported, with
    run.json, result.json and manifest.sha256 written the way the pipeline writes them. Returns (run, eff, val)."""
    run = tc.FakeRun(root, run_id=run_id)
    eff = tv.full_eff()
    eff["derived"]["rounds"] = 2
    golden.write_text(run.path("inputs", "spec.json"),
                      json.dumps({"schema": "aces-golden-spec/1", "name": "b2-test", "note": "test input"}) + "\n")
    run.write_json("inputs/spec.effective.json", eff)
    run.write_text("inputs/domain.step", "ISO-10303-21;\n/* test input: not a real STEP */\nEND-ISO-10303-21;\n")
    run.write_json("inputs/domain.json", {"schema": golden.SCHEMA["inputs"], "source": "build123d",
                                          "step": "inputs/domain.step",
                                          "step_sha256": golden.sha256_file(run.path("inputs", "domain.step")),
                                          "note": "test input"})
    run.write_json("domain/domain.json", {"schema": golden.SCHEMA["domain"], "mask_sha256": "test-input",
                                          "shape": [25, 25, 10], "h_gen_mm": 4.0, "design_share": 0.7824,
                                          "stress_exclude": [], "n_void_boxes": 0, "stats": {"cells": 6250}})
    run.write_json("domain/mesh.json", {"elem_mm": 4.0, "cells": 6250, "solid_cells": 4890, "wall_s": 0.0,
                                        "volume_ratio_voxel_over_brep": 0.996, "watertight": True})
    stages = []
    run.write_json("run.json", {"schema": golden.SCHEMA["run"], "run_id": run.run_id, "project": run.project,
                                "exp_id": run.exp_id, "created_utc": golden.utc_now(), "status": "running",
                                "tool": {"name": "aces-golden", "version": golden.VERSION,
                                         "argv": ["aces-golden", "run", "--spec", "test-input.json"]},
                                "stages": stages, "timings": {}})
    det = compute.detect(device="cpu")
    plan = compute.plan(det, eff, jobs=1, device="cpu")
    if parity_bias:
        plan["parity"] = [{"case_id": "p001", "device": "gpu", "backend": "adapter"}]  # as on a GPU host (test)
    run.write_json("compute.json", {**det, "plan": plan})
    project = {"name": run.project, "company": "", "rd_activity": "", "hypothesis": ""}
    exp = evidence.experiment(eff, run, project, "b2-test", plan=plan, detected=det)
    run.write_json("spec.resolved.json", exp.model_dump(mode="json"))
    fake = tv.FakeCaseModule(write_evidence=set(evidence_roles), parity_bias=parity_bias)
    with tv.case_module(fake):
        tv.run_sweep(run, eff, fake, experiment=exp)
        loop = verify.run_rounds(run, eff, {}, exp, None, plan, golden.NullProgress())
        levels = compute.plan_check(det, eff, coarse_solve_s=0.5, device="cpu")
        check = verify.independent_check(run, eff, {}, exp, loop, levels, None, golden.NullProgress())
        parity = verify.device_parity(run, eff, {}, exp, loop, plan, golden.NullProgress())
        val = verify.finalise(run, eff, loop, check, parity, verify.grade(loop, check, eff))
    label = "winner" if val["verdict"] == "PASS" else "candidate"
    res = run.read_json(f"{val['final_case']}/results.json") or {}
    run.write_json(f"{label}/{label}.json", {
        "schema": golden.SCHEMA["winner"], "label": label, "case_id": val["final_case"], "params": res.get("design"),
        "metrics": {k: res.get(k) for k in golden.METRICS}, "grade": val["grade"], "verdict": val["verdict"],
        "stl": None, "mass_kg": res.get("mass_kg"), "density": None, "renders": {},
        "step": {"written": False, "reason": "test input: the fake runner makes no part"}})
    if charts:
        report.charts(run)
    compute.ComputeLog().from_run(run, det, plan).write(run)
    for name in ("compute", "inputs", "domain", "sweep", "loop", "check", "export"):
        stages.append({"name": name, "status": "done", "started_utc": None, "wall_s": 0.5, "note": ""})
    meta = run.read_json("run.json")
    meta["stages"] = stages
    run.write_json("run.json", meta)
    if record:
        evidence.record(run, eff, project, aces, operator="b2-test", note="unit test", verdict=val["verdict"],
                        outcome=val["outcome"])
        meta["stages"].append({"name": "evidence", "status": "done", "started_utc": None, "wall_s": 0.5, "note": ""})
        run.write_json("run.json", meta)
    finish_run(run, val)
    return run, eff, val


def finish_run(run, val: dict) -> None:
    """What the pipeline does after evidence (test fixture): report.*, run.json closed with a total, result.json, and
    manifest.sha256 over every file except itself and log.txt."""
    report.write(run, report.assemble(run))
    meta = run.read_json("run.json", {}) or {}
    meta.update(status="done", timings=dict(meta.get("timings") or {}, total_s=9.0))
    run.write_json("run.json", meta)
    run.write_json("result.json", {"schema": golden.SCHEMA["result"], "verdict": val["verdict"], "grade": val["grade"],
                                   "outcome": val["outcome"]})
    rows = []
    for dirpath, _dirs, files in os.walk(run.root):
        for fn in files:
            rel = (Path(dirpath) / fn).relative_to(run.root).as_posix()
            if rel not in ("manifest.sha256", "log.txt"):
                rows.append(f"{golden.sha256_file(run.root / rel)} *{rel}" + chr(10))
    run.write_text("manifest.sha256", "".join(sorted(rows, key=lambda r: r.split(" *", 1)[1])))


def minimal_run(root, *, run_id="20261009T000003Z-b2min"):
    """The smallest run directory record() accepts (test input): inputs, spec.resolved.json, one FINISHED sweep case,
    a THIN validation.json. No surrogate fit, so the ledger-failure tests stay fast. Returns (run, eff)."""
    run = tc.FakeRun(root, run_id=run_id)
    eff = tv.full_eff()
    golden.write_text(run.path("inputs", "spec.json"), '{"note": "test input"}\n')
    run.write_json("inputs/spec.effective.json", eff)
    run.write_text("inputs/domain.step", "ISO-10303-21;\n/* test input */\nEND-ISO-10303-21;\n")
    plan = compute.plan(tc.detected(), eff, jobs=1)
    run.write_json("compute.json", {**tc.detected(), "plan": plan})
    exp = evidence.experiment(eff, run, {}, "b2-test", plan=plan, detected=tc.detected())
    run.write_json("spec.resolved.json", exp.model_dump(mode="json"))
    run.register_case({"case_id": "d000", "role": "sweep", "volfrac": 0.2, "rmin_mm": 8.0})
    run.write_json("d000/results.json", {"case_id": "d000", "converged": True, **tv.physics(0.2, 8.0)})
    run.write_json("d000/status.json", {"status": "FINISHED", "design_status": "ok"})
    run.write_json("sweep.json", {"n": 1, "n_ok": 1, "rows": [{"case_id": "d000", "status": "FINISHED"}]})
    verify.finalise(run, eff, None, None, [], None, outcome="thin-sweep")
    compute.ComputeLog().from_run(run, tc.detected(), plan).write(run)
    return run, eff


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "evidence-root"
        self.state = base / "state"
        self.env = mock.patch.dict(os.environ, {"SIMLAB_EVIDENCE_ROOT": str(self.root), "ACES_USER_CONFIG": str(base / "cfg"),
                                                "ACES_ETC": str(base / "etc")})
        self.env.start()
        self.aces = FakeAces(self.state)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def runs_dir(self, name: str) -> Path:
        p = Path(self.tmp.name) / name
        p.mkdir()
        return p

    def ledger_lines(self) -> int:
        p = self.root / "ledger.jsonl"
        return len([ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]) if p.is_file() else 0


class TestPieces(_Env):
    def test_solver_digest_and_golden_code(self):
        dg = evidence.solver_digest()
        self.assertRegex(dg["content_sha256"] or "", r"^[0-9a-f]{64}$")
        self.assertRegex(dg["source_commit"] or "", r"^[0-9a-f]{40}$")
        self.assertRegex(dg["golden_code_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(dg["matches_snapshot"], bool)
        self.assertGreater(dg["modules_checked"], 5)
        self.assertIn("golden/evidence.py", dg["golden_code_files"])
        self.assertTrue(dg["simlab_imported_from"])

    def test_compute_digest_recipe(self):
        """compute_digest(): sha256 of the sorted `golden/<name> <LF sha256>` lines (or `golden/<name> missing`), joined
        with LF plus a final LF (section 14.7); recomputed here from the files themselves."""
        got = evidence.compute_digest()
        pkg = Path(evidence.__file__).resolve().parent
        lines, present = [], []
        for name in evidence.GOLDEN_COMPUTE_MODULES:
            f = pkg / name
            if f.is_file():
                data = f.read_bytes().replace(b"\r\n", b"\n")
                lines.append(f"golden/{name} {hashlib.sha256(data).hexdigest()}")
                present.append(f"golden/{name}")
            else:
                lines.append(f"golden/{name} missing")
        want = hashlib.sha256(("\n".join(sorted(lines)) + "\n").encode("utf-8")).hexdigest()
        self.assertEqual(got["golden_compute_sha256"], want)
        self.assertEqual(sorted(got["golden_compute_files"]), sorted(present))
        self.assertEqual(evidence.GOLDEN_COMPUTE_MODULES, ("__init__.py", "case.py", "conforming.py", "domain.py",
                                                           "fea.py", "generator.py", "organic.py", "rigside.py",
                                                           "spec.py"))
        self.assertIn("golden/rigside.py", got["golden_compute_files"])
        self.assertIn("gendesign.mg", evidence.DIGEST_MODULES)
        self.assertIn("gendesign.connectivity", evidence.DIGEST_MODULES)

    def test_anchor_exclusions(self):
        run = tc.FakeRun(self.runs_dir("anchor"))
        for rel in ("run.json", "result.json", "log.txt", "events.jsonl", "manifest.sha256", "report.md", "report.html",
                    "report.json", "evidence/bundle.json", "d000/progress.json", "d000/x.json.tmp", "spec.resolved.json",
                    "d000/generator/report.json", "d000/results.json", "inputs/spec.json"):
            run.write_text(rel, f"test input {rel}\n")
        digest = evidence.anchor(run)
        text = Path(run.path("evidence", "files.sha256")).read_text(encoding="utf-8")
        self.assertEqual(digest, golden.sha256_file(run.path("evidence", "files.sha256")))
        listed = [ln.split(" *", 1)[1] for ln in text.splitlines()]
        self.assertEqual(listed, ["d000/generator/report.json", "d000/results.json", "inputs/spec.json",
                                  "spec.resolved.json"])
        self.assertNotIn("\r", text)

    def test_experiment_fields(self):
        run = tc.FakeRun(self.runs_dir("exp"))
        eff = tv.full_eff()
        run.write_json("inputs/spec.effective.json", eff)
        det = tc.detected(gpu_usable=True, warp_usable=True)
        plan = compute.plan(det, eff, jobs=3)
        exp = evidence.experiment(eff, run, {"name": run.project, "company": "", "rd_activity": "RD-1",
                                             "hypothesis": "project hypothesis (test input)"}, "op", plan=plan,
                                  detected=det)
        self.assertEqual((exp.id, exp.physics, exp.solver), (run.exp_id, "structures", "step-structures"))
        self.assertEqual(exp.geometry.source, "step")
        self.assertEqual(exp.geometry.ref, "domain.step")
        self.assertEqual(exp.sweep, {"volfrac": [0.13, 0.38], "rmin_mm": [6.0, 12.0]})
        self.assertEqual(exp.resources.cpu_threads, 3)
        self.assertEqual(exp.resources.vram_gb, round(24000 / 1024, 1))  # the plan puts parity on the GPU
        self.assertEqual(exp.hypothesis, "test hypothesis (test input)")
        self.assertEqual(exp.rd_activity, "RD-1")
        self.assertIsNone(exp.company)
        self.assertEqual(exp.tags["golden"], golden.VERSION)
        self.assertEqual(exp.tags["generator"], "simp-organic")
        self.assertEqual(exp.tags["grade"], golden.GRADE_PREVIEW)
        self.assertEqual(len(exp.tags["vendored_snapshot"]), 12)
        self.assertEqual(exp.spec_path, str(run.path("inputs", "spec.effective.json")))
        self.assertEqual(exp.spec_sha256, golden.sha256_file(run.path("inputs", "spec.effective.json")))


class TestRecordAndAudit(_Env):
    def test_record_audit_tamper(self):
        run, eff, val = build_run(self.runs_dir("r1"), aces=self.aces, charts=True)
        self.assertEqual(val["verdict"], "PASS")
        led = run.read_json("evidence/ledgers.json")
        self.assertEqual(led["schema"], golden.SCHEMA["ledgers"])
        self.assertEqual((led["simlab"]["entry"], led["simlab"]["entries"], led["simlab"]["previous"]), (1, 1, None))
        self.assertTrue(led["simlab"]["verify"]["ok"])
        self.assertEqual(led["simlab"]["verify"]["head"], led["simlab"]["sha256"])
        self.assertEqual((led["aces"]["n"], led["aces"]["entries"]), (1, 1))
        self.assertTrue(led["aces"]["verify"]["ok"])
        self.assertEqual(self.aces.evidence, [(run.project, f"golden/{run.run_id} bundle:{led['simlab']['sha256'][:16]} PASS")])
        self.assertEqual([c for _, c in self.aces.components], list(evidence.COMPONENTS))
        bundle = run.read_json("evidence/bundle.json")
        from simlab.evidence_bundle import canonical_sha256

        self.assertEqual(canonical_sha256({k: v for k, v in bundle.items() if k != "bundle_sha256"}),
                         bundle["bundle_sha256"])
        g = bundle["golden"]
        self.assertEqual(g["schema"], golden.SCHEMA["bundle"])
        self.assertEqual(g["files_sha256"], golden.sha256_file(run.path("evidence", "files.sha256")))
        self.assertEqual((g["verdict"], g["grade"], g["preview_grade"]), ("PASS", val["grade"], True))
        self.assertEqual(g["validation"]["verdict"], "PASS")
        self.assertTrue(g["compute_used"])
        self.assertTrue(bundle["note"].startswith("PASS · "))
        self.assertTrue(bundle["note"].endswith(" · unit test"))
        self.assertNotIn("simlab #", bundle["note"])  # the bundle's own note carries no ledger positions
        payload = run.read_json("evidence/aces-ledger-payload.json")
        self.assertLess(len(golden.canonical_json(payload).encode("utf-8")), 4096)
        self.assertEqual(payload["bundle_sha256"], bundle["bundle_sha256"])
        self.assertEqual(self.aces.ledger_find(golden.LEDGER_KIND)[0]["payload"], payload)
        self.assertTrue(Path(run.path("evidence", "record.md")).is_file())
        res = evidence.audit(run, self.aces)
        self.assertEqual([c["name"] for c in res["checks"]],
                         ["files", "bundle", "simlab-ledger", "aces-ledger", "sweep", "surrogate", "verdict", "report",
                          "compute", "rig", "conforming", "manifest", "resolve"])
        self.assertTrue(res["ok"], [c for c in res["checks"] if not c["ok"]])
        checks = {c["name"]: c for c in res["checks"]}
        self.assertEqual((checks["rig"]["ok"], checks["rig"]["detail"]), (None, "not run: no rig work in this run"))
        self.assertIsNone(checks["conforming"]["ok"])
        self.assertTrue(checks["conforming"]["detail"].startswith("not run"), checks["conforming"]["detail"])
        # a desktop-only run: no rig block in the bundle or the payload; the conforming block says it did not run
        self.assertIsNone(g["rig"])
        self.assertEqual(g["conforming"], val["conforming"])
        self.assertFalse(g["conforming"]["ran"])
        self.assertIsNone(payload["rig"])
        self.assertEqual(payload["conforming"], {"ran": False, "passed": None, "host": None})

        # a second run chains onto the first: positions recorded
        run2, eff2 = minimal_run(self.runs_dir("r2"), run_id="20261009T000001Z-b2test")
        led2 = evidence.record(run2, eff2, {}, self.aces, operator="b2-test", note=None, verdict="THIN",
                               outcome="thin-sweep")
        self.assertEqual((led2["simlab"]["entry"], led2["simlab"]["previous"]), (2, led["simlab"]["sha256"]))
        self.assertEqual(led2["aces"]["n"], 2)
        self.assertTrue(led2["simlab"]["verify"]["ok"])
        self.assertTrue(evidence.audit(run, self.aces)["ok"])  # the first run still audits clean

        # after a golden code change the compute table is not re-derived (INFO), so an untampered run still verifies
        newer = {"golden_code_sha256": "f" * 64, "golden_code_files": []}
        with mock.patch.object(evidence, "golden_code", return_value=newer):
            res = evidence.audit(run, self.aces, use_model=False)
        comp = next(c for c in res["checks"] if c["name"] == "compute")
        self.assertIsNone(comp["ok"])
        self.assertTrue(comp["detail"].startswith("not re-derived: the golden code changed since the run (recorded "))
        self.assertTrue(res["ok"], [c for c in res["checks"] if c["ok"] is False])

        def audit_copy(name, mutate):
            dst = Path(self.tmp.name) / name
            shutil.copytree(run.root, dst)
            copy = tc.FakeRun(dst, run_id=run.run_id)
            mutate(copy)
            return {c["name"]: c for c in evidence.audit(copy, self.aces, use_model=False)["checks"]}

        def edit_results(copy):
            p = copy.path("d000", "results.json")
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc["vm_p99_MPa"] *= 0.9
            golden.dump_json(p, doc)

        checks = audit_copy("t-results", edit_results)
        self.assertFalse(checks["files"]["ok"])
        self.assertIn("d000/results.json changed", checks["files"]["detail"])
        self.assertFalse(checks["sweep"]["ok"])

        def edit_bundle(copy):
            p = copy.path("evidence", "bundle.json")
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc["note"] = "edited (test input)"
            p.write_text(json.dumps(doc, indent=2), encoding="utf-8")

        checks = audit_copy("t-bundle", edit_bundle)
        self.assertFalse(checks["bundle"]["ok"])
        self.assertIn("self-hash", checks["bundle"]["detail"])

        def edit_golden_and_rehash(copy):  # a careful forger edits the golden section and re-hashes the copy
            from simlab.evidence_bundle import canonical_sha256

            p = copy.path("evidence", "bundle.json")
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc["golden"]["verdict"] = "FAIL"
            doc["bundle_sha256"] = canonical_sha256({k: v for k, v in doc.items() if k != "bundle_sha256"})
            p.write_text(json.dumps(doc, indent=2), encoding="utf-8")

        checks = audit_copy("t-golden", edit_golden_and_rehash)
        self.assertFalse(checks["bundle"]["ok"])  # no longer the ledger's bundle
        self.assertFalse(checks["verdict"]["ok"])

        # the golden section edited in the simlab root itself breaks the chain
        bundle_path = Path(led["simlab"]["path"])
        doc = json.loads(bundle_path.read_text(encoding="utf-8"))
        doc["golden"]["grade"] = golden.GRADE_VERIFIED_DESKTOP if val["grade"] != golden.GRADE_VERIFIED_DESKTOP \
            else golden.GRADE_PREVIEW
        bundle_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        checks = {c["name"]: c for c in evidence.audit(run, self.aces, use_model=False)["checks"]}
        self.assertFalse(checks["simlab-ledger"]["ok"])
        self.assertFalse(checks["bundle"]["ok"])

        # ...and a run recorded after that refuses: exit 6, nothing appended to either ledger
        before_simlab, before_aces = self.ledger_lines(), len(self.aces.entries())
        run3, eff3 = minimal_run(self.runs_dir("r3"), run_id="20261009T000002Z-b2test")
        with self.assertRaises(golden.GoldenError) as cm:
            evidence.record(run3, eff3, {}, self.aces, operator="b2-test", note=None, verdict="THIN",
                            outcome="thin-sweep")
        self.assertEqual(cm.exception.code, golden.EXIT_EVIDENCE)
        self.assertIn("the simlab ledger does not verify before this run", cm.exception.message)
        self.assertIn("nothing was appended", cm.exception.message)
        self.assertEqual((self.ledger_lines(), len(self.aces.entries())), (before_simlab, before_aces))
        self.assertFalse((Path(self.tmp.name) / "r3" / "evidence" / "files.sha256").exists())

    def test_broken_aces_ledger_and_failed_record(self):
        run_dir = self.runs_dir("a1")
        self.aces.path.parent.mkdir(parents=True, exist_ok=True)
        self.aces.path.write_text(json.dumps({"ts": "x", "kind": "policy.mode", "payload": {}, "prev": GENESIS,
                                              "hash": "f" * 64}) + "\n", encoding="utf-8")  # a broken chain (test)
        run1, eff1 = minimal_run(run_dir)
        with self.assertRaises(golden.GoldenError) as cm:
            evidence.record(run1, eff1, {}, self.aces, operator="b2-test", note=None, verdict="THIN",
                            outcome="thin-sweep")
        self.assertEqual(cm.exception.code, golden.EXIT_EVIDENCE)
        self.assertIn("the aces ledger does not verify before this run", cm.exception.message)
        self.assertEqual(self.ledger_lines(), 0)
        self.aces.path.unlink()
        self.aces.fail_record = True
        run2, eff2 = minimal_run(self.runs_dir("a2"))
        with self.assertRaises(golden.GoldenError) as cm:
            evidence.record(run2, eff2, {}, self.aces, operator="b2-test", note=None, verdict="THIN",
                            outcome="thin-sweep")
        self.assertEqual(cm.exception.code, golden.EXIT_EVIDENCE)
        self.assertIn("aces ledger record golden.run --json", cm.exception.hint)
        self.assertEqual(self.ledger_lines(), 1)  # the simlab bundle was recorded; the replay command says how to finish
        led = json.loads((Path(self.tmp.name) / "a2" / "evidence" / "ledgers.json").read_text(encoding="utf-8"))
        self.assertEqual(led["simlab"]["entry"], 1)
        self.assertIn("refused", led["aces"]["error"])


def _real_aces_cmd() -> list[str] | None:
    return [sys.executable, "-m", "aces.cli"] if (ACES_BASE / "aces" / "cli.py").is_file() else None


class TestRealAcesCli(_Env):
    """The same record and audit through B3's AcesCli and the real aces-base CLI (skipped when either is missing)."""

    def test_record_and_audit_with_the_aces_command(self):
        try:
            from aces_engineering.golden.acescli import AcesCli
        except ImportError:
            self.skipTest("golden.acescli (B3) is not importable here")
        cmd = _real_aces_cmd()
        if cmd is None:
            self.skipTest("src/aces-base is not next to this test tree")
        work = Path(self.tmp.name) / "work"
        env = {"ACES_CMD": json.dumps(cmd), "ACES_STATE": str(self.state), "ACES_WORK": str(work),
               "ACES_ETC": str(ACES_BASE / "etc"),
               "PYTHONPATH": os.pathsep.join([str(ACES_BASE), os.environ.get("PYTHONPATH", "")])}
        with mock.patch.dict(os.environ, env):
            aces = AcesCli()
            if not (aces.available() and aces.supports_record()):
                self.skipTest("this aces has no `ledger record` (aces-base >= 0.1.0-3 needed)")
            aces.project_new("b2-test", hypothesis="test hypothesis (test input)")
            run, eff = minimal_run(self.runs_dir("real"))
            val = run.read_json("validation.json")
            led = evidence.record(run, eff, {"name": "b2-test"}, aces, operator="b2-test", note=None,
                                  verdict=val["verdict"], outcome=val["outcome"])
            self.assertEqual(led, run.read_json("evidence/ledgers.json"))
            finish_run(run, val)
            self.assertTrue(led["aces"]["verify"]["ok"])
            self.assertEqual(led["project"]["messages"], [])
            shown = aces.project_show("b2-test") or {}
            self.assertIn(f"golden/{run.run_id} bundle:{led['simlab']['sha256'][:16]} {val['verdict']}",
                          shown.get("evidence") or [])
            res = evidence.audit(run, aces)
            self.assertTrue(res["ok"], [c for c in res["checks"] if not c["ok"]])


if __name__ == "__main__":
    unittest.main()
