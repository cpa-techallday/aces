"""W14 of build spec section 21: the cad_start / cad_note / cad_runs round trip of an agent's loop (sections 11.1,
11.3, 11.8 and 16), with real build123d runs.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from base import WorkerTestCase

LIMITS = {"timeout_s": 120, "timeout_s_max": 600, "memory_mb": 4096, "threads": 2, "script_max_chars": 200000,
          "max_file_mb": 1024, "max_run_mb": 2048}


class LoopTest(WorkerTestCase):

    def test_w14_start_note_runs(self):
        start = self.op("start", project="p")
        pdir = Path(os.environ["ACES_WORK"]) / "p"
        self.assertEqual((start["project"]["name"], start["project"]["class"]), ("p", "sovereign"))
        self.assertEqual(Path(start["paths"]["cad"]), pdir / "cad")
        self.assertEqual(Path(start["paths"]["runs"]), pdir / "cad" / "local" / "runs")
        self.assertEqual(Path(start["paths"]["notes"]), pdir / "cad" / "NOTES.md")
        self.assertTrue((pdir / "cad" / "local" / "runs").is_dir(), "cad_start creates the cad/ directories")
        self.assertIs(start["notes"]["exists"], False)
        self.assertEqual(start["recent_runs"], [])
        self.assertEqual(start["limits"], LIMITS)
        self.assertEqual(start["conventions"]["entry"], "def build(p) (gen_step() accepted)")
        self.assertEqual(start["conventions"]["units"], "mm, degrees")
        self.assertIs(start["ready"], True, start["warnings"])
        self.assertEqual(start["sandbox"]["mode"], self.mode)
        self.assertTrue(start["environment"]["worker_python"])
        self.assertIn(start["environment"]["ray_backend"], ("embree", "triangle", "rtree", None))
        plate = self.run_script("aces_plate", name="plate")
        failed = self.run_error("name_error", "script")
        note = self.op("note", project="p", text="aces_plate: 11607.30 mm3 at the defaults (fixture lesson).",
                       tags=["plate"], run_id=plate["run_id"])
        self.assertEqual(note["entry"]["n"], 1)
        again = self.op("start", project="p")
        self.assertIn("11607.30 mm3 at the defaults (fixture lesson).", again["notes"]["text"])
        self.assertEqual(again["notes"]["entries_total"], 1)
        recent = again["recent_runs"]
        self.assertEqual([r["run_id"] for r in recent], [failed["run_id"], plate["run_id"]], "newest first")
        self.assertEqual((recent[0]["status"], recent[0]["error_kind"]), ("error", "script"))
        self.assertEqual((recent[1]["status"], recent[1]["kind"], recent[1]["valid"]), ("ok", "solid", True))
        listing = self.op("runs", project="p")
        self.assertEqual((listing["count"], listing["total"]), (2, 2))
        self.assertEqual([r["run_id"] for r in listing["runs"]], [failed["run_id"], plate["run_id"]])
        row = listing["runs"][1]
        self.assertEqual(row["params"], plate["params"])
        self.assert_expected(row["volume_mm3"], "aces_plate.volume_mm3")
        self.assertEqual(len(self.op("runs", project="p", limit=1)["runs"]), 1)
        shown = self.op("runs", project="p", run_id=plate["run_id"], include_script=True, include_output=True)
        self.assertEqual(shown["run"]["run_id"], plate["run_id"])
        self.assertEqual(shown["script"], self.script("aces_plate"))
        self.assertIn("stdout", shown)
        self.assertIn("stderr", shown)
        bare = self.op("runs", project="p", run_id=failed["run_id"])
        self.assertEqual(bare["run"]["status"], "error")
        self.assertNotIn("script", bare)
        latest = self.op("runs", project="p", run_id="latest")
        self.assertEqual(latest["run"]["run_id"], failed["run_id"], "cad_runs latest is the newest run of any status")


if __name__ == "__main__":
    unittest.main()
