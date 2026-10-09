"""Tests 19-29 and 46-52 of the spec: the operations, the pull bundle, the class gate and the guards."""
from __future__ import annotations

import hashlib
import json
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

import fake_onshape
from fake_onshape import FIXTURE_HEADER, PLATE_CODE, AdapterTestCase

from aces_onshape import mcp_server, ops
from aces_onshape import project as aces_project
from aces_onshape.errors import (
    ClassRefused,
    ProjectError,
    RateLimitError,
    ServerError,
    UsageError,
    WriteRefused,
)

ENUM_CODE = """export enum PlateCorner
{
    annotation { "Name" : "Square" }
    SQUARE,
    annotation { "Name" : "Round" }
    ROUND
}
annotation { "Feature Type Name" : "ACES plate" }
export const acesPlate = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        isLength(definition.length, NONNEGATIVE_LENGTH_BOUNDS);
        annotation { "Name" : "Corner" }
        definition.corner is PlateCorner;
    }
    {
    });
"""
FEATURE = {"btType": "BTMFeature-134", "featureType": "fixtureFeature", "name": "Fixture feature", "namespace": "",
           "parameters": []}
SCRIPT = "function(context is Context, queries is map) { return 1; }"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OpsTestCase(AdapterTestCase):

    def setUp(self):
        super().setUp()
        self.make_project("proj", "commercial")
        self.c = self.client()

    def new_document(self, session: ops.Session, name: str = "Ops (fixture)") -> tuple[str, str, str]:
        doc = ops.create_document(self.c, name, project="proj", session=session)
        ps = next(e["id"] for e in doc["elements"] if e["element_type"] == "PARTSTUDIO")
        return doc["did"], doc["wid"], ps

    def plate(self, session: ops.Session, code: str = PLATE_CODE):
        did, wid, ps = self.new_document(session)
        studio = ops.featurestudio_write(self.c, did, wid, code, project="proj", session=session, name="ACES plate")
        return did, wid, ps, studio["eid"]

    def seeded_studio(self, contents: str) -> tuple[str, str, str, str]:
        did, wid, ps = self.fake.seed_document()
        return did, wid, ps, self.fake.seed_studio(did, wid, "Seeded studio (fixture)", FIXTURE_HEADER + contents)

    def pull(self, did: str, wid: str, ps: str, **kw) -> dict:
        return ops.pull(self.c, f"onshape://{did}/{wid}/{ps}", project="proj", **kw)


