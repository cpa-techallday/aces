"""S01-S05 of build spec section 21: static checks that need no worker.

S01 parent modules import the standard library only, never the network stack (and no shell=True, no ctypes _pack_
anywhere in the package: the code rules of section 4.3). S02 worker modules import heavy libraries only inside
functions, and only distributions in the engineering lock (never shapely or manifold3d); the lock-file part skips when
src/aces-engineering/requirements.txt is absent. S03 ezdxf and lib3mf are pinned in that lock; without the lock (an
installed or makepkg run) the worker venv must import both. S04 text hygiene and the 3.11 grammar, plus the fixture
script rules. S05 decodes seccomp.program(pid=4242) and runs it on synthetic seccomp_data.
"""
from __future__ import annotations

import ast
import errno
import json
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

import base
from base import CadTestCase

FORBIDDEN_PARENT = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "xmlrpc"}
LOCK_MAP = {"build123d": "build123d", "OCP": "cadquery-ocp-novtk", "numpy": "numpy", "scipy": "scipy",
            "skimage": "scikit-image", "trimesh": "trimesh", "matplotlib": "matplotlib", "mpl_toolkits": "matplotlib",
            "ezdxf": "ezdxf", "lib3mf": "lib3mf"}
NEVER = {"shapely", "manifold3d"}
LOCK_FILE = base.PKG_ROOT.parent / "aces-engineering" / "requirements.txt"
STDLIB = set(sys.stdlib_module_names)
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".example", ".toml", ".cfg", ".sh", ""}
BROKEN_ON_PURPOSE = {"syntax_error.py"}


def package_dir() -> Path | None:
    root = base.lib_root()
    return None if root is None else root / "aces_cad"


def require_package(test: unittest.TestCase) -> Path:
    pkg = package_dir()
    if pkg is None or not (pkg / "__init__.py").is_file():
        test.skipTest("the aces_cad package is not in this tree (B1 and B2 provide it)")
    return pkg


def imported_names(tree: ast.AST):
    """(top-level module name, line, inside_function) for every import and every literal __import__/import_module."""
    found = []

    def visit(node, in_function):
        for child in ast.iter_child_nodes(node):
            fn = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(child, ast.Import):
                for alias in child.names:
                    found.append((alias.name.split(".")[0], child.lineno, in_function))
            elif isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module:
                    found.append((child.module.split(".")[0], child.lineno, in_function))
            elif isinstance(child, ast.Call) and child.args and isinstance(child.args[0], ast.Constant) \
                    and isinstance(child.args[0].value, str):
                f = child.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                if name in ("__import__", "import_module"):
                    found.append((child.args[0].value.split(".")[0], child.lineno, in_function))
            visit(child, fn)

    visit(tree, False)
    return found


def lock_pins() -> dict[str, str]:
    pins = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\;]+)", line)
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return pins


