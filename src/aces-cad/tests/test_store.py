"""U04 of build spec section 21: the run store (sections 5.1, 7.6 and 9, aces_cad.store).

Run ids and their collision retry, the `latest` rules, safe_read refusing links, FIFOs, extra hard links and oversize
files, read_project_file refusing traversal, absolute paths, links and other extensions, and promote refusing
unexpected names and links (kind integrity, nothing moved).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unittest
from pathlib import Path
from unittest import mock

from base import POSIX, RUN_ID_RE, CadTestCase


def try_symlink(test: unittest.TestCase, target, link, *, directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as e:
        test.skipTest(f"cannot create symlinks here ({e})")


class StoreTest(CadTestCase):

    def setUp(self):
        super().setUp()
        from aces_cad import errors, store
        self.errors, self.store = errors, store
        self.pdir = self.make_project("p", "sovereign")
        self.info = {"name": "p", "class": "sovereign", "class_source": "aces.yaml", "dir": str(self.pdir)}
        self.base = self.tmp / "base"
        self.base.mkdir()

    def refused(self, fn, *args, kinds=None, **kwargs) -> dict:
        with self.assertRaises(self.errors.CadError) as ctx:
            fn(*args, **kwargs)
        err = ctx.exception.to_dict()
        if kinds is not None:
            self.assertIn(err["kind"], kinds, err)
        return err

    # --- run ids and latest ----------------------------------------------------------------------------------------

    def test_u04_run_ids(self):
        runs = [self.store.new_run(self.info) for _ in range(30)]
        ids = [r.id for r in runs]
        self.assertEqual(len(set(ids)), 30)
        for r in runs:
            self.assertRegex(r.id, RUN_ID_RE)
            self.assertTrue(Path(r.root).is_dir())
            self.assertEqual(Path(r.root).name, r.id)
            self.assertEqual(Path(r.root).parent, self.runs_dir("p"))
            if POSIX:
                self.assertEqual(os.stat(r.root).st_mode & 0o777, 0o700)

    def test_u04_run_id_collision_is_retried(self):
        real_mkdir = os.mkdir
        hits: list[str] = []

        def flaky(path, *args, **kwargs):
            name = os.path.basename(os.fspath(path))
            if re.fullmatch(RUN_ID_RE, name):
                hits.append(name)
                if len(hits) == 1:
                    raise FileExistsError(17, "File exists (fixture: a concurrent run took this id)", os.fspath(path))
            return real_mkdir(path, *args, **kwargs)

        with mock.patch("os.mkdir", flaky):
            run = self.store.new_run(self.info)
        self.assertGreaterEqual(len(hits), 2, "new_run did not retry after an exclusive mkdir failed")
        self.assertRegex(run.id, RUN_ID_RE)
        self.assertTrue(Path(run.root).is_dir())

    def write_run(self, run_id: str, status: str) -> Path:
        root = self.runs_dir("p") / run_id
        root.mkdir(parents=True)
        rec = {"schema": "aces.cad.run/1", "run_id": run_id, "name": "fixture", "project": "p", "class": "sovereign",
               "created_at": "2026-10-10T00:00:00Z", "finished_at": "2026-10-10T00:00:01Z", "status": status,
               "error": None if status == "ok" else {"kind": "script", "message": "fixture failure"},
               "script": {"source": "inline", "path": None, "sha256": "0" * 64, "bytes": 0}, "result": None,
               "params": {}, "params_overridden": [], "params_declared": {}, "params_sha256": "0" * 64}
        (root / "run.json").write_text(json.dumps(rec), encoding="utf-8")
        return root

    def test_u04_latest_rules(self):
        with self.assertRaises(self.errors.CadError) as ctx:
            self.store.open_run(self.info, "latest", require_ok=True)
        self.assertEqual(ctx.exception.kind, "not_found")
        self.write_run("20261010T000001Z-0a0a", "ok")
        self.write_run("20261010T000002Z-0b0b", "ok")
        self.write_run("20261010T000003Z-0c0c", "error")
        self.assertEqual(self.store.open_run(self.info, "latest", require_ok=True).id, "20261010T000002Z-0b0b")
        self.assertEqual(self.store.open_run(self.info, "latest", require_ok=False).id, "20261010T000003Z-0c0c")
        self.assertEqual(self.store.open_run(self.info, "20261010T000001Z-0a0a", require_ok=True).id,
                         "20261010T000001Z-0a0a")
        self.assertEqual(self.store.open_run(self.info, "20261010T000003Z-0c0c", require_ok=False).id,
                         "20261010T000003Z-0c0c")
        self.refused(self.store.open_run, self.info, "20261010T000003Z-0c0c", require_ok=True, kinds=("usage",))
        self.refused(self.store.open_run, self.info, "20261010T000009Z-0d0d", require_ok=False, kinds=("not_found",))
        self.refused(self.store.open_run, self.info, "../../etc", require_ok=False, kinds=("usage", "not_found"))
        rows = self.store.list_runs(self.info, 2)
        self.assertEqual([r["run_id"] for r in rows], ["20261010T000003Z-0c0c", "20261010T000002Z-0b0b"])
        self.assertEqual(rows[0]["status"], "error")

    # --- safe_read and sha256_file ---------------------------------------------------------------------------------

    def test_u04_safe_read(self):
        (self.base / "sub").mkdir()
        (self.base / "sub" / "a.txt").write_bytes(b"fixture bytes\n")
        self.assertEqual(self.store.safe_read(self.base, "sub/a.txt", 100), b"fixture bytes\n")
        sha, size = self.store.sha256_file(self.base, "sub/a.txt")
        self.assertEqual((sha, size), (hashlib.sha256(b"fixture bytes\n").hexdigest(), 14))
        self.refused(self.store.safe_read, self.base, "sub/missing.txt", 100, kinds=("not_found",))
        self.refused(self.store.safe_read, self.base, "sub/a.txt", 5)
        for bad in ("../outside.txt", "/etc/hostname", "sub/../sub/a.txt", "", "sub//a.txt"):
            self.refused(self.store.safe_read, self.base, bad, 100)

    def test_u04_safe_read_refuses_links(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"secret (fixture)\n")
        try_symlink(self, outside / "secret.txt", self.base / "final-link.txt")
        self.refused(self.store.safe_read, self.base, "final-link.txt", 100)
        try_symlink(self, outside, self.base / "dir-link", directory=True)
        self.refused(self.store.safe_read, self.base, "dir-link/secret.txt", 100)
        self.refused(self.store.sha256_file, self.base, "dir-link/secret.txt")

    def test_u04_safe_read_refuses_hard_links(self):
        (self.base / "one.txt").write_bytes(b"x\n")
        try:
            os.link(self.base / "one.txt", self.tmp / "second-name.txt")
        except OSError as e:
            self.skipTest(f"cannot create hard links here ({e})")
        self.refused(self.store.safe_read, self.base, "one.txt", 100)

    def test_u04_safe_read_refuses_a_fifo_without_blocking(self):
        self.require_posix("a FIFO")
        os.mkfifo(self.base / "fifo")
        result: dict = {}

        def attempt():
            try:
                self.store.safe_read(self.base, "fifo", 100)
                result["outcome"] = "read"
            except self.errors.CadError as e:
                result["outcome"] = e.kind

        t = threading.Thread(target=attempt, daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive(), "safe_read blocked on a FIFO")
        self.assertNotEqual(result.get("outcome"), "read")

    # --- read_project_file ------------------------------------------------------------------------------------------

    def test_u04_read_project_file(self):
        src = self.pdir / "cad" / "src"
        src.mkdir(parents=True)
        (src / "plate.py").write_text(self.script("aces_plate"), encoding="utf-8", newline="\n")
        (src / "notes.txt").write_text("not a script (fixture)\n", encoding="utf-8", newline="\n")
        data = self.store.read_project_file(self.pdir, "cad/src/plate.py", max_bytes=1 << 20, exts=(".py",))
        self.assertEqual(data.decode("utf-8"), self.script("aces_plate"))
        for bad in ("../outside.py", "/etc/passwd.py", "cad/../cad/src/plate.py", "cad/src/notes.txt",
                    "cad\\src\\plate.py", "C:/x.py"):
            self.refused(self.store.read_project_file, self.pdir, bad, max_bytes=1 << 20, exts=(".py",),
                         kinds=("usage", "not_found"))
        self.refused(self.store.read_project_file, self.pdir, "cad/src/missing.py", max_bytes=1 << 20, exts=(".py",),
                     kinds=("not_found",))
        self.refused(self.store.read_project_file, self.pdir, "cad/src/plate.py", max_bytes=10, exts=(".py",))
        outside = self.tmp / "outside.py"
        outside.write_text("print(1)\n", encoding="utf-8", newline="\n")
        try_symlink(self, outside, src / "linked.py")
        self.refused(self.store.read_project_file, self.pdir, "cad/src/linked.py", max_bytes=1 << 20, exts=(".py",),
                     kinds=("usage",))
        try_symlink(self, self.tmp, self.pdir / "cad" / "up", directory=True)
        self.refused(self.store.read_project_file, self.pdir, "cad/up/outside.py", max_bytes=1 << 20, exts=(".py",),
                     kinds=("usage",))

    # --- promote and ensure_subdir ------------------------------------------------------------------------------------

    def promote(self, names, **kw):
        kw.setdefault("max_file_bytes", 1 << 20)
        kw.setdefault("max_total_bytes", 1 << 20)
        return self.store.promote(self.base, names, self.tmp / "dest", **kw)

    def test_u04_promote(self):
        (self.base / "parts.json").write_bytes(b"{}\n")
        (self.base / "p0.brep").write_bytes(b"brep (fixture)\n")
        got = self.promote(["parts.json", "p0.brep"])
        self.assertEqual(sorted(got), ["p0.brep", "parts.json"])
        self.assertEqual(got["p0.brep"]["sha256"], hashlib.sha256(b"brep (fixture)\n").hexdigest())
        self.assertEqual(got["p0.brep"]["bytes"], 15)
        self.assertTrue((self.tmp / "dest" / "p0.brep").is_file())
        self.assertFalse((self.base / "p0.brep").exists(), "promote moves the file")

    def test_u04_promote_refuses_unexpected_names_and_links(self):
        (self.base / "p0.brep").write_bytes(b"brep (fixture)\n")
        for bad in (["../p0.brep"], ["sub/p0.brep"], [".."], ["missing.json"]):
            self.refused(self.promote, bad, kinds=("integrity",))
        (self.base / "big.bin").write_bytes(b"x" * 2048)
        self.refused(self.promote, ["big.bin"], max_file_bytes=1024, kinds=("integrity",))
        self.refused(self.promote, ["big.bin", "p0.brep"], max_total_bytes=2050, kinds=("integrity",))
        self.assertTrue((self.base / "p0.brep").exists(), "nothing is moved when promote refuses")
        try:
            os.link(self.base / "p0.brep", self.tmp / "other-name.brep")
        except OSError as e:
            self.skipTest(f"cannot create hard links here ({e})")
        self.refused(self.promote, ["p0.brep"], kinds=("integrity",))
        self.assertFalse((self.tmp / "dest" / "p0.brep").exists())
        (self.base / "real.json").write_bytes(b"{}\n")
        try_symlink(self, self.base / "real.json", self.base / "parts.json")
        self.refused(self.promote, ["parts.json"], kinds=("integrity",))

    def test_u04_ensure_subdir_replaces_a_file_or_a_link(self):
        (self.base / "inputs").write_bytes(b"a file where a directory belongs (fixture)\n")
        made = self.store.ensure_subdir(self.base, "inputs")
        self.assertTrue(Path(made).is_dir() and not Path(made).is_symlink())
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.txt").write_bytes(b"keep (fixture)\n")
        try_symlink(self, elsewhere, self.base / "renders", directory=True)
        made = self.store.ensure_subdir(self.base, "renders")
        self.assertFalse(Path(made).is_symlink())
        self.assertTrue(Path(made).is_dir())
        self.assertTrue((elsewhere / "keep.txt").exists(), "the link target must be left alone")


if __name__ == "__main__":
    unittest.main()
