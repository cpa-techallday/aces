"""W12 of build spec section 21: cad_drawing (section 14) with real build123d and ezdxf in the worker.

The DXF is read back with ezdxf in the worker python as a plain child process (a test tool, outside any sandbox): its
layers, the HIDDEN linetype, the DIMENSION entities with the texts their blocks render (%%c and %%d decoded to the
diameter and degree signs) and get_measurement(), $INSUNITS and the audit. The rendered texts must read 60, 40, 5,
30, 20 and Ø10 THRU, never 6000 (the EZDXF dimstyle's dimlfac 100 that section 14 overrides).
"""
from __future__ import annotations

import hashlib
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from base import WorkerTestCase

READ_DXF = r"""
import json, sys
import ezdxf

def dec(t):
    for code, char in (("%%c", "Ø"), ("%%C", "Ø"), ("%%d", "°"), ("%%D", "°"), ("%%p", "±")):
        t = t.replace(code, char)
    return t

doc = ezdxf.readfile(sys.argv[1])
msp = doc.modelspace()
dims = []
for d in msp.query("DIMENSION"):
    texts = []
    blk = d.get_geometry_block()
    if blk is not None:
        for e in blk:
            if e.dxftype() == "MTEXT":
                texts.append(e.plain_text())
            elif e.dxftype() == "TEXT":
                texts.append(e.dxf.text)
    dims.append({"layer": d.dxf.layer, "text": dec(" ".join(t.strip() for t in texts if t.strip())),
                 "measurement": float(d.get_measurement())})
layers = {layer.dxf.name: {"linetype": layer.dxf.linetype, "color": layer.dxf.color} for layer in doc.layers}
hidden = sorted({e.dxf.linetype for e in msp if e.dxf.layer == "HIDDEN" and e.dxf.hasattr("linetype")})
auditor = doc.audit()
print(json.dumps({"dims": dims, "layers": layers, "hidden_entity_linetypes": hidden,
                  "audit_errors": len(auditor.errors), "insunits": doc.header.get("$INSUNITS"),
                  "linetypes": [lt.dxf.name for lt in doc.linetypes],
                  "texts": [dec(t.dxf.text) for t in msp.query("TEXT")],
                  "title_texts": [dec(t.dxf.text) for t in msp.query("TEXT") if t.dxf.layer == "TITLE"]}))
"""
HOLE_LIMIT_NOTE = "NOTE: HOLE DETECTION STOPS AT 100 HOLES PER PART"


