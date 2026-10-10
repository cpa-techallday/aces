"""U05 of build spec section 21: cad/NOTES.md (section 16), through the note, notes and start operations.

The server writes only the heading line of an entry; lines starting with # and control characters are refused; the
file stops growing at 256 KiB and is never rewritten; two writing processes keep every entry whole; cad_start returns
the last whole entries within 16 KiB. cad_start runs an env job, here through the fake worker (permissive mode).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import base
from base import CadTestCase

HEADER = ("# CAD notes for p\n\n<!-- aces-cad: entries are appended by cad_note, verbatim; nothing here is generated "
          "prose. -->\n")
HEADING_RE = re.compile(r"^## (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)( · tags: ([a-z0-9, -]+))?( · run (\S+))?$")


def entries(text: str) -> list[tuple[str, str]]:
    """(heading line, body) pairs; the body is the text between the blank line after the heading and the next one."""
    out = []
    parts = re.split(r"(?m)^(## .*)$", text)
    for i in range(1, len(parts), 2):
        body = parts[i + 1]
        assert body.startswith("\n\n"), repr(body[:20])
        out.append((parts[i], body[2:].rstrip("\n")))
    return out


class NotesTest(CadTestCase):

    def setUp(self):
        super().setUp()
        self.make_project("p", "sovereign")
        self.notes_md = Path(os.environ["ACES_WORK"]) / "p" / "cad" / "NOTES.md"

    def note(self, text: str, **kw) -> dict:
        return self.op("note", project="p", text=text, **kw)

    def test_u05_format_and_heading_only_server_text(self):
        first = self.note("A 50 mm fillet on a 10 mm cube fails: use r <= 4.9 (fixture lesson).",
                          tags=["fillet", "kernel"], run_id="20261010T133514Z-a3f9")
        self.assertEqual(Path(first["path"]).resolve(), self.notes_md.resolve())
        self.assertEqual(first["entry"]["n"], 1)
        self.assertEqual(first["entry"]["tags"], ["fillet", "kernel"])
        self.assertEqual(first["entry"]["run_id"], "20261010T133514Z-a3f9")
        self.assertRegex(first["entry"]["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        texts = ["A 50 mm fillet on a 10 mm cube fails: use r <= 4.9 (fixture lesson).",
                 "line one\nline two\twith a tab\n\nline four (fixture)",
                 "windows line\nendings (fixture)"]
        self.note(texts[1])
        last = self.note("windows line\r\nendings (fixture)", tags=["crlf"])
        self.assertEqual((last["entries_total"], last["entry"]["n"]), (3, 3))
        data = self.notes_md.read_bytes()
        self.assertEqual(last["bytes_total"], len(data))
        self.assertNotIn(b"\r", data)
        text = data.decode("utf-8")
        self.assertTrue(text.startswith(HEADER), text[:200])
        got = entries(text)
        self.assertEqual([body for _h, body in got], texts, "the server writes only the headings; texts are verbatim")
        m = HEADING_RE.match(got[0][0])
        self.assertIsNotNone(m, got[0][0])
        self.assertEqual((m.group(3), m.group(5)), ("fillet, kernel", "20261010T133514Z-a3f9"))
        self.assertIsNotNone(HEADING_RE.match(got[1][0]))
        self.assertIsNone(HEADING_RE.match(got[1][0]).group(2), "no tags, no tags part")
        self.assertEqual(HEADING_RE.match(got[2][0]).group(3), "crlf")
        server_lines = [ln for ln in text[len(HEADER):].split("\n") if ln.startswith("## ")]
        self.assertEqual(len(server_lines), 3)

    def test_u05_refusals(self):
        for bad in ("fine\n# a heading of my own", "# heading first", "bell \x07 (fixture)", "escape \x1b[31m red",
                    "nul \x00 (fixture)"):
            err = self.op_error("note", "usage", project="p", text=bad)
            if "#" in bad:
                self.assertIn("reserved for headings", err["message"])
        self.assertFalse(self.notes_md.exists(), "a refused note writes nothing")
        self.op_error("note", "usage", project="p", text="x", tags=["Bad"])
        self.op_error("note", "usage", project="p", text="x", run_id="latest")
        self.op_error("note", "usage", project="p", text="x" * 2001)
        self.op_error("note", "project", text="no project given (fixture)")

    def test_u05_the_256_kib_cap(self):
        body = "x" * 1990
        n = 0
        sizes = []
        while True:
            try:
                self.note(f"{n:05d} {body}")
            except base_cad_error() as e:
                self.assertEqual(e.kind, "usage")
                self.assertIn("NOTES.md is full", str(e))
                break
            n += 1
            sizes.append(self.notes_md.stat().st_size)
            self.assertLess(n, 200, "NOTES.md kept growing past 256 KiB")
        self.assertGreater(n, 100)
        self.assertLessEqual(self.notes_md.stat().st_size, 256 * 1024)
        self.assertEqual(sizes, sorted(sizes), "the file only grows")
        before = self.notes_md.read_bytes()
        self.op_error("note", "usage", project="p", text=f"99999 {body}")
        self.assertEqual(self.notes_md.read_bytes(), before, "a refused note leaves the file unchanged")

    def test_u05_two_concurrent_writers_keep_entries_whole(self):
        root = base.lib_root()
        code = ("import sys\nsys.path.insert(0, sys.argv[1])\nfrom aces_cad import ops\n"
                "for i in range(20):\n"
                "    ops.dispatch('note', {'project': 'p', 'text': 'writer ' + sys.argv[2] + ' note ' + str(i) + ' '"
                " + sys.argv[2] * 1500})\n")
        env = self.child_env()
        procs = [subprocess.Popen([sys.executable, "-c", code, str(root), w], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for w in ("A", "B")]
        for p in procs:
            _out, err = p.communicate(timeout=300)
            self.assertEqual(p.returncode, 0, err.decode("utf-8", "replace")[-1500:])
        got = entries(self.notes_md.read_text(encoding="utf-8"))
        self.assertEqual(len(got), 40)
        expected = {f"writer {w} note {i} " + w * 1500 for w in "AB" for i in range(20)}
        self.assertEqual({body for _h, body in got}, expected)

    def test_u05_start_returns_the_last_16_kib(self):
        self.use_fake_worker("ok")
        os.environ["ACES_CAD_SANDBOX"] = "permissive"
        empty = self.op("start", project="p")
        self.assertEqual(empty["notes"]["entries_total"], 0)
        self.assertFalse(empty["notes"]["exists"])
        texts = [f"lesson {i:02d} (fixture) " + "y" * 1500 for i in range(20)]
        for t in texts:
            self.note(t)
        started = self.op("start", project="p")
        notes = started["notes"]
        self.assertTrue(notes["exists"])
        self.assertEqual(notes["entries_total"], 20)
        self.assertEqual(notes["bytes_total"], self.notes_md.stat().st_size)
        self.assertLessEqual(len(notes["text"].encode("utf-8")), 16 * 1024)
        self.assertLess(notes["entries_returned"], 20)
        self.assertGreaterEqual(notes["entries_returned"], 9)
        got = entries(notes["text"])
        self.assertEqual(len(got), notes["entries_returned"], "only whole entries")
        self.assertEqual([b for _h, b in got], texts[-len(got):], "the last entries, in order")
        full = self.op("notes", project="p")
        self.assertIn(texts[0], full["text"])


def base_cad_error():
    from aces_cad.errors import CadError
    return CadError


if __name__ == "__main__":
    unittest.main()
