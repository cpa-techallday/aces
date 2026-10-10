"""W11 of build spec section 21: cad_render (section 13) with real build123d and matplotlib in the worker.

The PNGs are decoded here with the standard library (base.read_png): size per preset, the golden STYLE background
#061225 in the corner, and more than 1 % of the pixels drawn. Through the MCP server the renders come back as inline
image content that decodes as PNG.
"""
from __future__ import annotations

import base64
import hashlib
import unittest
from pathlib import Path

import base
from base import RENDER_SIZES, WorkerTestCase

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class RenderTest(WorkerTestCase):

    def check_png(self, render: dict, size: str) -> None:
        path = Path(render["path"])
        self.assertTrue(path.is_file(), path)
        data = path.read_bytes()
        self.assertEqual((render["bytes"], render["sha256"]), (len(data), hashlib.sha256(data).hexdigest()))
        self.assertEqual((render["width_px"], render["height_px"]), RENDER_SIZES[size])
        width, height, channels, rows = base.read_png(path)
        self.assertEqual((width, height), RENDER_SIZES[size])
        self.assertEqual(base.png_pixel(rows, channels, 0, 0), base.BACKGROUND_RGB, f"{path.name}: the corner")
        self.assertGreater(base.non_background_fraction(rows, channels), 0.01, f"{path.name} shows the part")

    def test_w11_views_sizes_and_files(self):
        rid = self.run_script("aces_plate", name="plate")["run_id"]
        views = ["iso", "front", "cutaway", "thickness"]
        out = self.op("render", project="p", run_id=rid, views=views, size="small", inline=False)
        self.assertEqual(out["run_id"], rid)
        self.assertEqual([r["view"] for r in out["renders"]], views)
        self.assertNotIn("inline_images", out)
        for r in out["renders"]:
            self.assertEqual(Path(r["path"]), self.runs_dir() / rid / "renders" / f"{r['view']}-small.png")
            self.check_png(r, "small")
        tess = out["tessellation"]
        self.assertGreater(tess["tolerance_mm"], 0)
        self.assertGreater(tess["triangles"], 0)
        medium = self.op("render", project="p", run_id=rid)
        self.assertEqual([r["view"] for r in medium["renders"]], ["iso"])
        self.check_png(medium["renders"][0], "medium")
        self.assertEqual(medium["inline_images"], [medium["renders"][0]["path"]], "inline by default")
        mesh = self.run_script("sdf_sphere")["run_id"]
        shown = self.op("render", project="p", run_id=mesh, views=["iso"], size="small", inline=False)
        self.check_png(shown["renders"][0], "small")

    def test_w11_mcp_inline_images_are_pngs(self):
        from aces_cad import mcp_server
        rid = self.run_script("aces_plate", name="plate")["run_id"]
        reply = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "cad_render",
                                              "arguments": {"project": "p", "run_id": rid, "views": ["iso", "top"],
                                                            "size": "small"}}})
        result = reply["result"]
        self.assertFalse(result["isError"], result)
        images = [c for c in result["content"] if c["type"] == "image"]
        self.assertEqual(len(images), 2)
        for img in images:
            self.assertEqual(img["mimeType"], "image/png")
            self.assertTrue(base64.b64decode(img["data"]).startswith(PNG_MAGIC))
        self.assertNotIn("inline_images", result["structuredContent"])
        self.assertEqual(result["content"][0]["type"], "text")


if __name__ == "__main__":
    unittest.main()