class StaticTest(CadTestCase):

    def test_s01_parent_modules_use_the_standard_library_only(self):
        pkg = require_package(self)
        problems = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name, line, _fn in imported_names(tree):
                if name in FORBIDDEN_PARENT:
                    problems.append(f"{path.name}:{line} imports {name} (no network code in the parent)")
                elif name != "aces_cad" and name not in STDLIB:
                    problems.append(f"{path.name}:{line} imports {name}, which is not in the standard library")
        self.assertEqual(problems, [])

    def test_s01b_code_rules_no_shell_true_no_pack(self):
        pkg = require_package(self)
        problems = []
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "shell" and not (
                        isinstance(node.value, ast.Constant) and node.value.value is False):
                    problems.append(f"{path.relative_to(pkg)}:{node.value.lineno} passes shell=")
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_pack_"
                                                        for t in node.targets):
                    problems.append(f"{path.relative_to(pkg)}:{node.lineno} sets _pack_ (use struct)")
        self.assertEqual(problems, [])

    def test_s02_worker_modules_import_heavy_libraries_inside_functions_only(self):
        pkg = require_package(self)
        worker = pkg / "worker"
        self.assertTrue((worker / "__init__.py").is_file(), "aces_cad/worker is missing")
        problems = []
        for path in sorted(worker.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name, line, in_function in imported_names(tree):
                where = f"worker/{path.name}:{line}"
                if name in NEVER:
                    problems.append(f"{where} imports {name}, which is not in the engineering lock")
                elif name == "aces_cad" or name in STDLIB:
                    continue
                elif name not in LOCK_MAP:
                    problems.append(f"{where} imports {name}, which maps to no distribution of the engineering lock")
                elif not in_function:
                    problems.append(f"{where} imports {name} at module level (heavy imports go inside functions)")
        main = worker / "main.py"
        self.assertTrue(main.is_file(), "aces_cad/worker/main.py is missing")
        tree = ast.parse(main.read_text(encoding="utf-8"))
        tables = [n.value for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "IMPORTS" for t in n.targets)]
        self.assertEqual(len(tables), 1, "worker/main.py must define one IMPORTS table (section 5.3)")
        for value in ast.walk(tables[0]):
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and "." not in value.value[:1]:
                top = value.value.split(".")[0]
                if top in ("run", "inspect", "render", "drawing", "export", "env", "selftest"):
                    continue  # the op names (dict keys)
                if top in NEVER or top not in LOCK_MAP:
                    problems.append(f"worker/main.py IMPORTS names {value.value}, not a lock distribution")
        self.assertEqual(problems, [])

    def test_s02b_every_worker_distribution_is_in_the_engineering_lock(self):
        if not LOCK_FILE.is_file():
            self.skipTest(f"{LOCK_FILE} is absent (installed tests or a makepkg tree)")
        pins = lock_pins()
        missing = sorted({dist for dist in LOCK_MAP.values() if dist not in pins})
        self.assertEqual(missing, [], f"distributions the worker imports are not pinned in {LOCK_FILE}")
        self.assertNotIn("shapely", pins)
        self.assertNotIn("manifold3d", pins)

    def test_s03_ezdxf_and_lib3mf_are_in_the_lock(self):
        if LOCK_FILE.is_file():
            text = LOCK_FILE.read_text(encoding="utf-8")
            for dist in ("ezdxf", "lib3mf"):
                self.assertRegex(text, rf"(?m)^{dist}==", f"{dist} is no longer pinned in {LOCK_FILE}: re-locking "
                                                          f"dropped it, and aces-cad drawings or 3MF export need it")
            return
        ok, reason = base.worker_status()
        if not ok:
            if base.REQUIRE_WORKER:
                self.fail(reason)
            self.skipTest(f"no lock file and {reason}")
        done = subprocess.run([base.WORKER_PYTHON, "-I", "-c", "import ezdxf, lib3mf, json, importlib.metadata as m; "
                               "print(json.dumps({d: m.version(d) for d in ('ezdxf', 'lib3mf')}))"],
                              capture_output=True, timeout=300)
        self.assertEqual(done.returncode, 0, "the worker venv cannot import ezdxf and lib3mf: "
                         + done.stderr.decode("utf-8", "replace")[-600:])
        versions = json.loads(done.stdout.decode().strip().splitlines()[-1])
        self.assertEqual(sorted(versions), ["ezdxf", "lib3mf"])

    def test_s04_text_hygiene_and_the_311_grammar(self):
        roots = [base.PKG_ROOT] if base.SOURCE_TREE else [base.TESTS_DIR] + ([package_dir()] if package_dir() else [])
        problems, seen = [], 0
        for root in roots:
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                if "__pycache__" in path.parts or path.suffix in (".pyc", ".png", ".step", ".stl", ".3mf", ".gz"):
                    continue
                if path.suffix not in TEXT_SUFFIXES:
                    continue
                data = path.read_bytes()
                if b"\0" in data:
                    continue
                seen += 1
                rel = path.relative_to(root)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    problems.append(f"{rel}: not UTF-8")
                    continue
                if "\r" in text:
                    problems.append(f"{rel}: CR line endings")
                if text and not text.endswith("\n"):
                    problems.append(f"{rel}: no final newline")
                for i, line in enumerate(text.split("\n"), 1):
                    if line != line.rstrip():
                        problems.append(f"{rel}:{i}: trailing whitespace")
                    if path.suffix == ".py" and len(line) > 120:
                        problems.append(f"{rel}:{i}: {len(line)} columns (at most 120)")
                if path.suffix == ".py" and path.name not in BROKEN_ON_PURPOSE:
                    try:
                        ast.parse(text, filename=str(rel), feature_version=(3, 11))
                    except SyntaxError as e:
                        problems.append(f"{rel}: does not parse with the 3.11 grammar: {e}")
        self.assertGreater(seen, 10)
        self.assertEqual(problems, [])

    def test_s04b_fixture_scripts_have_no_single_quotes_or_dollars(self):
        scripts = sorted(base.SCRIPTS_DIR.glob("*.py"))
        self.assertGreaterEqual(len(scripts), 50)
        bad = [p.name for p in scripts if "'" in p.read_text(encoding="utf-8") or "$" in p.read_text(encoding="utf-8")]
        self.assertEqual(bad, [], "fixture scripts are reused inside bin/install-test's single-quoted block")
        with self.assertRaises(SyntaxError):
            ast.parse((base.SCRIPTS_DIR / "syntax_error.py").read_text(encoding="utf-8"))

    def test_s04c_the_gyroid_fixture_is_the_skill_example(self):
        """tests/scripts/skill_gyroid.py is the SKILL.md implicit-field example verbatim, so the W13 test that runs and
        pulls it tests what agents copy (review finding 3)."""
        skill = base.TESTS_DIR.parent / "skills" / "aces-cad" / "SKILL.md"
        if not skill.is_file():
            self.skipTest(f"{skill} is absent")
        blocks = re.findall(r"```python\n(.*?)```", skill.read_text(encoding="utf-8"), flags=re.S)
        gyroid = [b for b in blocks if "mesh_from_sdf(sdf" in b]
        self.assertEqual(len(gyroid), 1, "SKILL.md has one implicit-field example")
        self.assertEqual(gyroid[0], (base.SCRIPTS_DIR / "skill_gyroid.py").read_text(encoding="utf-8"))

    def test_s05_the_seccomp_program(self):
        require_package(self)
        from aces_cad.worker import seccomp
        prog = decode(seccomp.program(pid=4242))
        self.assertEqual(len(prog), 106, "section 8.4: 106 instructions")
        self.assertEqual(prog[0], (0x20, 0, 0, 4), "the first instruction loads the architecture")
        allow, eperm = 0x7FFF0000, 0x00050000 | errno.EPERM
        run = BpfRun(prog)
        # architecture and x32
        self.assertEqual(run(41, arch=0x40000003), eperm, "a foreign architecture is refused")
        self.assertEqual(run(0x40000000 + 41), eperm, "x32 system calls are refused")
        # socket, clone3, clone
        self.assertEqual(run(41), 0x00050000 | errno.EACCES)
        self.assertEqual(run(435), 0x00050000 | errno.ENOSYS)
        self.assertEqual(run(56, args=(0x11,)), eperm, "clone without CLONE_THREAD (a fork) is refused")
        self.assertEqual(run(56, args=(0x3D0F00,)), allow, "clone with CLONE_THREAD (a thread) is allowed")
        # own pid only
        for nr in (62, 234, 129, 297):
            self.assertEqual(run(nr, args=(4242, 0)), allow, nr)
            for other in (1, 4243, 0, 0xFFFFFFFF):
                self.assertEqual(run(nr, args=(other, 0)), eperm, (nr, other))
        # arg0 in {own pid, 0}
        for nr in (302, 203):
            for ok in (4242, 0):
                self.assertEqual(run(nr, args=(ok,)), allow, (nr, ok))
            self.assertEqual(run(nr, args=(1,)), eperm, nr)
        # setpriority: PRIO_PROCESS and who in {0, own pid}
        self.assertEqual(run(141, args=(0, 0)), allow)
        self.assertEqual(run(141, args=(0, 4242)), allow)
        self.assertEqual(run(141, args=(0, 1)), eperm)
        self.assertEqual(run(141, args=(1, 0)), eperm, "PRIO_PGRP is refused")
        self.assertEqual(run(141, args=(2, 0)), eperm, "PRIO_USER is refused")
        # the EPERM list
        for nr in DENY_EPERM:
            self.assertEqual(run(nr), eperm, nr)
        # ordinary calls stay allowed
        for nr in (0, 1, 3, 9, 39, 202, 231, 257):
            self.assertEqual(run(nr), allow, nr)
        # the syscall numbers compared against nr, in the order of section 8.4
        self.assertEqual(nr_compares(prog), ORDER)
        own = [k for (code, _jt, _jf, k), src in zip(prog, sources(prog)) if code == 0x15 and k == 4242]
        self.assertEqual(len(own), 7, "the own-pid compares: 4 own-pid-only, 2 in {pid, 0}, setpriority's who")
        self.assertNotEqual(decode(seccomp.program(pid=7)), prog, "the program embeds the pid")


