"""Tests 30-33 and 53-55 of the spec: the aces-onshape CLI, its exit codes, redaction and the test isolation."""
from __future__ import annotations

import json
import os
import secrets
import unittest
from unittest import mock

import fake_onshape
from fake_onshape import ACCESS, FIXTURE_HEADER, PLATE_CODE, SECRET, AdapterTestCase

from aces_onshape import client as C

FEATURE = {"btType": "BTMFeature-134", "featureType": "fixtureFeature", "name": "Fixture feature", "namespace": "",
           "parameters": []}


class CliTest(AdapterTestCase):

    def setUp(self):
        super().setUp()
        self.make_project("proj", "commercial")

    def test_30_whoami_without_credentials(self):
        del os.environ["ONSHAPE_ACCESS_KEY"]
        del os.environ["ONSHAPE_SECRET_KEY"]
        code, out, err = self.cli_main("whoami")
        self.assertEqual(code, 3, err)
        self.assertIn("ONSHAPE_ACCESS_KEY", err)
        self.assertIn("onshape.env", err)
        self.assertIn("no Onshape API keys", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        self.assertEqual(len(self.fake.requests), 0)

    def test_31_whoami_json_and_verbose(self):
        code, out, err = self.cli_main("whoami", "--json")
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertEqual(info["name"], "Fixture User")
        self.assertEqual(info["credentials_source"], "environment")
        self.assertEqual(info["base_url"], self.fake.base_url)
        code, out, err = self.cli_main("--verbose", "whoami")
        self.assertEqual(code, 0, err)
        self.assertRegex(err, r"aces-onshape: GET /users/sessioninfo -> 200 \(\d+ ms\)")
        for text in (out, err):
            for needle in (ACCESS, SECRET, fake_onshape.basic(), "Authorization", "Basic "):
                self.assertNotIn(needle, text)
        code, out, _err = self.cli_main("--version")
        self.assertEqual((code, out.strip()), (0, "aces-onshape 0.1.0"))

    def test_32_exit_codes(self):
        did, wid, ps = self.fake.seed_document()
        self.make_project("sov", "sovereign")
        code, _out, err = self.cli_main("pull", f"onshape://{did}/{wid}/{ps}", "--project", "sov")
        self.assertEqual(code, 4, err)
        self.assertIn("never for sovereign or controlled", err)
        code, _out, err = self.cli_main("pull", "onshape://not-a-ref", "--project", "proj")
        self.assertEqual(code, 2, err)
        code, _out, err = self.cli_main("elements", secrets.token_hex(12), "w", secrets.token_hex(12))
        self.assertEqual(code, 1, err)
        self.assertIn("HTTP 404", err)
        code, _out, err = self.cli_main("export", "step", did, "m", wid, ps, "--project", "proj")
        self.assertEqual(code, 2, err)
        code, _out, err = self.cli_main("nonsense-verb")
        self.assertEqual(code, 2)
        before = len(self.fake.requests)
        code, _out, err = self.cli_main("pull", f"https://evil.example/documents/{did}/w/{wid}/e/{ps}",
                                        "--project", "proj")
        self.assertEqual(code, 2, err)
        self.assertIn("refusing a document URL on host evil.example", err)
        self.assertEqual(len(self.fake.requests), before)
        code, out, err = self.cli_main("pull", f"https://127.0.0.1/documents/{did}/w/{wid}/e/{ps}",
                                       "--project", "proj")
        self.assertEqual(code, 0, err)
        self.assertIn("bundle ", out)
        self.assertNotIn("Traceback", err)

    def test_33_fs_round_trip_and_params(self):
        did, wid, ps = self.fake.seed_document()
        p = self.run_module("aces_onshape.cli", "fs", "write", did, wid, "-", "--name", "Studio (fixture)",
                            "--project", "proj", "--json", input=PLATE_CODE.encode("utf-8"))
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        written = json.loads(p.stdout)
        self.assertTrue(written["created"] and written["header_prepended"] and written["ok"])
        eid = written["eid"]
        p = self.run_module("aces_onshape.cli", "fs", "read", did, "w", wid, eid)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertEqual(p.stdout.decode("utf-8"), FIXTURE_HEADER + "\n" + PLATE_CODE)
        out_file = self.tmp / "studio.fs"
        code, out, err = self.cli_main("fs", "read", did, "w", wid, eid, "--out", str(out_file))
        self.assertEqual(code, 0, err)
        self.assertEqual(out_file.read_bytes(), (FIXTURE_HEADER + "\n" + PLATE_CODE).encode("utf-8"))
        code, out, err = self.cli_main("add-custom-feature", did, wid, ps, eid, "acesPlate", "--param", "length=20 mm",
                                       "--param", "flag=true", "--params", '{"width": "5 mm"}', "--project", "proj",
                                       "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        stored = {p["parameterId"]: p for p in self.fake.feature(did, ps, result["feature_id"])["parameters"]}
        self.assertEqual(stored["length"]["expression"], "20 mm")
        self.assertEqual(stored["width"]["expression"], "5 mm")
        self.assertIs(stored["flag"]["value"], True)
        code, out, err = self.cli_main("features", did, "w", wid, ps)
        self.assertEqual(code, 0, err)
        self.assertIn(result["feature_id"], out)

    def test_53_redaction(self):
        did, wid, _ps = self.fake.seed_document()
        self.fake.inject(400, path_contains="/elements", body={"message": f"bad request (fixture) {SECRET}"})
        code, out, err = self.cli_main("elements", did, "w", wid)
        self.assertEqual(code, 1, err)
        self.assertIn("[redacted]", err)
        self.assertNotIn(SECRET, out + err)

    def test_54_cli_guards(self):
        did, wid, ps = self.fake.seed_document()
        fid = self.fake.seed_feature(did, wid, ps, FEATURE)
        studio = self.fake.seed_studio(did, wid, "Seeded studio (fixture)", FIXTURE_HEADER + PLATE_CODE)
        feature_file = self.tmp / "feature.json"
        feature_file.write_bytes(json.dumps(dict(FEATURE, name="Replaced (fixture)")).encode("utf-8"))
        code_file = self.tmp / "plate.fs"
        code_file.write_bytes(PLATE_CODE.encode("utf-8"))
        before = len(self.fake.requests)
        code, _out, err = self.cli_main("update-feature", did, wid, ps, fid, str(feature_file), "--project", "proj")
        self.assertEqual(code, 2, err)
        self.assertIn("--confirm-foreign", err)
        code, _out, err = self.cli_main("fs", "write", did, wid, str(code_file), "--eid", studio, "--project", "proj")
        self.assertEqual(code, 2, err)
        code, _out, err = self.cli_main("delete-feature", did, wid, ps, fid, "--project", "proj")
        self.assertEqual(code, 2, err)
        self.assertEqual(len(self.fake.requests), before)
        code, _out, err = self.cli_main("update-feature", did, wid, ps, fid, str(feature_file), "--project", "proj",
                                        "--confirm-foreign")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.feature(did, ps, fid)["name"], "Replaced (fixture)")
        self.assertNotIn("ACES_ONSHAPE_PROJECT", os.environ)
        code, _out, err = self.cli_main("create-document", "X (fixture)")
        self.assertEqual(code, 2, err)
        self.assertIn("no ACES project given", err)

    def test_55a_planted_credentials_are_never_read(self):
        planted = self.tmp / "planted"
        sentinel_access, sentinel_secret = "PLANTED-fixture-access-01", "PLANTED-fixture-secret-01"
        text = f"ONSHAPE_ACCESS_KEY={sentinel_access}\nONSHAPE_SECRET_KEY={sentinel_secret}\n".encode("utf-8")
        for path in (planted / "onshape.env", planted / ".config" / "aces" / "onshape.env"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text)
            if os.name == "posix":
                os.chmod(path, 0o600)
        with mock.patch.dict(os.environ, {"ACES_USER_CONFIG": str(planted), "HOME": str(planted),
                                          "USERPROFILE": str(planted)}):
            sentinel = self.start_fake(access=sentinel_access, secret=sentinel_secret)
            env = fake_onshape.subprocess_env(self.tmp, fake=sentinel, keys=False)
            p = self.run_module("aces_onshape.cli", "whoami", env=env)
        err = p.stderr.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 3, err)
        self.assertIn(str(self.tmp / "config" / "onshape.env"), err)
        self.assertNotIn(str(planted), err)
        self.assertEqual(sentinel.count(), 0)

    def test_55b_the_network_guard(self):
        c = C.Client("https://cad.onshape.com/api/v17", ACCESS, SECRET, sleep=self.sleeps.append)
        with self.assertRaises(fake_onshape.NonLoopbackConnection):
            c.session_info()
        self.assertEqual(fake_onshape.BLOCKED, ["cad.onshape.com:443"])
        fake_onshape.BLOCKED.clear()
        self.assertEqual(self.sleeps, [])

    def test_55c_loopback_means_no_proxy(self):
        closed = "http://127.0.0.1:9"
        with mock.patch("urllib.request.getproxies", lambda: {"http": closed, "https": closed}):
            info = self.client().session_info()
        self.assertEqual(info["name"], "Fixture User")


if __name__ == "__main__":
    unittest.main()
