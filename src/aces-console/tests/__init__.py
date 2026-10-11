"""aces-console tests (unittest, standard library only):

    python -m unittest discover -s src/aces-console/tests -t src/aces-console

This package is imported before any test module, so it isolates the environment first: ACES_ETC, ACES_USER_CONFIG,
ACES_STATE, ACES_WORK and XDG_DATA_HOME point at a scratch tree (the aces-base etc files are copied in so the modes
table and the defaults are the shipped ones), every token variable is removed, ACES_CONSOLE_ALLOW_FIXTURES is set, the
`aces` package is found in ../aces-base when this is a source tree, and the network guard is installed: a connection
to any host other than loopback raises NonLoopbackConnection and is recorded in BLOCKED. No test reaches the rig; the
fakes in tests/fakes replay the 2026-10-11 captures of tests/fixtures (MANIFEST.json carries their provenance).
Nothing here is test data.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PKG_ROOT = TESTS_DIR.parent                       # src/aces-console (or $srcdir/aces-console; /usr/share/aces/console)
FIXTURES = TESTS_DIR / "fixtures"
SCENARIOS = TESTS_DIR / "fakes" / "scenarios"
_SRC = PKG_ROOT.parent
ACES_BASE = _SRC / "aces-base"
REPO = _SRC.parent
CAD_RECORDS = REPO / "rehearsal" / "reports" / "2026-10-10-aces-cad-vm"

ROOT = Path(tempfile.mkdtemp(prefix="aces-console-test-"))
(ROOT / "etc").mkdir(parents=True, exist_ok=True)
for _name in ("aces.toml", "policy.json", "modes.json"):
    _src = ACES_BASE / "etc" / _name
    if _src.is_file():
        shutil.copyfile(_src, ROOT / "etc" / _name)
for _d in ("cfg", "state", "work", "share"):
    (ROOT / _d).mkdir(parents=True, exist_ok=True)

for _var in ("ACES_GC_TOKEN", "SIMLAB_GC_TOKEN", "ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN", "ACES_CONSOLE_STATIC",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY"):
    os.environ.pop(_var, None)
os.environ.update({"ACES_ETC": str(ROOT / "etc"), "ACES_USER_CONFIG": str(ROOT / "cfg"), "ACES_STATE": str(ROOT / "state"),
                   "ACES_WORK": str(ROOT / "work"), "XDG_DATA_HOME": str(ROOT / "share"),
                   "ACES_CONSOLE_ALLOW_FIXTURES": "1"})

if (ACES_BASE / "aces" / "__init__.py").is_file() and str(ACES_BASE) not in sys.path:
    sys.path.insert(0, str(ACES_BASE))
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

# --- the network guard --------------------------------------------------------------------------------------------------

BLOCKED: list[str] = []
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_REAL_CREATE_CONNECTION = getattr(socket.create_connection, "_aces_real", socket.create_connection)


class NonLoopbackConnection(RuntimeError):
    """A test tried to reach a host other than the loopback fakes."""


def _guarded_create_connection(address, *args, **kwargs):
    host = str(address[0]).lower()
    if host not in LOOPBACK:
        BLOCKED.append(f"{host}:{address[1]}")
        raise NonLoopbackConnection(f"test tried to connect to {host}:{address[1]}; tests may only reach the loopback fakes")
    return _REAL_CREATE_CONNECTION(address, *args, **kwargs)


_guarded_create_connection._aces_real = _REAL_CREATE_CONNECTION  # type: ignore[attr-defined]
socket.create_connection = _guarded_create_connection


def _cleanup() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)


atexit.register(_cleanup)