class DrawingTest(WorkerTestCase):

    def read_dxf(self, path) -> dict:
        return self.worker_python_json(READ_DXF, str(path))

    def holes_of(self, run_id: str) -> dict:
        """cad_inspect's hole records of a run, by id."""
        out = self.op("inspect", project="p", run_id=run_id, checks=["holes"])
        return {h["id"]: h for h in out["checks"]["holes"]["holes"]}

    def drawing(self, run_id: str, **kw) -> dict:
        out = self.op("drawing", project="p", run_id=run_id, **kw)
        self.assertEqual((out["schema"], out["run_id"], out["units"], out["scale"]),
                         ("aces.cad.drawing/1", run_id, "mm", "1:1"))
        self.assertEqual(out["audit_errors"], 0)
        for fmt, info in out["files"].items():
            data = Path(info["path"]).read_bytes()
            self.assertEqual((info["bytes"], info["sha256"]), (len(data), hashlib.sha256(data).hexdigest()), fmt)
            self.assertEqual(Path(info["path"]).parent, self.runs_dir() / run_id / "drawings")
        return out

    def test_w12_the_aces_plate(self):
        rid = self.run_script("aces_plate", name="plate")["run_id"]
        out = self.drawing(rid)
        self.assertEqual(out["projection"], "third-angle")
        self.assertEqual(sorted(out["files"]), ["dxf", "png", "svg"])
        self.assertEqual(sorted(v["name"] for v in out["views"]), ["front", "iso", "right", "top"])
        dxf = self.read_dxf(out["files"]["dxf"]["path"])
        for layer in ("VISIBLE", "HIDDEN", "DIMENSIONS", "TITLE"):
            self.assertIn(layer, dxf["layers"])
        self.assertEqual(dxf["layers"]["HIDDEN"]["linetype"], "ACES_HIDDEN")
        self.assertIn("ACES_HIDDEN", dxf["linetypes"])
        self.assertIn(dxf["hidden_entity_linetypes"], ([], ["BYLAYER"]))
        self.assertEqual(dxf["insunits"], 4)
        self.assertEqual(dxf["audit_errors"], 0)
        self.assertGreaterEqual(len(dxf["dims"]), 6)
        texts = [d["text"] for d in dxf["dims"]]
        for want in ("60", "40", "5", "30", "20", "Ø10 THRU"):
            self.assertIn(want, texts, f"rendered dimension texts {texts}")
        for bad in ("6000", "500", "4000"):
            self.assertNotIn(bad, texts, "a dimension rendered with dimlfac 100")
        features = {60.0, 40.0, 5.0, 30.0, 20.0, 10.0}
        for d in dxf["dims"]:
            self.assertEqual(d["layer"], "DIMENSIONS")
            self.assertTrue(any(abs(d["measurement"] - f) < 1e-6 for f in features), d)
        for rec in out["dimensions"]:
            self.assertRegex(rec["id"], r"^d[0-9]+$")
            self.assertIn(rec["type"], ("linear", "diameter"))
            self.assertTrue(any(abs(rec["value_mm"] - f) < 1e-6 for f in features), rec)
        title = " ".join(dxf["texts"])
        for want in ("PROJECT p (sovereign)", f"RUN {rid}", "SCALE 1:1", "UNITS mm", "THIRD ANGLE PROJECTION",
                     "not a GD&T drawing"):
            self.assertIn(want, title)
        root = ET.parse(out["files"]["svg"]["path"]).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("viewBox", root.attrib)
        self.assertTrue(Path(out["files"]["png"]["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        record = sorted((self.runs_dir() / rid / "drawings").glob("*.json"))
        self.assertEqual(len(record), 1)
        self.assertEqual(json.loads(record[0].read_text(encoding="utf-8"))["files"], out["files"])

    def test_w12_hole_callouts(self):
        rid = self.run_script("holes_block")["run_id"]
        out = self.drawing(rid, views=["front", "top", "right"])
        texts = [d["text"] for d in self.read_dxf(out["files"]["dxf"]["path"])["dims"]]
        joined = " | ".join(texts)
        for want in ("Ø10 THRU", "DEEP 8", "CBORE Ø11 DEEP 6", "CSK Ø12 X 90°"):
            self.assertIn(want, joined)
        # review finding 2: a blind hole's drill point is no countersink (it read "Ø6 DEEP 11.8, CSK Ø0 X 118°"), and
        # a hole countersunk on both faces is called out at its upper end
        rid = self.run_script("drill_point_block")["run_id"]
        out = self.drawing(rid, views=["top"], formats=["dxf"])
        texts = [d["text"] for d in self.read_dxf(out["files"]["dxf"]["path"])["dims"]]
        joined = " | ".join(texts)
        for want in ("Ø6 DEEP 10", "Ø6.6 THRU, CSK Ø12 X 90°"):
            self.assertIn(want, joined)
        for bad in ("CSK Ø0", "DEEP 11.8", "118°"):
            self.assertNotIn(bad, joined)

    def test_w12_hole_ids_are_cad_inspect_ids(self):
        """drawing.json's hole features are cad_inspect's ids for the same run (review finding 5: the drawing renumbered
        the holes by position, so an h2 in one output was another hole in the other)."""
        rid = self.run_script("three_axis_holes")["run_id"]
        holes = self.holes_of(rid)
        self.assertEqual(sorted(holes), ["h1", "h2", "h3"])
        by_position = sorted(holes.values(), key=lambda h: [round(h["entry_point_mm"][i], 6) for i in (2, 1, 0)])
        self.assertNotEqual([h["id"] for h in by_position], ["h1", "h2", "h3"],
                            "the fixture must number its holes out of position order, or this test proves nothing")
        out = self.drawing(rid, views=["front", "top", "right"], formats=["dxf"])
        callouts = {d["feature"]: d for d in out["dimensions"] if d["type"] == "diameter"}
        self.assertEqual(sorted(callouts), sorted(holes))
        for hid, dim in callouts.items():
            self.assert_close(dim["value_mm"], holes[hid]["diameter_mm"], 1e-6, hid)
        positions = [d for d in out["dimensions"] if d["feature"].startswith("pos_")]
        self.assertEqual(len(positions), 6, "two position dimensions per hole")
        for dim in positions:
            _pos, hid, axis = dim["feature"].split("_")
            self.assert_close(dim["value_mm"], holes[hid]["entry_point_mm"]["xyz".index(axis)], 1e-6, dim["feature"])
        # several parts: cad_inspect numbers holes across the parts in part order and groups them per part
        rid = self.run_script("two_plates_holes")["run_id"]
        inspected = self.op("inspect", project="p", run_id=rid, checks=["holes"])["checks"]["holes"]
        holes = {h["id"]: h for h in inspected["holes"]}
        self.assertEqual({h["id"]: h["part"] for h in holes.values()}, {"h1": "left", "h2": "right", "h3": "right"})
        groups = {g["id"]: g["holes"] for g in inspected["hole_groups"]}
        out = self.drawing(rid, views=["top"], formats=["dxf"])
        callouts = {d["feature"]: d["value_mm"] for d in out["dimensions"] if d["type"] == "diameter"}
        self.assertEqual(set(callouts), {"h1", "g2"})
        self.assertEqual(groups["g2"], ["h2", "h3"])
        self.assert_close([callouts["h1"], callouts["g2"]],
                          [holes["h1"]["diameter_mm"], holes["h2"]["diameter_mm"]], 1e-6)
        for dim in [d for d in out["dimensions"] if d["feature"].startswith("pos_")]:
            _pos, hid, axis = dim["feature"].split("_")
            self.assert_close(dim["value_mm"], holes[hid]["entry_point_mm"]["xyz".index(axis)], 1e-6, dim["feature"])

    def test_w12_the_hole_cap_and_the_hole_table(self):
        """The 100-hole note and warning appear only when a part has more than 100 holes (integration fix c5f93c2), and
        the hole table lists cad_inspect's ids in capitals with their positions (review finding 5)."""
        rid = self.run_script("perforated_block")["run_id"]
        holes = self.holes_of(rid)
        self.assertEqual(len(holes), 100)
        out = self.drawing(rid, views=["top"], formats=["dxf"])
        self.assertEqual([w for w in out["warnings"] if "at most 100 holes" in w], [])
        dxf = self.read_dxf(out["files"]["dxf"]["path"])
        self.assertFalse([t for t in dxf["texts"] if "HOLE DETECTION STOPS" in t], "no cap note at 100 holes")
        table = out["hole_table"]
        self.assertEqual(len(table), 100)
        for row in table:
            self.assertEqual(row["id"], row["hole"].upper())
            self.assert_close([row["x"], row["y"]], holes[row["hole"]]["entry_point_mm"][:2], 1e-6, row["id"])
        self.assertIn(f"{table[0]['id']} ", " ".join(dxf["title_texts"]))
        self.assertNotEqual([r["hole"] for r in table], [f"h{i}" for i in range(1, 101)],
                            "the table runs in position order while the ids stay cad_inspect's")
        over = self.run_script("perforated_block_101")["run_id"]
        out = self.drawing(over, views=["top"], formats=["dxf"])
        capped = [w for w in out["warnings"] if "at most 100 holes per part" in w]
        self.assertEqual(len(capped), 1, out["warnings"])
        self.assertIn("perforated-block-101 has more", capped[0])
        self.assertEqual(len(out["hole_table"]), 100)
        title = self.read_dxf(out["files"]["dxf"]["path"])["title_texts"]
        self.assertEqual(len([t for t in title if t.startswith(HOLE_LIMIT_NOTE)]), 1, title)

    def test_w12_projection_placement(self):
        rid = self.run_script("aces_plate", name="plate")["run_id"]

        def offsets(projection: str) -> dict:
            out = self.drawing(rid, views=["front", "top", "right"], projection=projection, formats=["dxf"])
            self.assertEqual(sorted(out["files"]), ["dxf"])
            return {v["name"]: v["offset_mm"] for v in out["views"]}

        third, first = offsets("third-angle"), offsets("first-angle")
        for views in (third, first):
            self.assertAlmostEqual(views["top"][0], views["front"][0], delta=1e-6, msg="top moves only in y")
            self.assertAlmostEqual(views["right"][1], views["front"][1], delta=1e-6,
                                   msg="the right view moves only in x")
        self.assertGreater(third["top"][1], third["front"][1], "third angle: top above front")
        self.assertGreater(third["right"][0], third["front"][0], "third angle: right to the right")
        self.assertLess(first["top"][1], first["front"][1], "first angle: top below front")
        self.assertLess(first["right"][0], first["front"][0], "first angle: right on the left")

    def test_w12_a_mesh_run_is_refused(self):
        rid = self.run_script("sdf_sphere")["run_id"]
        err = self.op_error("drawing", "usage", project="p", run_id=rid)
        self.assertIn("B-rep", err["message"])


if __name__ == "__main__":
    unittest.main()