class CustomFeatureTest(OpsTestCase):

    def test_19_custom_feature_in_the_workspace(self):
        s = ops.Session()
        did, wid, ps = self.new_document(s)
        w = ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project="proj", session=s, name="ACES plate")
        studio = w["eid"]
        self.assertTrue(w["created"])
        self.assertTrue(w["header_prepended"])
        self.assertTrue(w["ok"])
        self.assertTrue(self.fake.element(did, studio)["contents"].startswith(FIXTURE_HEADER))
        self.assertEqual([f["feature_type"] for f in w["feature_specs"]], ["acesPlate"])
        r = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"length": "60 mm", "width": "40 mm"},
                                   project="proj", session=s)
        mv = self.fake.element(did, studio)["microversionId"]
        stored = self.fake.feature(did, ps, r["feature_id"])
        self.assertEqual(stored["namespace"], f"e{studio}::m{mv}")
        self.assertEqual(r["namespace_source"], "partstudio_featurespecs")
        self.assertEqual(r["namespace_form"], "workspace")
        self.assertEqual(r["computed_namespace"], f"e{studio}::m{mv}")
        self.assertEqual((stored["btType"], stored["featureType"]), ("BTMFeature-134", "acesPlate"))
        params = {p["parameterId"]: p for p in stored["parameters"]}
        self.assertEqual((params["length"]["expression"], params["width"]["expression"]), ("60 mm", "40 mm"))
        self.assertIs(params["flag"]["value"], False)
        self.assertEqual(self.fake.count("POST", "/versions"), 0)
        self.assertEqual(r["feature_status"], "OK")
        self.assertIsNone(r["messages"])
        again = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"length": "70 mm"}, project="proj",
                                       session=s, replace_feature_id=r["feature_id"])
        self.assertTrue(again["replaced"])
        self.assertEqual(again["feature_id"], r["feature_id"])
        self.assertEqual(self.fake.count("POST", "/featureid/"), 1)
        self.assertEqual({p["parameterId"]: p.get("expression")
                          for p in self.fake.feature(did, ps, r["feature_id"])["parameters"]}["length"], "70 mm")

    def test_19b_enum_namespace_end_to_end(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s, ENUM_CODE)
        r = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"corner": "ROUND"}, project="proj",
                                   session=s)
        stored = {p["parameterId"]: p for p in self.fake.feature(did, ps, r["feature_id"])["parameters"]}
        self.assertEqual(stored["corner"]["value"], "ROUND")
        self.assertEqual(stored["corner"]["enumName"], "PlateCorner")
        self.assertEqual(stored["corner"]["namespace"], r["namespace"])
        sent = {p["id"]: p for p in r["parameters_sent"]}
        self.assertEqual(sent["corner"]["spec_namespace"], "")
        self.assertEqual(sent["corner"]["namespace"], r["namespace"])

    def test_20_pin_version(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        r = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"length": "60 mm"}, project="proj",
                                   session=s, pin_version=True)
        self.assertEqual(self.fake.count("POST", "/versions"), 1)
        vid = r["version_id"]
        mv = self.fake.element(did, studio, vid=vid)["microversionId"]
        self.assertEqual(r["namespace"], f"d{did}::v{vid}::e{studio}::m{mv}")
        self.assertEqual(self.fake.feature(did, ps, r["feature_id"])["namespace"], r["namespace"])
        self.assertEqual((r["namespace_source"], r["namespace_form"]), ("version", "version"))
        self.assertTrue(json.loads(self.fake.last("POST", "/versions").body)["name"].startswith("ACES pin acesPlate "))

    def test_21_unknown_feature_type(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        for pin in (False, True):
            with self.assertRaises(UsageError) as cm:
                ops.add_custom_feature(self.c, did, wid, ps, studio, "noSuchFeature", project="proj", session=s,
                                       pin_version=pin)
            self.assertIn("acesPlate (ACES plate)", str(cm.exception))
        with self.assertRaises(UsageError):
            ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"nope": 1}, project="proj", session=s,
                                   pin_version=True)
        self.assertEqual(self.fake.count("POST", "/versions"), 0)
        self.assertEqual(self.fake.count("POST", f"/e/{ps}/features"), 0)

    def test_22_error_status_and_the_messages_probe(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        self.fake.next_feature_status = "ERROR"
        self.fake.eval_result = fake_onshape.fs_value({"error": "Fixture error text"})
        r = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", project="proj", session=s)
        self.assertEqual(r["feature_status"], "ERROR")
        self.assertFalse(r["ok"])
        self.assertEqual(r["messages"], {"error": "Fixture error text"})
        self.assertIn(f'makeId("{r["feature_id"]}")', self.fake.last_eval["script"])
        self.fake.next_feature_status = "WARNING"
        self.fake.inject(400, method="POST", path_contains="/featurescript")  # 400: not retried, unlike a 5xx
        r = ops.add_feature(self.c, did, wid, ps, FEATURE, project="proj", session=s)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["messages"])
        self.assertTrue(any(w.startswith("could not read the feature's error text") for w in r["warnings"]))

    def test_23_featurestudio_write_errors_and_restore_point(self):
        s = ops.Session()
        did, wid, _ps = self.new_document(s)
        w = ops.featurestudio_write(self.c, did, wid, "SYNTAX_ERROR_FOR_TEST\n" + PLATE_CODE, project="proj",
                                    session=s)
        self.assertIn("Parse error", w["specs_error"])
        self.assertFalse(w["ok"])
        self.assertIn(ops.NO_SPECS, w["warnings"])
        self.assertEqual(w["previous_contents_sha256"], sha(FIXTURE_HEADER.encode("utf-8")))
        self.assertTrue(w["previous_microversion"])
        written = self.fake.element(did, w["eid"])["contents"]
        self.assertEqual(w["contents_sha256"], sha(written.encode("utf-8")))
        again = ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project="proj", session=s, eid=w["eid"])
        self.assertTrue(again["ok"])
        self.assertFalse(again["created"])
        self.assertEqual(again["previous_contents_sha256"], w["contents_sha256"])
        self.assertEqual(again["previous_microversion"], w["microversion"])


