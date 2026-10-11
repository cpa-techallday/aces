"""safefs.py: the name rules, the component-wise walk, the forbidden names, the size caps, the content-type allow-list
and the file-response headers (CSP sandbox, nosniff, inline disposition, private cache, ETag, 304)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from aces_console import safefs, server

from . import ROOT
from .support import Console


class SafeFsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "safefs-root"
        (cls.root / "sub" / "deep").mkdir(parents=True, exist_ok=True)
        (cls.root / "a.json").write_bytes(b'{"a": 1}\n')
        (cls.root / "sub" / "nested.txt").write_bytes(b"hello\n")
        (cls.root / "sub" / "deep" / "part.stl").write_bytes(b"solid x\nendsolid x\n")
        (cls.root / "report.html").write_bytes(b"<p>r</p>")
        (cls.root / "drawing.svg").write_bytes(b"<svg/>")
        (cls.root / "events.jsonl").write_bytes(b'{"n":1}\n')
        (cls.root / "model.joblib").write_bytes(b"\x80\x04")
        (cls.root / ".hidden").write_text("x", encoding="utf-8")
        (cls.root / "keys.env").write_text("X=1", encoding="utf-8")
        (cls.root / "server.key").write_text("k", encoding="utf-8")
        (cls.root / "ca.pem").write_text("p", encoding="utf-8")
        (cls.root / "my-Token.txt").write_text("t", encoding="utf-8")
        (cls.root / "tool.exe").write_bytes(b"MZ")
        with open(cls.root / "big.json", "wb") as fh:
            fh.write(b" " * (8 * 1024 * 1024 + 1))
        cls.link_ok = False
        try:
            os.symlink(cls.root / "a.json", cls.root / "link.json")
            cls.link_ok = True
        except (OSError, NotImplementedError):
            cls.link_ok = False

    def _status(self, rel, **kw):
        try:
            safefs.open_under(self.root, rel, **kw)
        except safefs.SafeFsError as e:
            return e.status
        return 200

    def test_reads_and_types(self):
        data, ctype, digest = safefs.open_under(self.root, "a.json")
        self.assertEqual((data, ctype, len(digest)), (b'{"a": 1}\n', "application/json", 64))
        self.assertEqual(safefs.open_under(self.root, "sub/nested.txt")[1], "text/plain; charset=utf-8")
        self.assertEqual(safefs.open_under(self.root, "sub/deep/part.stl")[1], "model/stl")
        self.assertEqual(safefs.open_under(self.root, "report.html")[1], "text/html; charset=utf-8")
        self.assertEqual(safefs.open_under(self.root, "drawing.svg")[1], "image/svg+xml")
        self.assertEqual(safefs.open_under(self.root, "events.jsonl")[1], "application/x-ndjson")
        self.assertEqual(self._status("model.joblib"), 415, "binary kinds are served only on request")
        self.assertEqual(safefs.open_under(self.root, "model.joblib", kinds=("bin",))[1], "application/octet-stream")
        self.assertEqual(self._status("tool.exe"), 415)
        self.assertEqual(self._status("sub/nested.txt", kinds=("json",)), 415)

    def test_refused_and_malformed(self):
        self.assertEqual(self._status("../a.json"), 400)
        self.assertEqual(self._status("sub/../a.json"), 400)
        self.assertEqual(self._status("./a.json"), 400)
        self.assertEqual(self._status("/a.json"), 400)
        self.assertEqual(self._status("sub\\nested.txt"), 400)
        self.assertEqual(self._status("C:/x.json"), 400)
        self.assertEqual(self._status("a/b/c/d/e/f/g/h/i.json"), 400)
        self.assertEqual(self._status(""), 400)
        self.assertEqual(self._status(".hidden"), 403)
        self.assertEqual(self._status("keys.env"), 403)
        self.assertEqual(self._status("server.key"), 403)
        self.assertEqual(self._status("ca.pem"), 403)
        self.assertEqual(self._status("my-Token.txt"), 403)
        self.assertEqual(self._status("missing.json"), 404)
        self.assertEqual(self._status("sub"), 415, "a directory has no served type")
        self.assertEqual(self._status("nested.txt/x.txt"), 404)
        if self.link_ok:
            self.assertEqual(self._status("link.json"), 403)
        else:
            self.skipTest("symlinks cannot be created here (Windows without the privilege); the lstat branch is untested")

    def test_size_caps(self):
        self.assertEqual(self._status("big.json"), 413)
        self.assertEqual(self._status("a.json", max_bytes=4), 413)
        self.assertEqual(self._status("a.json", max_bytes=1 << 20), 200)

    def test_name_checks(self):
        self.assertEqual(safefs.check_project("cad-gold"), "cad-gold")
        for bad in ("", "../x", "a/b", ".", "..", "x" * 65, None, "a b"):
            with self.assertRaises(safefs.SafeFsError, msg=repr(bad)):
                safefs.check_project(bad)
        self.assertEqual(safefs.check_run_id("20261010T101628Z-0ffb"), "20261010T101628Z-0ffb")
        with self.assertRaises(safefs.SafeFsError):
            safefs.check_run_id("20261010T102210Z-bc7391")
        self.assertEqual(safefs.check_golden_run_id("20261010T102210Z-bc7391"), "20261010T102210Z-bc7391")
        self.assertEqual(safefs.check_golden_run_id("20261010T102210Z-bc7391-2"), "20261010T102210Z-bc7391-2")
        with self.assertRaises(safefs.SafeFsError):
            safefs.check_golden_run_id("latest")
        self.assertEqual(safefs.check_bundle_name("20261010T101736Z-plate"), "20261010T101736Z-plate")
        with self.assertRaises(safefs.SafeFsError):
            safefs.check_bundle_name("plate")

    def test_headers(self):
        h = safefs.headers_for("image/png", "ab" * 32, 'we ird".png')
        self.assertEqual(h["Content-Security-Policy"], "sandbox")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Content-Disposition"], 'inline; filename="we_ird_.png"')
        self.assertEqual(h["Cache-Control"], "private, max-age=60")
        self.assertEqual(h["ETag"], '"' + "ab" * 32 + '"')


class FileRouteTest(unittest.TestCase):
    """A throwaway file route on a running console proves the response headers of rule 5 end to end."""

    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / "safefs-route"
        (cls.root / "sub").mkdir(parents=True, exist_ok=True)
        (cls.root / "sub" / "nested.txt").write_bytes(b"hello\n")
        (cls.root / "report.html").write_bytes(b"<p>r</p>")
        (cls.root / ".hidden").write_bytes(b"x")
        root = cls.root
        server.register("GET", "/t/safefs/{rel:path}", lambda req: server.file_response(req, root, req.params["rel"]))
        cls.c = Console(tag="safefs")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def test_file_headers_and_304(self):
        status, hdrs, body = self.c.request("GET", "/t/safefs/sub/nested.txt")
        self.assertEqual((status, body), (200, b"hello\n"))
        self.assertEqual(hdrs["content-security-policy"], "sandbox")
        self.assertEqual(hdrs["x-content-type-options"], "nosniff")
        self.assertEqual(hdrs["content-disposition"], 'inline; filename="nested.txt"')
        self.assertEqual(hdrs["cache-control"], "private, max-age=60")
        self.assertEqual(hdrs["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(hdrs["referrer-policy"], "no-referrer")
        etag = hdrs["etag"]
        self.assertTrue(etag.startswith('"') and len(etag) == 66)
        status, hdrs, body = self.c.request("GET", "/t/safefs/sub/nested.txt", headers={"If-None-Match": etag})
        self.assertEqual((status, body), (304, b""))
        status, hdrs, body = self.c.request("GET", "/t/safefs/report.html")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["content-security-policy"], "sandbox")
        self.assertTrue(hdrs["content-type"].startswith("text/html"))
        status, _, body = self.c.get("/t/safefs/.hidden")
        self.assertEqual((status, body["code"]), (403, "refused"))
        self.assertTrue(body["error"].startswith("refusing"), body["error"])
        status, _, body = self.c.get("/t/safefs/sub/../report.html")
        self.assertEqual((status, body["code"]), (400, "bad-path"))
        status, _, body = self.c.get("/t/safefs/nope.txt")
        self.assertEqual((status, body["code"]), (404, "not-found"))
        status, _, body = self.c.get("/t/safefs/sub/nested.exe")
        self.assertEqual((status, body["code"]), (415, "unsupported-type"))


if __name__ == "__main__":
    unittest.main()
