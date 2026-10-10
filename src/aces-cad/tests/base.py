"""Test infrastructure for aces-cad (build spec section 21): isolation, the worker python, the sandbox mode, the
network guard and the helpers every test module shares. Not a test module.

- Isolation. CadTestCase patches os.environ for every test: it strips the operator's ACES_*, ONSHAPE_*, proxy and
  colour variables and points HOME, USERPROFILE, ACES_WORK, ACES_STATE, ACES_USER_CONFIG and ACES_CAD_CACHE at a
  fresh temporary directory. ACES_CAD_ACES_CMD is [] (no aces CLI) unless a test calls use_fake_aces().
- The worker python is ACES_CAD_PYTHON as the caller set it, else /opt/aces/engineering/venv/bin/python. Test classes
  with NEEDS_WORKER = True skip with "no worker python: ..." when it cannot import build123d; with
  ACES_CAD_REQUIRE_WORKER=1 (the package check() and bin/install-test) that is a failure instead.
- The sandbox mode is decided once per test run. On Linux x86_64 a doctor probe (the aces-cad CLI in a child process,
  ACES_CAD_SANDBOX=strict, a temporary home) must report both kernel guards active for strict mode; otherwise the
  tests run permissive and print "strict-mode tests skipped: <missing>". The caller's ACES_CAD_SANDBOX=permissive
  (Windows, or bin/install-test after doctor exit 3) always wins.
- The network guard: any connect() from the test process to a host other than loopback raises
  NonLoopbackConnection, is recorded in BLOCKED, and fails the test in tearDown.

The package is driven only through the interfaces of build spec section 5 (ops.dispatch, the CLI, the MCP server, and
the store, sandbox, params and project functions named there). Helpers that read DXF, STEP or STL files run the
worker python as a plain child process, outside any sandbox: they are test tools, never aces-cad code.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
PKG_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = TESTS_DIR / "scripts"
FAKE_ACES = TESTS_DIR / "fake_aces.py"
FAKE_WORKER = TESTS_DIR / "fake_worker.py"
CHECK_BUNDLE = TESTS_DIR / "check_bundle.py"
INSTALLED_LIB = Path("/opt/aces/cad/lib")
DEFAULT_WORKER_PYTHON = "/opt/aces/engineering/venv/bin/python"

SOURCE_TREE = (PKG_ROOT / "aces_cad" / "__init__.py").is_file()
if SOURCE_TREE:
    if str(PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(PKG_ROOT))  # a source tree (src/aces-cad or makepkg's $srcdir/aces-cad)
elif (INSTALLED_LIB / "aces_cad" / "__init__.py").is_file() and str(INSTALLED_LIB) not in sys.path:
    sys.path.append(str(INSTALLED_LIB))  # installed tests; PYTHONPATH=/opt/aces/cad/lib keeps precedence

# What the caller asked for, read before any test strips the ACES_* variables.
CALLER = {k: os.environ.get(k) for k in ("ACES_CAD_PYTHON", "ACES_CAD_REQUIRE_WORKER", "ACES_CAD_SANDBOX")}
REQUIRE_WORKER = CALLER["ACES_CAD_REQUIRE_WORKER"] == "1"
WORKER_PYTHON = CALLER["ACES_CAD_PYTHON"] or DEFAULT_WORKER_PYTHON
POSIX = os.name == "posix"
LINUX_X86_64 = sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64")

PROXY_VARS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY"}
DROP_VARS = {"FORCE_COLOR", "PYTHON_COLORS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "XDG_CACHE_HOME",
             "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "FAKE_ACES_MODE", "FAKE_ACES_LOG",
             "FAKE_WORKER_MODE"}

TOOL_NAMES = ["cad_start", "cad_run", "cad_runs", "cad_inspect", "cad_render", "cad_drawing", "cad_pull", "cad_note"]
RUN_ID_RE = r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$"
# section 8.1: exactly these keys reach a worker (plus the Windows-only five on Windows)
WORKER_ENV_KEYS = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME", "MPLBACKEND", "MPLCONFIGDIR",
                   "XDG_CACHE_HOME", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MALLOC_ARENA_MAX", "NO_COLOR"}
# Section 8.1's Windows keys plus USERPROFILE, which must point at the job's home: on Windows Path.home() reads it,
# and ezdxf raised "Could not determine home directory" at import without it (B3's probe, 2026-10-10).
WORKER_ENV_KEYS_WINDOWS = {"SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "USERPROFILE"}
BACKGROUND_RGB = (0x06, 0x12, 0x25)  # golden STYLE background #061225 (section 2.1)
RENDER_SIZES = {"small": (640, 500), "medium": (1024, 800), "large": (1600, 1250)}

with open(TESTS_DIR / "expected.json", encoding="utf-8") as _fh:
    EXPECTED = json.load(_fh)

# --- the network guard --------------------------------------------------------------------------------------------

BLOCKED: list[str] = []


class NonLoopbackConnection(RuntimeError):
    """A test tried to reach a host other than loopback (not an OSError, so nothing wraps or retries it)."""


def _loopback(address) -> bool:
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX: a local path, not a network
    host = str(address[0]).lower()
    return host in ("localhost", "::1") or host.startswith("127.")


_REAL_CONNECT = getattr(socket.socket.connect, "_aces_real", socket.socket.connect)
_REAL_CONNECT_EX = getattr(socket.socket.connect_ex, "_aces_real", socket.socket.connect_ex)


def _guard(address) -> None:
    if not _loopback(address):
        BLOCKED.append(repr(address))
        raise NonLoopbackConnection(f"test tried to connect to {address!r}; aces-cad tests may only reach loopback")


def _guarded_connect(self, address):
    _guard(address)
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    _guard(address)
    return _REAL_CONNECT_EX(self, address)


_guarded_connect._aces_real = _REAL_CONNECT
_guarded_connect_ex._aces_real = _REAL_CONNECT_EX
socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex

# --- environment ----------------------------------------------------------------------------------------------------


def strip_env(env) -> None:
    for key in list(env):
        if key.startswith(("ACES_", "ONSHAPE_")) or key in DROP_VARS or key.upper() in PROXY_VARS:
            del env[key]


def lib_root() -> Path | None:
    """The directory that holds the aces_cad package in use (a source tree or /opt/aces/cad/lib)."""
    spec = importlib.util.find_spec("aces_cad")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve().parent.parent


def isolated_env(tmp: Path, **extra) -> dict:
    """A complete environment for aces-cad under tmp: no operator settings, temporary home and ACES directories."""
    env = dict(os.environ)
    strip_env(env)
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "ACES_WORK": str(tmp / "work"),
                "ACES_STATE": str(tmp / "state"), "ACES_USER_CONFIG": str(tmp / "config"),
                "ACES_CAD_CACHE": str(tmp / "cache"), "XDG_CACHE_HOME": str(tmp / "xdg-cache"),
                "ACES_CAD_ACES_CMD": "[]", "ACES_CAD_PYTHON": WORKER_PYTHON, "NO_COLOR": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def entry_argv(module: str, call: str) -> list[str]:
    """argv that runs an aces-cad entry point the way the installed shims do (section 22.2), from lib_root()."""
    root = lib_root()
    if root is None:
        raise unittest.SkipTest("the aces_cad package is not importable here")
    code = f"import sys; sys.path.insert(0, {str(root)!r}); from aces_cad.{module} import main; sys.exit({call})"
    return [sys.executable, "-I", "-B", "-c", code]


CLI_ARGV = ("cli", "main(sys.argv[1:])")
MCP_ARGV = ("mcp_server", "main()")

# --- the worker python and the sandbox mode (decided once per test run) --------------------------------------------

_SESSION: dict = {}


def worker_status() -> tuple[bool, str]:
    """(usable, reason): can WORKER_PYTHON import build123d, OCP, trimesh and ezdxf?"""
    if "worker" not in _SESSION:
        py = WORKER_PYTHON
        if not Path(py).exists():
            _SESSION["worker"] = (False, f"no worker python: {py} does not exist (set ACES_CAD_PYTHON to a python "
                                         f"that has build123d, or install aces-engineering)")
        else:
            try:
                done = subprocess.run([py, "-I", "-c", "import build123d, OCP, trimesh, ezdxf; "
                                                       "print(build123d.__version__)"],
                                      capture_output=True, timeout=600)
                if done.returncode == 0:
                    _SESSION["worker"] = (True, f"{py}: build123d {done.stdout.decode().strip()}")
                else:
                    tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["(no output)"]
                    _SESSION["worker"] = (False, f"no worker python: {py} cannot import build123d: {tail[0]}")
            except (OSError, subprocess.TimeoutExpired) as e:
                _SESSION["worker"] = (False, f"no worker python: {py}: {type(e).__name__}: {e}")
    return _SESSION["worker"]


def _doctor_probe() -> tuple[str, list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="aces-cad-doctor-probe-"))
    try:
        env = isolated_env(tmp, ACES_CAD_SANDBOX="strict")
        env["PYTHONPATH"] = str(lib_root() or "")
        done = subprocess.run(entry_argv(*CLI_ARGV) + ["doctor", "--json"], env=env, capture_output=True,
                              timeout=600, cwd=str(tmp))
        try:
            doc = json.loads(done.stdout.decode("utf-8"))
            sandbox = doc["sandbox"]
            active = {"seccomp": bool(sandbox["seccomp"]["active"]), "landlock": bool(sandbox["landlock"]["active"])}
        except (ValueError, KeyError, TypeError) as e:
            reason = (f"the doctor probe gave no usable JSON (exit {done.returncode}, {type(e).__name__}): "
                      + done.stderr.decode("utf-8", "replace").strip()[-400:])
            if REQUIRE_WORKER:
                raise AssertionError(reason) from None
            return "permissive", [reason]
        missing = [name for name, on in active.items() if not on]
        return ("strict", []) if not missing else ("permissive", missing)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sandbox_mode() -> tuple[str, list[str]]:
    """("strict", []) or ("permissive", [what is missing]) for this test run."""
    if "mode" not in _SESSION:
        if CALLER["ACES_CAD_SANDBOX"] == "permissive":
            mode, missing = "permissive", ["ACES_CAD_SANDBOX=permissive was set by the caller"]
        elif not LINUX_X86_64:
            mode, missing = "permissive", [f"the kernel guards are Linux x86_64 only ({sys.platform} "
                                           f"{platform.machine()})"]
        else:
            ok, reason = worker_status()
            mode, missing = _doctor_probe() if ok else ("permissive", [reason])
        if mode == "permissive":
            print(f"strict-mode tests skipped: {', '.join(missing)}", file=sys.stderr, flush=True)
        _SESSION["mode"] = (mode, missing)
    return _SESSION["mode"]


# --- small helpers ----------------------------------------------------------------------------------------------------


def expected(key: str):
    return EXPECTED["values"][key]["value"]


def within(actual, value, tol) -> bool:
    if isinstance(value, dict):
        return isinstance(actual, dict) and all(k in actual and within(actual[k], v, tol) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return (isinstance(actual, (list, tuple)) and len(actual) == len(value)
                and all(within(a, v, tol) for a, v in zip(actual, value)))
    if tol is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return actual == value
    if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual):
        return False
    if "abs" in tol:
        return abs(actual - value) <= tol["abs"]
    return abs(actual - value) <= tol["rel"] * abs(value)


def pid_alive(pid: int) -> bool:
    """Whether pid is a live process (a zombie counts as gone). Never sends a signal on Windows, where os.kill kills."""
    if POSIX:
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            try:
                state = stat.read_text().rsplit(")", 1)[1].split()[0]
            except (OSError, IndexError):
                return False
            return state not in ("Z", "X")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes  # Windows: OpenProcess + GetExitCodeProcess (test process only, never a worker)
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pgid_members(pgid: int) -> list[int]:
    """Live processes in process group pgid (Linux /proc), zombies excluded."""
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            fields = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
            found.append(int(d.name))
    return found


def children_of(ppid: int) -> list[int]:
    """Live direct children of ppid (Linux /proc)."""
    found = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            fields = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == ppid and fields[0] not in ("Z", "X"):
            found.append(int(d.name))
    return found


def read_png(path) -> tuple[int, int, int, list[bytes]]:
    """(width, height, channels, rows) of an 8-bit, non-interlaced PNG (grey, RGB, grey+alpha or RGBA)."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    width, height, depth, ctype, _c, _f, interlace = ihdr
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or interlace or channels is None:
        raise ValueError(f"{path}: unsupported PNG (depth {depth}, colour type {ctype}, interlace {interlace})")
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(height):
        ftype, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if ftype == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def png_pixel(rows: list[bytes], channels: int, x: int, y: int) -> tuple[int, int, int]:
    row = rows[y]
    if channels >= 3:
        return row[x * channels], row[x * channels + 1], row[x * channels + 2]
    return row[x * channels], row[x * channels], row[x * channels]