# --- a small classic-BPF interpreter for S05 ---------------------------------------------------------------------

DENY_EPERM = (57, 58, 59, 322, 101, 272, 308, 248, 249, 250, 298, 310, 311, 321, 425, 426, 427, 200, 434, 424, 438,
              312, 440, 323, 251)
ORDER = [41, 435, 56, 62, 234, 129, 297, 302, 203, 141] + list(DENY_EPERM)


def decode(prog) -> list[tuple[int, int, int, int]]:
    """Normalise program(): bytes, (bytes, n), or a list of 8-byte instructions or (code, jt, jf, k) tuples.
    Section 8.4 fixes the encoding (struct "<HBBI") but not the Python return type."""
    if isinstance(prog, tuple) and len(prog) == 2 and isinstance(prog[0], (bytes, bytearray)):
        data, n = prog
        out = decode(bytes(data))
        assert len(out) == n, (len(out), n)
        return out
    if isinstance(prog, (bytes, bytearray)):
        assert len(prog) % 8 == 0
        return [struct.unpack_from("<HBBI", prog, i) for i in range(0, len(prog), 8)]
    out = []
    for ins in prog:
        if isinstance(ins, (bytes, bytearray)):
            out.append(struct.unpack("<HBBI", ins))
        else:
            out.append(tuple(int(v) for v in ins))
    return out