class PullTest(OpsTestCase):

    def test_24_pull_bundle(self):
        log = self.use_fake_aces("new")
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        f = ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", {"length": "60 mm"}, project="proj",
                                   session=s)
        r = self.pull(did, wid, ps, session=s)
        bundle = Path(r["bundle"])
        self.assertEqual(bundle.parent, Path(os.environ["ACES_WORK"]) / "proj" / "cad" / "onshape")
        self.assertTrue(re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", bundle.name), bundle.name)
        self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                         ["outcome.json", "parameters.json", "part.step", "source.json"])
        source = json.loads((bundle / "source.json").read_bytes())
        params = json.loads((bundle / "parameters.json").read_bytes())
        self.assertEqual((bundle / "part.step").read_bytes(), self.fake.step_bytes(source["translation"]["id"]))
        for name in ("part.step", "parameters.json"):
            self.assertEqual(source["files"][name]["sha256"], sha((bundle / name).read_bytes()))
        self.assertEqual(params["schema"], "aces.cad.onshape.parameters/1")
        self.assertEqual(params["features"][0]["namespace"], f["namespace"])
        studio_entry = params["feature_studios"][0]
        self.assertEqual(studio_entry["element_id"], studio)
        self.assertEqual(studio_entry["contents_sha256"], sha(studio_entry["contents"].encode("utf-8")))
        self.assertTrue(studio_entry["namespace_current"])
        self.assertEqual(studio_entry["reached_by"], [{"namespace": f["namespace"]}])
        std = [i for i in studio_entry["imports"] if i["kind"] == "std"]
        self.assertEqual(std[0]["path"], "onshape/std/common.fs")
        self.assertEqual(std[0]["reason"], "std library")
        self.assertTrue(params["feature_studios_complete"])
        self.assertIn("bodies", params["mass_properties"])
        self.assertEqual([x["state"] for x in source["translation"]["states"]], ["ACTIVE", "DONE"])
        self.assertTrue(source["microversion"]["consistent"])
        self.assertTrue(source["exported_at"].endswith("Z"))
        self.assertIs(source["document"]["public"], False)
        self.assertEqual(source["project"]["class"], "commercial")
        self.assertEqual(source["project"]["class_source"], "aces project show")
        self.assertEqual(source["ref"], f"onshape://{did}/{wid}/{ps}")
        calls = [json.loads(line) for line in log.read_bytes().decode("utf-8").splitlines()]
        records = [c for c in calls if c["argv"][:3] == ["ledger", "record", "cad.pull"]]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["argv"], ["ledger", "record", "cad.pull", "--json", "-", "--print", "json"])
        payload = json.loads(records[0]["stdin"])
        self.assertEqual(set(payload["files"]), {"part.step", "parameters.json", "source.json"})
        for name, digest in payload["files"].items():
            self.assertEqual(digest, sha((bundle / name).read_bytes()), name)
        self.assertEqual(payload["microversion_before"], source["microversion"]["before"])
        self.assertEqual(payload["microversion_after"], source["microversion"]["after"])
        self.assertEqual(payload["consistent"], source["microversion"]["consistent"])
        self.assertEqual(payload["exported_at"], source["exported_at"])
        self.assertEqual(payload["base_url"], source["base_url"])
        self.assertEqual(payload["bundle"], str(bundle))
        self.assertEqual(list(bundle.parent.glob(".*.partial")), [])
        source_bytes = (bundle / "source.json").read_bytes()
        self.assertEqual(r["files"]["source.json"]["sha256"], sha(source_bytes))
        # source.json says up front whether an OS ledger entry can be written; outcome.json holds source.json's hash
        self.assertEqual(source["ledger"], {"kind": "cad.pull", "available": True, "reason": None})
        self.assertEqual(sum(c["argv"] == ["ledger", "record", "--help"] for c in calls), 1)
        outcome = json.loads((bundle / "outcome.json").read_bytes())
        self.assertEqual(outcome["files"], {"source.json": {"sha256": sha(source_bytes), "bytes": len(source_bytes)}})
        self.assert_no_secrets_in_tree()

    def test_24b_a_lone_surrogate_from_onshape_is_written_as_its_escape(self):
        log = self.use_fake_aces("new")
        name = "Bad \ud800 name (fixture)"  # legal as the JSON escape \ud800, not encodable as UTF-8
        did, wid, ps = self.fake.seed_document(name=name)
        r = self.pull(did, wid, ps)
        raw = (Path(r["bundle"]) / "source.json").read_bytes()
        self.assertIn(b'"Bad \\ud800 name (fixture)"', raw)
        self.assertEqual(json.loads(raw)["document"]["name"], name)
        self.assertEqual(r["files"]["source.json"]["sha256"], sha(raw))
        self.assertTrue(r["ledger"]["written"])
        calls = [json.loads(line) for line in log.read_bytes().decode("utf-8").splitlines()]
        stdin = next(c["stdin"] for c in calls if c["argv"][:3] == ["ledger", "record", "cad.pull"])
        self.assertEqual(json.loads(stdin)["document_name"], name)
        step = ops.export_step(self.c, did, "w", wid, ps, project="proj")
        self.assertEqual(json.loads(Path(step["sidecar"]).read_bytes())["document"]["name"], name)

    def test_25_class_gate(self):
        did, wid, ps = self.fake.seed_document()
        ref = f"onshape://{did}/{wid}/{ps}"
        cases = [("p-sov", "sovereign", None), ("p-ctl", "controlled", None), ("p-unknown", "secret", None),
                 ("p-empty", None, "class: ")]
        for name, cls, line in cases:
            self.make_project(name, cls, class_line=line)
            before = len(self.fake.requests)
            calls = [lambda: ops.pull(self.c, ref, project=name),
                     lambda: ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project=name),
                     lambda: ops.add_feature(self.c, did, wid, ps, FEATURE, project=name),
                     lambda: ops.featurescript_eval(self.c, did, "w", wid, ps, SCRIPT, project=name)]
            for call in calls:
                with self.assertRaises(ClassRefused, msg=name) as cm:
                    call()
                self.assertEqual(cm.exception.exit_code, 4)
                self.assertIn("never for sovereign or controlled", str(cm.exception))
            self.assertEqual(len(self.fake.requests), before, name)
        with self.assertRaises(ClassRefused) as cm:
            ops.pull(self.c, ref, project="p-empty")
        self.assertIn("is class 'none'", str(cm.exception))
        with self.assertRaises(ProjectError) as cm:
            ops.pull(self.c, ref, project="no-such-project")
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertIn("aces project new no-such-project", str(cm.exception))
        before = len(self.fake.requests)
        with self.assertRaises(ProjectError):
            ops.pull(self.c, ref, project=None)
        with self.assertRaises(ProjectError) as cm:
            ops.dispatch("pull", {"ref": ref})
        self.assertIn("no ACES project given", str(cm.exception))
        self.assertEqual(len(self.fake.requests), before)

    def test_26_ledger_through_the_aces_cli(self):
        did, wid, ps = self.fake.seed_document()
        self.use_fake_aces("new")
        r = self.pull(did, wid, ps)
        self.assertTrue(r["ledger"]["written"])
        self.assertEqual(r["ledger"]["n"], 1)
        self.assertRegex(r["ledger"]["hash"], r"^[0-9a-f]{64}$")
        name = Path(r["bundle"]).name
        self.assertEqual(r["cad_source"], {"recorded": True, "ref": f"cad/onshape/{name}/part.step"})
        outcome = json.loads((Path(r["bundle"]) / "outcome.json").read_bytes())
        self.assertEqual(outcome["schema"], "aces.cad.onshape.outcome/1")
        self.assertEqual(outcome["ledger"], r["ledger"])
        self.assertEqual(outcome["cad_source"], r["cad_source"])
        self.assertTrue(outcome["updated_at"].endswith("Z"))
        self.assertEqual(aces_project.project_class("proj")["class_source"], "aces project show")
        self.use_fake_aces("old")
        r = self.pull(did, wid, ps)
        self.assertFalse(r["ledger"]["written"])
        self.assertIn("no ledger record command", r["ledger"]["reason"])
        self.assertFalse(r["cad_source"]["recorded"])
        noted = json.loads((Path(r["bundle"]) / "source.json").read_bytes())["ledger"]
        self.assertEqual((noted["available"], noted["reason"]), (False, r["ledger"]["reason"]))
        self.assertIn("the OS ledger entry was not written", noted["reason"])
        os.environ["ACES_ONSHAPE_ACES_CMD"] = "[]"
        r = self.pull(did, wid, ps)
        self.assertEqual(r["ledger"], {"written": False, "kind": "cad.pull",
                                       "reason": "no aces CLI on PATH (or ACES_ONSHAPE_ACES_CMD=[])"})
        self.assertEqual(json.loads((Path(r["bundle"]) / "outcome.json").read_bytes())["ledger"], r["ledger"])
        self.assertEqual(json.loads((Path(r["bundle"]) / "source.json").read_bytes())["ledger"],
                         {"kind": "cad.pull", "available": False,
                          "reason": "no aces CLI on PATH (or ACES_ONSHAPE_ACES_CMD=[])"})
        self.assertEqual(aces_project.project_class("proj")["class_source"], "aces.yaml")
        self.assert_no_secrets_in_tree()

    def test_27_exports(self):
        did, wid, ps = self.fake.seed_document()
        exports = Path(os.environ["ACES_WORK"]) / "proj" / "cad" / "onshape" / "exports"
        step = ops.export_step(self.c, did, "w", wid, ps, project="proj", label="v1")
        path, sidecar = Path(step["path"]), Path(step["sidecar"])
        self.assertEqual(path.parent, exports)
        self.assertTrue(re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}-v1\.step", path.name), path.name)
        side = json.loads(sidecar.read_bytes())
        self.assertEqual(side["sha256"], sha(path.read_bytes()))
        self.assertEqual(side["format"], "STEP")
        self.assertIs(side["document"]["public"], False)
        self.assertEqual(side["translation"]["id"], step["translation_id"])
        self.assertEqual(side["project"]["name"], "proj")
        stl = ops.export_stl(self.c, did, "w", wid, ps, project="proj", units="inch")
        stl_side = json.loads(Path(stl["sidecar"]).read_bytes())
        self.assertEqual(Path(stl["path"]).read_bytes(), fake_onshape.STL_FIXTURE)
        self.assertEqual(stl_side["stl"], {"units": "inch", "mode": "binary", "grouping": True})
        self.assertEqual(self.fake.last("GET", "/stl").query["units"], "inch")
        with self.assertRaises(UsageError):
            ops.export_stl(self.c, did, "w", wid, ps, project="proj", label="bad label")
        with self.assertRaises(UsageError) as cm:
            ops.export_step(self.c, did, "m", wid, ps, project="proj")
        self.assertIn("STEP export needs a workspace or a version", str(cm.exception))
        self.make_project("sov", "sovereign")
        before = len(self.fake.requests)
        with self.assertRaises(ClassRefused):
            ops.export_step(self.c, did, "w", wid, ps, project="sov")
        with self.assertRaises(ClassRefused):
            ops.export_stl(self.c, did, "w", wid, ps, project="sov")
        self.assertEqual(len(self.fake.requests), before)
        self.assertEqual(sorted(p.suffix for p in exports.iterdir()), [".json", ".json", ".step", ".stl"])
        self.assert_no_secrets_in_tree()

    def test_28_bound_project(self):
        did, wid, ps = self.fake.seed_document()
        self.make_project("sov", "sovereign")
        os.environ["ACES_ONSHAPE_PROJECT"] = "sov"
        before = len(self.fake.requests)
        code, _out, err = self.cli_main("whoami")
        self.assertEqual(code, 4, err)
        reply, _redactor = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "onshape_whoami", "arguments": {}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"]["error"]["kind"], "class_refused")
        os.environ["ACES_ONSHAPE_PROJECT"] = "proj"
        code, _out, err = self.cli_main("pull", f"onshape://{did}/{wid}/{ps}", "--project", "other")
        self.assertEqual(code, 2, err)
        self.assertIn("bound to project 'proj'", err)
        reply, _redactor = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "onshape_pull", "arguments": {"ref": f"onshape://{did}/{wid}/{ps}", "project": "other"}}})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(reply["result"]["structuredContent"]["error"]["kind"], "project")
        self.assertEqual(len(self.fake.requests), before)

    def test_29_no_secret_leaks(self):
        log = self.use_fake_aces("new")
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", project="proj", session=s)
        self.pull(did, wid, ps, session=s)
        ops.export_step(self.c, did, "w", wid, ps, project="proj")
        ops.export_stl(self.c, did, "w", wid, ps, project="proj")
        self.assertTrue(log.exists())
        self.assertGreater(len(self.files_under()), 8)
        self.assert_no_secrets_in_tree()
        for line in log.read_bytes().decode("utf-8").splitlines():
            for needle in (fake_onshape.SECRET, fake_onshape.ACCESS, fake_onshape.basic()):
                self.assertNotIn(needle, line)