def non_background_fraction(rows: list[bytes], channels: int, background=BACKGROUND_RGB, step: int = 2) -> float:
    total = other = 0
    for y in range(0, len(rows), step):
        row = rows[y]
        for x in range(0, len(row) // channels, step):
            px = png_pixel(rows, channels, x, y)
            total += 1
            if max(abs(px[k] - background[k]) for k in range(3)) > 8:
                other += 1
    return other / max(total, 1)


# --- the base test case ---------------------------------------------------------------------------------------------


class CadTestCase(unittest.TestCase):
    """Isolated environment and helpers. NEEDS_WORKER = True: the class runs real build123d jobs."""

    maxDiff = None
    NEEDS_WORKER = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.NEEDS_WORKER:
            ok, reason = worker_status()
            if not ok:
                if REQUIRE_WORKER:
                    raise AssertionError(f"{reason} (ACES_CAD_REQUIRE_WORKER=1)")
                raise unittest.SkipTest(reason)

    def setUp(self):
        BLOCKED.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="aces-cad-test-"))
        env_patch = mock.patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        proxy_patch = mock.patch("urllib.request.getproxies", lambda: {})
        proxy_patch.start()
        self.addCleanup(proxy_patch.stop)
        mode = sandbox_mode()[0] if self.NEEDS_WORKER else (CALLER["ACES_CAD_SANDBOX"] or "permissive")
        env = isolated_env(self.tmp, ACES_CAD_SANDBOX=mode)
        os.environ.clear()
        os.environ.update(env)
        self.mode = mode

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        blocked = list(BLOCKED)
        BLOCKED.clear()
        self.assertEqual(blocked, [], "a test connected to a non-loopback host")

    # --- skips -----------------------------------------------------------------------------------------------------

    def require_strict(self) -> None:
        mode, missing = sandbox_mode()
        if mode != "strict":
            self.skipTest(f"strict-mode tests skipped: {', '.join(missing)}")

    def require_posix(self, what: str = "this check") -> None:
        if not POSIX:
            self.skipTest(f"{what} is POSIX only")

    def require_linux(self, what: str = "this check") -> None:
        if not sys.platform.startswith("linux"):
            self.skipTest(f"{what} is Linux only")

    # --- projects, fakes, scripts ----------------------------------------------------------------------------------

    def make_project(self, name: str, cls: str | None, *, class_line: str | None = None,
                     extra_lines: str = "") -> Path:
        """aces.yaml in aces-base's _dump format; class_line, when given, replaces the class line verbatim."""
        pdir = Path(os.environ["ACES_WORK"]) / name
        pdir.mkdir(parents=True, exist_ok=True)
        line = class_line if class_line is not None else f"class: {'null' if cls is None else cls}"
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = (f"name: {name}\n{line}\ncreated: \"{created}\"\ncompany: \"\"\nrd_activity: \"\"\nhypothesis: \"\"\n"
                f"cad_sources: []\ncomponents: []\nevidence: []\n{extra_lines}")
        (pdir / "aces.yaml").write_bytes(text.encode("utf-8"))
        return pdir

    def use_fake_aces(self, mode: str = "new") -> Path:
        """ACES_CAD_ACES_CMD = [python, tests/fake_aces.py]; returns the fake's call log."""
        log = self.tmp / "fake-aces.log"
        os.environ["ACES_CAD_ACES_CMD"] = json.dumps([sys.executable, str(FAKE_ACES)])
        os.environ["FAKE_ACES_MODE"] = mode
        os.environ["FAKE_ACES_LOG"] = str(log)
        return log

    def fake_aces_calls(self, log: Path) -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def use_fake_worker(self, mode: str, *args: str) -> None:
        """ACES_CAD_WORKER_CMD = the fake worker in MODE. The parent passes a worker only the allowlisted environment
        (section 8.1), so the mode travels as the command's argument, not as FAKE_WORKER_MODE."""
        os.environ["ACES_CAD_WORKER_CMD"] = json.dumps([sys.executable, str(FAKE_WORKER), mode, *args])

    @staticmethod
    def script(name: str) -> str:
        return (SCRIPTS_DIR / f"{name}.py").read_text(encoding="utf-8")

    # --- the package -----------------------------------------------------------------------------------------------

    @staticmethod
    def modules():
        from aces_cad import errors, ops
        return errors, ops

    # The op and script names are positional-only: "name" is also a tool argument (the run's name).
    def op(self, op_name: str, /, **kwargs) -> dict:
        _errors, ops = self.modules()
        return ops.dispatch(op_name, kwargs)

    def op_error(self, op_name: str, kind: str | tuple[str, ...] | None = None, /, **kwargs) -> dict:
        """Run an op that must fail; returns its error dict (CadError.to_dict()) after checking the kind."""
        errors, ops = self.modules()
        with self.assertRaises(errors.CadError) as ctx:
            ops.dispatch(op_name, kwargs)
        err = ctx.exception.to_dict()
        if kind is not None:
            kinds = (kind,) if isinstance(kind, str) else kind
            self.assertIn(err["kind"], kinds, err)
        self.assertEqual(err["exit_code"], ctx.exception.exit_code)
        return err

    def run_script(self, script_name: str, /, project: str = "p", **kwargs) -> dict:
        kwargs.setdefault("name", script_name.replace("_", "-")[:40])
        return self.op("run", project=project, script=self.script(script_name), **kwargs)

    def run_error(self, script_name: str, kind=None, /, project: str = "p", **kwargs) -> dict:
        kwargs.setdefault("name", script_name.replace("_", "-")[:40])
        return self.op_error("run", kind, project=project, script=self.script(script_name), **kwargs)

    def runs_dir(self, project: str = "p") -> Path:
        return Path(os.environ["ACES_WORK"]) / project / "cad" / "local" / "runs"

    def cli(self, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
        """The aces-cad CLI in this process (cli.main), with stdout and stderr captured."""
        from aces_cad import cli
        out, err = io.StringIO(), io.StringIO()
        patches = [contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)]
        if stdin is not None:
            fake_in = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
            patches.append(mock.patch.object(sys, "stdin", fake_in))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def cli_json(self, *argv: str) -> tuple[int, dict]:
        code, out, err = self.cli(*argv, "--json")
        try:
            return code, json.loads(out)
        except ValueError:
            self.fail(f"aces-cad {' '.join(argv)} --json printed no single JSON document (exit {code}): "
                      f"stdout {out[:400]!r} stderr {err[:400]!r}")

    def child_env(self, **extra) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(lib_root() or "")
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def cli_process(self, *argv: str, input: bytes | None = None, env: dict | None = None,
                    timeout: float = 600) -> subprocess.CompletedProcess:
        """The aces-cad CLI as a child process, run the way the installed shim runs it."""
        return subprocess.run(entry_argv(*CLI_ARGV) + list(argv), input=input, capture_output=True,
                              env=env if env is not None else self.child_env(), timeout=timeout, cwd=str(self.tmp))

    def worker_python(self, code: str, *args: str, timeout: float = 600) -> subprocess.CompletedProcess:
        """Run code in the worker python as a plain child process (a test tool: no sandbox, no aces-cad)."""
        env = dict(os.environ)
        return subprocess.run([WORKER_PYTHON, "-I", "-c", code, *args], capture_output=True, env=env,
                              timeout=timeout, cwd=str(self.tmp))

    def worker_python_json(self, code: str, *args: str, timeout: float = 600):
        done = self.worker_python(code, *args, timeout=timeout)
        lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
        if done.returncode != 0 or not lines:
            self.fail(f"worker-python helper failed (exit {done.returncode}): "
                      f"{done.stderr.decode('utf-8', 'replace')[-1500:]}")
        return json.loads(lines[-1])

    def make_step(self, path: Path, label: str = "vendor-part", size: float = 10.0) -> Path:
        """A real STEP file (a labelled cube) made by build123d in the worker python, outside any sandbox."""
        path.parent.mkdir(parents=True, exist_ok=True)
        code = ("import sys\nfrom build123d import Box, export_step\nb = Box(float(sys.argv[3]), float(sys.argv[3]), "
                "float(sys.argv[3]))\nb.label = sys.argv[2]\nexport_step(b, sys.argv[1])\nprint('{}')")
        done = self.worker_python(code, str(path), label, str(size))
        if done.returncode != 0 or not path.exists():
            self.fail(f"could not make the STEP fixture {path}: {done.stderr.decode('utf-8', 'replace')[-800:]}")
        return path

    # --- assertions ------------------------------------------------------------------------------------------------

    def assert_expected(self, actual, key: str, msg: str | None = None) -> None:
        entry = EXPECTED["values"][key]
        if not within(actual, entry["value"], entry["tol"]):
            self.fail(f"{key}: got {actual!r}, expected {entry['value']!r} within {entry['tol']} "
                      f"({entry['source']}){': ' + msg if msg else ''}")

    def assert_close(self, actual, value, tol: float, msg: str | None = None) -> None:
        if not within(actual, value, {"abs": tol}):
            self.fail(f"got {actual!r}, expected {value!r} within {tol}{': ' + msg if msg else ''}")

    def assert_run_id(self, value) -> None:
        self.assertIsInstance(value, str)
        self.assertRegex(value, RUN_ID_RE)

    def assert_ascii_json_lines(self, lines: list[bytes]) -> None:
        for line in lines:
            self.assertTrue(line.isascii(), line[:120])

    def files_under(self, root: Path | None = None) -> list[Path]:
        root = root or self.tmp
        return [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]

    def assert_marker_absent(self, marker: str, *texts, root: Path | None = None) -> None:
        """marker is in none of the texts and no file under root (default: the project tree)."""
        needle = marker.encode("utf-8")
        for t in texts:
            blob = t if isinstance(t, (bytes, bytearray)) else json.dumps(t, ensure_ascii=False).encode("utf-8")
            self.assertNotIn(needle, blob, "the marker leaked into an output")
        for path in self.files_under(root or Path(os.environ["ACES_WORK"])):
            self.assertNotIn(needle, path.read_bytes(), f"the marker leaked into {path}")

    def wait_for(self, predicate, timeout: float, interval: float = 0.05, what: str = "condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        self.fail(f"timed out after {timeout} s waiting for {what}")


class WorkerTestCase(CadTestCase):
    """Runs real build123d jobs through the worker python (skips or fails per ACES_CAD_REQUIRE_WORKER)."""

    NEEDS_WORKER = True

    def setUp(self):
        super().setUp()
        self.make_project("p", "sovereign")


def strip_ansi_present(text: str) -> bool:
    return bool(re.search(r"\x1b\[[0-9;?]*[A-Za-z]", text))
