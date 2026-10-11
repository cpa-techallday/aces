"""ledgers.py: the operator ledger through aces.ledger (entries newest first, the kind prefix, n, the 200 with
meta.error on a corrupt file, verify), the rig-side chain as the SIMLAB console reports it (13 entries in the capture)
and the desktop simlab evidence ledger (the VM's 4-entry chain re-checked here). Nothing leaves the loopback."""
from __future__ import annotations

import json
import shutil
import unittest

from aces import config as aces_config, ledger

from aces_console import config as console_config, ledgers

from . import FIXTURES
from .fakes.fake_console import FakeConsole
from .support import fresh_state
from .support_b import ConsoleB


class LedgerRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fc = FakeConsole()
        cls.c = ConsoleB(simlab_console=cls.fc.url, tag="ledgers")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.fc.close()

    def test_entries_filter_and_verify(self):
        fresh_state("ledgers-entries")
        status, _, body = self.c.get("/api/ledger")
        self.assertEqual(status, 200)
        self.assertEqual((body["data"]["n_total"], body["data"]["exists"], body["data"]["entries"]), (0, False, []))
        ledger.record("test.alpha", {"i": 1, "project": "p"})
        ledger.record("test.beta", {"i": 2, "api_key": "VALUE-THAT-MUST-NOT-SHOW"})
        ledger.record("test.alpha", {"i": 3})
        status, _, body = self.c.get("/api/ledger")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["n_total"], d["n_matching"], d["exists"], d["path"]), (3, 3, True, str(ledger.path())))
        self.assertEqual([e["n"] for e in d["entries"]], [3, 2, 1])
        self.assertEqual([e["kind"] for e in d["entries"]], ["test.alpha", "test.beta", "test.alpha"])
        self.assertEqual(d["kinds"], {"test.alpha": 2, "test.beta": 1})
        self.assertEqual(d["entries"][1]["payload"]["api_key"], "[masked]")
        self.assertNotIn("VALUE-THAT-MUST-NOT-SHOW", json.dumps(body))
        self.assertEqual(set(d["entries"][0]), {"n", "ts", "kind", "operator", "host", "payload", "hash", "prev"})
        status, _, body = self.c.get("/api/ledger?kind=test.al")
        self.assertEqual(([e["n"] for e in body["data"]["entries"]], body["data"]["n_matching"], body["data"]["kind_filter"]), ([3, 1], 2, "test.al"))
        status, _, body = self.c.get("/api/ledger?n=1")
        self.assertEqual([e["n"] for e in body["data"]["entries"]], [3])
        status, _, body = self.c.get("/api/ledger?n=0")
        self.assertEqual(status, 400)
        status, _, body = self.c.get("/api/ledger?n=abc")
        self.assertEqual(status, 400)
        status, _, body = self.c.get("/api/ledger?n=99999")
        self.assertEqual(body["data"]["n"], 2000)
        self.c.app.cache.invalidate("ledger.verify")
        status, _, body = self.c.get("/api/ledger/verify")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], {"ok": True, "n": 3, "first_bad": None, "message": "chain intact", "path": str(ledger.path())})
        okv, data, err = ledgers.fetch_aces_summary(self.c.app)
        self.assertEqual((data["n_total"], data["last_kind"], data["exists"]), (3, "test.alpha", True))

    def test_corrupt_ledger_is_200_with_meta_error(self):
        fresh_state("ledgers-corrupt")
        p = ledger.path()
        ledger.record("test.gamma", {"i": 4})
        good = p.read_text(encoding="utf-8")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        try:
            status, _, body = self.c.get("/api/ledger")
            self.assertEqual(status, 200)
            self.assertIsNone(body["data"])
            self.assertFalse(body["meta"]["ok"])
            self.assertTrue(body["meta"]["error"].startswith("ledger unreadable: aces: ledger line"), body["meta"]["error"])
            self.c.app.cache.invalidate("ledger.verify")
            status, _, body = self.c.get("/api/ledger/verify")
            self.assertEqual(status, 200)
            self.assertIsNone(body["data"])
            self.assertTrue(body["meta"]["error"].startswith("ledger unreadable"))
            okv, data, err = ledgers.fetch_aces_summary(self.c.app)
            self.assertTrue(data["error"].startswith("ledger unreadable"))
        finally:
            p.write_text(good, encoding="utf-8")
        status, _, body = self.c.get("/api/ledger")
        self.assertEqual((status, body["data"]["n_total"]), (200, len(good.splitlines())))

    def test_rig_side_chain_from_the_console(self):
        status, _, body = self.c.get("/api/ledger/simlab")
        self.assertEqual(status, 200)
        d = body["data"]
        self.assertEqual((d["n"], len(d["entries"])), (13, 13))
        self.assertEqual(d["verify"]["ok"], True)
        self.assertEqual(d["verify"]["head"], "132b2ca0723654b5af0f1443e97ed82abc6ad069fc61bd2c0ac68a768d5aaddf")
        self.assertEqual(d["note"], ledgers.SIMLAB_RIG_NOTE)
        self.assertEqual(d["errors"], {"entries": None, "verify": None})
        self.assertEqual(set(d["entries"][0]), {"ts", "experiment", "path", "record", "sha256", "previous", "n_cases", "n_finished", "company"})
        self.assertIn("parts", body["meta"])
        down = ConsoleB(tag="ledgers-down")
        try:
            status, _, body = down.get("/api/ledger/simlab")
            self.assertEqual(status, 200)
            self.assertIsNone(body["data"]["entries"])
            self.assertTrue(body["data"]["errors"]["entries"].startswith("SIMLAB console unreachable at"))
            self.assertFalse(body["meta"]["ok"])
        finally:
            down.close()

    def test_desktop_simlab_ledger(self):
        path = ledgers.desktop_simlab_path()
        self.assertEqual(path, console_config.data_dir() / "simlab" / "evidence" / "ledger.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "golden" / "desktop-simlab-evidence-ledger.jsonl", path)
        try:
            self.c.app.hub.refresh("ledger.simlab")
            status, _, body = self.c.get("/api/ledger/simlab/desktop")
            self.assertEqual(status, 200)
            d = body["data"]
            self.assertEqual((d["exists"], d["n_total"], d["path"]), (True, 4, str(path)))
            self.assertEqual(d["verify"]["ok"], True)
            self.assertEqual(d["verify"]["problems"], [])
            self.assertEqual(d["verify"]["head"], "a134a66fddc5ba3c31c5ea0870b57d42b4117328594005702001bd15aaa60f82")
            self.assertEqual([e["experiment"] for e in d["entries"]],
                             ["golden-golden-demo-20261009T144006Z-b3ac99", "golden-golden-demo-20261010T084129Z-b3ac99",
                              "golden-golden-demo-20261010T084920Z-b3ac99", "golden-cad-gold-20261010T102210Z-bc7391"])
            self.assertEqual([e["previous"] for e in d["entries"]][0], None)
            self.assertEqual(d["entries"][1]["previous"], d["entries"][0]["sha256"])
            self.assertTrue(all(e["bundle_present"] is False for e in d["entries"]), "the bundle files stay on the VM")
            self.assertIn("4 not on this desktop", d["verify"]["note"])
            self.assertEqual(d["note"], ledgers.SIMLAB_DESKTOP_NOTE)
            # a broken previous link is a problem, honestly reported
            lines = path.read_text(encoding="utf-8").splitlines()
            row = json.loads(lines[2])
            row["previous"] = "0" * 64
            lines[2] = json.dumps(row)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            d = ledgers.read_desktop_simlab(path)
            self.assertEqual(d["verify"]["ok"], False)
            self.assertEqual(d["verify"]["problems"][0]["problem"], "chain break")
        finally:
            path.unlink(missing_ok=True)
            self.c.app.hub.refresh("ledger.simlab")
        status, _, body = self.c.get("/api/ledger/simlab/desktop")
        self.assertEqual((body["data"]["exists"], body["data"]["n_total"], body["data"]["verify"]["ok"]), (False, 0, None))
        self.assertEqual(body["data"]["verify"]["note"], "no desktop simlab ledger yet")


if __name__ == "__main__":
    unittest.main()