class CaptureTest(OpsTestCase):

    def test_47a_stale_namespace(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", project="proj", session=s)
        ops.featurestudio_write(self.c, did, wid, PLATE_CODE + "\n// edited (fixture)\n", project="proj", session=s,
                                eid=studio)
        r = self.pull(did, wid, ps, session=s)
        params = json.loads((Path(r["bundle"]) / "parameters.json").read_bytes())
        entry = params["feature_studios"][0]
        self.assertFalse(entry["namespace_current"])
        self.assertNotEqual(entry["microversion_expected"], entry["microversion_current"])
        self.assertTrue(any("changed after the instance was created" in w for w in r["warnings"]))
        self.assertTrue(r["feature_studios_complete"])

    def test_47b_imports_are_captured(self):
        did, wid, ps = self.fake.seed_document()
        b = self.fake.seed_studio(did, wid, "Studio B (fixture)", FIXTURE_HEADER + "// B (fixture)\n")
        blob = self.fake.seed_blob(did, wid, "Data (fixture)")
        did2, wid2, _ps2 = self.fake.seed_document("Library (fixture)")
        c_eid = self.fake.seed_studio(did2, wid2, "Studio C (fixture)", FIXTURE_HEADER + "// C (fixture)\n")
        vid2 = self.fake.seed_version(did2, wid2, "Library v1 (fixture)")
        b_mv = self.fake.element(did, b)["microversionId"]
        blob_mv = self.fake.element(did, blob)["microversionId"]
        c_mv = self.fake.element(did2, c_eid, vid=vid2)["microversionId"]
        a_code = (FIXTURE_HEADER
                  + f'import(path : "{b}", version : "{b_mv}");\n'
                  + f'import(path : "{did2}/{vid2}/{c_eid}", version : "{c_mv}");\n'
                  + f'import(path : "{blob}", version : "{blob_mv}");\n' + PLATE_CODE)
        a = self.fake.seed_studio(did, wid, "Studio A (fixture)", a_code)
        ops.add_custom_feature(self.c, did, wid, ps, a, "acesPlate", project="proj")
        r = self.pull(did, wid, ps)
        params = json.loads((Path(r["bundle"]) / "parameters.json").read_bytes())
        studios = {e["element_id"]: e for e in params["feature_studios"]}
        self.assertEqual(set(studios), {a, b, c_eid})
        imports = {i["path"]: i for i in studios[a]["imports"]}
        self.assertEqual(imports["onshape/std/common.fs"]["kind"], "std")
        self.assertEqual((imports[b]["kind"], imports[b]["fetched"], imports[b]["current"]), ("workspace", True, True))
        linked = imports[f"{did2}/{vid2}/{c_eid}"]
        self.assertEqual((linked["kind"], linked["fetched"], linked["current"]), ("linked", True, True))
        self.assertEqual((imports[blob]["fetched"], imports[blob]["reason"]), (False, "not a Feature Studio (BLOB)"))
        self.assertEqual(studios[c_eid]["read_from"], f"v/{vid2}")
        self.assertEqual(studios[c_eid]["document_id"], did2)
        self.assertEqual(studios[b]["reached_by"][0]["imported_by"], a)
        self.assertEqual(studios[c_eid]["reached_by"][0]["imported_by"], a)
        self.assertIn("// C (fixture)", studios[c_eid]["contents"])
        self.assertTrue(params["feature_studios_complete"])

    def test_47c_depth_limit(self):
        did, wid, ps = self.fake.seed_document()
        chain = []
        previous = None
        for n in range(4, -1, -1):
            body = FIXTURE_HEADER
            if previous is not None:
                body += f'import(path : "{previous}", version : "{self.fake.element(did, previous)["microversionId"]}");\n'
            if n == 0:
                body += PLATE_CODE
            previous = self.fake.seed_studio(did, wid, f"Studio {n} (fixture)", body + f"// level {n} (fixture)\n")
            chain.insert(0, previous)
        ops.add_custom_feature(self.c, did, wid, ps, chain[0], "acesPlate", project="proj")
        r = self.pull(did, wid, ps)
        params = json.loads((Path(r["bundle"]) / "parameters.json").read_bytes())
        captured = [e["element_id"] for e in params["feature_studios"]]
        self.assertEqual(captured, chain[:4])
        last = {i["path"]: i for i in params["feature_studios"][3]["imports"]}[chain[4]]
        self.assertEqual((last["fetched"], last["reason"]), (False, "depth limit 3"))
        self.assertFalse(params["feature_studios_complete"])
        self.assertFalse(r["feature_studios_complete"])


class NamespaceAndGuardTest(OpsTestCase):

    def test_46_namespace_preference(self):
        s = ops.Session()
        did, wid, ps, studio = self.plate(s)
        other = self.fake.seed_studio(did, wid, "Other studio (fixture)", FIXTURE_HEADER)

        def add(**kw):
            return ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", project="proj", session=s, **kw)

        computed = f"e{studio}::m{self.fake.element(did, studio)['microversionId']}"
        self.fake.partstudio_spec_namespace[studio] = f"e{studio}::m{'0' * 24}"                     # (a)
        r = add()
        self.assertEqual((r["namespace"], r["namespace_source"]), (f"e{studio}::m{'0' * 24}", "partstudio_featurespecs"))
        self.assertEqual(r["computed_namespace"], computed)
        self.fake.partstudio_spec_namespace[studio] = ""                                              # (b)
        r = add()
        self.assertEqual((r["namespace"], r["namespace_source"]), (computed, "computed"))
        self.assertEqual(r["partstudio_featurespec_namespace"], "")
        self.fake.partstudio_spec_namespace[studio] = f"e{other}::m{'1' * 24}"                       # (c)
        r = add()
        self.assertEqual((r["namespace"], r["namespace_source"]), (computed, "computed"))
        self.assertEqual(r["partstudio_featurespec_namespace"], f"e{other}::m{'1' * 24}")
        del self.fake.partstudio_spec_namespace[studio]                                               # (d)
        self.fake.inject(400, path_contains="/partstudios/", method="GET")
        r = add()
        self.assertEqual((r["namespace"], r["namespace_source"]), (computed, "computed"))
        self.assertIn("HTTP 400", r["partstudio_featurespecs_error"])
        r = add(pin_version=True)                                                                     # (e)
        self.assertEqual((r["namespace_form"], r["namespace_source"]), ("version", "version"))
        self.assertTrue(r["namespace"].startswith(f"d{did}::v{r['version_id']}::e{studio}::m"))
        self.assertEqual(r["partstudio_featurespec_namespace"], computed)

    def test_48_ownership_and_public_guards(self):
        did, wid, ps = self.fake.seed_document()
        studio = self.fake.seed_studio(did, wid, "Seeded studio (fixture)", FIXTURE_HEADER + PLATE_CODE)
        fid = self.fake.seed_feature(did, wid, ps, FEATURE)
        s = ops.Session()
        before = len(self.fake.requests)
        refused = [
            lambda **kw: ops.update_feature(self.c, did, wid, ps, fid, FEATURE, project="proj", session=s, **kw),
            lambda **kw: ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project="proj", session=s, eid=studio,
                                                 **kw),
            lambda **kw: ops.add_custom_feature(self.c, did, wid, ps, studio, "acesPlate", project="proj", session=s,
                                                replace_feature_id=fid, **kw),
            lambda **kw: ops.delete_feature(self.c, did, wid, ps, fid, project="proj", session=s, **kw),
        ]
        for call in refused:
            with self.assertRaises(WriteRefused) as cm:
                call()
            self.assertEqual(cm.exception.exit_code, 2)
            self.assertIn("was not created by this aces-onshape process", str(cm.exception))
        self.assertEqual(len(self.fake.requests), before)
        for call in refused:
            call(confirm_foreign=True)
        self.assertIsNone(self.fake.feature(did, ps, fid))
        own = ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project="proj", session=s)
        ops.featurestudio_write(self.c, did, wid, PLATE_CODE + "// again (fixture)\n", project="proj", session=s,
                                eid=own["eid"])
        added = ops.add_feature(self.c, did, wid, ps, FEATURE, project="proj", session=s)
        ops.update_feature(self.c, did, wid, ps, added["feature_id"], FEATURE, project="proj", session=s)
        ops.delete_feature(self.c, did, wid, ps, added["feature_id"], project="proj", session=s)
        s2 = ops.Session()
        doc = ops.create_document(self.c, "Owned (fixture)", project="proj", session=s2)
        ps2 = next(e["id"] for e in doc["elements"] if e["element_type"] == "PARTSTUDIO")
        seeded = self.fake.seed_feature(doc["did"], doc["wid"], ps2, FEATURE)
        ops.update_feature(self.c, doc["did"], doc["wid"], ps2, seeded, FEATURE, project="proj", session=s2)
        self.fake.force_public = True
        s3 = ops.Session()
        public = ops.create_document(self.c, "Public (fixture)", project="proj", session=s3)
        self.assertTrue(public["public"])
        self.assertIn(ops.PUBLIC_CREATED, public["warnings"])
        gets = self.fake.count("GET", f"/documents/{public['did']}")
        with self.assertRaises(WriteRefused) as cm:
            ops.featurestudio_write(self.c, public["did"], public["wid"], PLATE_CODE, project="proj", session=s3)
        self.assertIn("is public", str(cm.exception))
        self.assertEqual(self.fake.count("GET", f"/documents/{public['did']}"), gets)
        written = ops.featurestudio_write(self.c, public["did"], public["wid"], PLATE_CODE, project="proj",
                                          session=s3, allow_public=True)
        self.assertTrue(written["ok"])

    def test_49_export_names_never_collide(self):
        did, wid, ps = self.fake.seed_document()
        now = datetime(2026, 10, 9, 12, 34, 56, tzinfo=timezone.utc)
        first = ops.export_stl(self.c, did, "w", wid, ps, project="proj", now=now)
        second = ops.export_stl(self.c, did, "w", wid, ps, project="proj", now=now)
        self.assertEqual(Path(first["path"]).name, f"20261009T123456Z-{ps[:8]}.stl")
        self.assertEqual(Path(second["path"]).name, f"20261009T123456Z-{ps[:8]}-2.stl")
        self.assertEqual(Path(second["sidecar"]).name, f"20261009T123456Z-{ps[:8]}-2.stl.json")
        self.assertEqual(json.loads(Path(first["sidecar"]).read_bytes())["exported_at"], "2026-10-09T12:34:56Z")
        self.assertTrue(Path(first["sidecar"]).exists() and Path(second["sidecar"]).exists())

    def test_50_element_type_filter(self):
        did, wid, ps = self.fake.seed_document()
        self.fake.seed_studio(did, wid, "Studio (fixture)", FIXTURE_HEADER)
        r = ops.elements(self.c, did, "w", wid, element_type="partstudio")
        self.assertEqual([e["id"] for e in r["elements"]], [ps])
        self.assertEqual(self.fake.last("GET", "/elements").query, {})
        self.assertEqual(len(ops.elements(self.c, did, "w", wid)["elements"]), 3)
        with self.assertRaises(UsageError):
            ops.elements(self.c, did, "w", wid, element_type="SKETCH")

    def test_51_specs_error_classification(self):
        s = ops.Session()
        did, wid, _ps, studio = self.plate(s)

        def write():
            return ops.featurestudio_write(self.c, did, wid, PLATE_CODE, project="proj", session=s, eid=studio)

        self.fake.inject(400, method="GET", path_contains="/featurespecs")
        w = write()
        self.assertIn("HTTP 400", w["specs_error"])
        self.assertFalse(w["ok"])
        self.fake.inject(503, method="GET", path_contains="/featurespecs", count=4)
        with self.assertRaises(ServerError) as cm:
            write()
        self.assertEqual(cm.exception.kind, "server")
        self.assertEqual(self.sleeps, [1.0, 2.0, 4.0])
        self.fake.inject(429, method="GET", path_contains="/featurespecs", headers={"Retry-After": "3600"})
        with self.assertRaises(RateLimitError):
            write()

    def test_52_aces_yaml_fallback(self):
        self.make_project("twice", "commercial", class_line="class: commercial\nclass: sovereign")
        with self.assertRaises(ProjectError) as cm:
            aces_project.check_cloud_allowed("twice")
        self.assertIn("has 2 top-level class lines", str(cm.exception))
        self.make_project("quoted", None, class_line='class: "commercial"')
        info = aces_project.check_cloud_allowed("quoted")
        self.assertEqual((info["class"], info["class_source"]), ("commercial", "aces.yaml"))
        self.make_project("nulled", None)
        with self.assertRaises(ClassRefused):
            aces_project.check_cloud_allowed("nulled")
        self.make_project("escaped", None, class_line='class: "comm\\"ercial"')
        with self.assertRaises(ClassRefused) as cm:
            aces_project.check_cloud_allowed("escaped")
        self.assertIn("is class 'comm\"ercial'", str(cm.exception))

    def test_52b_the_aces_cli_reads_the_project_the_adapter_writes_to(self):
        # ACES_WORK set but empty: aces-base reads it as the current directory, the adapter as ~/Work. The class has to
        # come from ~/Work/<name>, the directory the files would go to.
        self.use_fake_aces("new")
        default_work, here = Path(os.path.expanduser("~")) / "Work", self.tmp / "cwd"
        for root, project, cls in ((default_work, "demo", "sovereign"), (here, "demo", "personal"),
                                   (default_work, "demo2", "personal"), (here, "demo2", "sovereign")):
            os.environ["ACES_WORK"] = str(root)
            self.make_project(project, cls)
        os.environ["ACES_WORK"] = ""
        previous = os.getcwd()
        os.chdir(here)
        try:
            before = len(self.fake.requests)
            with self.assertRaises(ClassRefused) as cm:
                ops.pull(self.c, f"onshape://{'a' * 24}/{'b' * 24}/{'c' * 24}", project="demo")
            self.assertIn("project 'demo' is class 'sovereign'", str(cm.exception))
            self.assertEqual(len(self.fake.requests), before)
            info = aces_project.check_cloud_allowed("demo2")
        finally:
            os.chdir(previous)
        self.assertEqual((info["class"], info["class_source"], info["dir"]),
                         ("personal", "aces project show", str(default_work / "demo2")))


if __name__ == "__main__":
    unittest.main()