class BpfRun:
    """Runs a seccomp program on struct seccomp_data {u32 nr; u32 arch; u64 ip; u64 args[6]} (little endian)."""

    def __init__(self, prog):
        self.prog = prog

    def __call__(self, nr: int, arch: int = 0xC000003E, args=()) -> int:
        a6 = list(args) + [0] * (6 - len(args))
        data = struct.pack("<IIQ6Q", nr & 0xFFFFFFFF, arch, 0, *[v & 0xFFFFFFFFFFFFFFFF for v in a6])
        acc, pc, steps = 0, 0, 0
        while True:
            steps += 1
            assert steps < 10_000 and 0 <= pc < len(self.prog), "the program runs off its end or loops"
            code, jt, jf, k = self.prog[pc]
            cls = code & 0x07
            if code == 0x20:  # BPF_LD | BPF_W | BPF_ABS
                acc = struct.unpack_from("<I", data, k)[0]
                pc += 1
            elif cls == 0x05:  # BPF_JMP with a constant operand
                op = code & 0xF0
                assert code & 0x08 == 0, "only BPF_K jumps are expected"
                if op == 0x00:
                    pc += 1 + k
                    continue
                cond = {0x10: acc == k, 0x20: acc > k, 0x30: acc >= k, 0x40: bool(acc & k)}[op]
                pc += 1 + (jt if cond else jf)
            elif code == 0x06:  # BPF_RET | BPF_K
                return k
            else:
                raise AssertionError(f"unexpected BPF instruction {code:#x} at {pc}")


def sources(prog) -> list[object]:
    """For each instruction, the seccomp_data offset the accumulator was loaded from (None, or "mixed")."""
    src: list[object] = [None] * (len(prog) + 1)
    seen = [False] * (len(prog) + 1)
    seen[0] = True
    for pc, (code, jt, jf, k) in enumerate(prog):
        if not seen[pc]:
            continue
        after = k if code == 0x20 else src[pc]
        if code & 0x07 == 0x05:
            succ = [pc + 1 + k] if code & 0xF0 == 0x00 else [pc + 1 + jt, pc + 1 + jf]
        elif code == 0x06:
            succ = []
        else:
            succ = [pc + 1]
        for s in succ:
            if s <= len(prog):
                src[s] = after if not seen[s] or src[s] == after else "mixed"
                seen[s] = True
    return src[:len(prog)]


def nr_compares(prog) -> list[int]:
    return [k for (code, _jt, _jf, k), src in zip(prog, sources(prog)) if code == 0x15 and src == 0]


if __name__ == "__main__":
    unittest.main()
