"""The [console] configuration and the state paths.

`load()` reads `aces.config.load()` (built-in defaults <- /etc/aces/aces.toml <- ~/.config/aces/aces.toml,
src/aces-base/aces/config.py:56-61; `_merge` keeps unknown tables, :36-43, so a `[console]` table reaches the console
with aces-base 0.1.0-3) and applies the in-package defaults for a missing table (build spec 2):

    bind = "127.0.0.1"   port = 8070   static_dir = "/usr/share/aces/console/static"   state_dir = "$ACES_STATE/console"

Environment: ACES_CONSOLE_STATIC overrides static_dir (development). When the configured static tree does not exist and
this package runs from a source tree (src/aces-console/aces_console), the sibling static/ is used and reported as
`static_source: "source-tree"`; a missing tree is reported, never invented. Fixtures mode (`serve --fixtures DIR`) is
accepted only with ACES_CONSOLE_ALLOW_FIXTURES=1 in the environment (spec 3.1).

The bind address is loopback only in v1: anything but 127.0.0.1 or ::1 is refused with LAN_REFUSAL.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aces import config as aces_config

from . import FIXTURE_STAMP

CONSOLE_DEFAULTS: dict[str, Any] = {
    "bind": "127.0.0.1",
    "port": 8070,
    "static_dir": "/usr/share/aces/console/static",
    "state_dir": None,  # resolved at load time: $ACES_STATE/console
}
LOOPBACK_BINDS = ("127.0.0.1", "::1", "localhost")
LAN_REFUSAL = ("aces-console: bind {bind} refused: v1 binds loopback only (127.0.0.1 or ::1); LAN/VPN access needs "
               "the authenticated shared API (docs/POCKET_BRIEF.md:44-53, 90-98)")
FIXTURES_REFUSAL = "aces-console: --fixtures is accepted only with ACES_CONSOLE_ALLOW_FIXTURES=1 in the environment"
SOURCE_STATIC = Path(__file__).resolve().parent.parent / "static"


def is_loopback(bind: str) -> bool:
    return bind in LOOPBACK_BINDS


def origin_for(bind: str, port: int) -> str:
    host = f"[{bind}]" if ":" in bind else bind
    return f"http://{host}:{int(port)}"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """The effective configuration of one server. `cfg` is the whole aces configuration (server, fabric, lanes,
    console); the console keys are lifted to attributes."""

    def __init__(self, cfg: dict[str, Any], bind: str, port: int, static_dir: Path, static_source: str,
                 state_dir: Path, fixtures_dir: Path | None):
        self.cfg = cfg
        self.server: dict[str, Any] = dict(cfg.get("server") or {})
        self.bind = bind
        self.port = int(port)
        self.origin = origin_for(bind, port)
        self.static_dir = Path(static_dir)
        self.static_source = static_source
        self.state_dir = Path(state_dir)
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.fixture: str | None = FIXTURE_STAMP if fixtures_dir else None
        try:
            self.timeout_s = float(self.server.get("timeout_s") or 4)
        except (TypeError, ValueError):
            self.timeout_s = 4.0

    @property
    def url(self) -> str:
        return self.origin

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "server.pid"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "server.log"

    def console_table(self) -> dict[str, Any]:
        return {"bind": self.bind, "port": self.port, "origin": self.origin, "state_dir": str(self.state_dir),
                "static_dir": str(self.static_dir), "static_source": self.static_source,
                "static_present": (self.static_dir / "index.html").is_file(), "fixtures": self.fixture}

    def paths(self) -> dict[str, str]:
        return {"work": str(aces_config.WORK), "state": str(aces_config.STATE),
                "user_config": str(aces_config.USER_CONFIG), "data_dir": str(data_dir()), "etc": str(aces_config.ETC)}


def data_dir() -> Path:
    """$XDG_DATA_HOME/aces (default ~/.local/share/aces): where the desktop simlab evidence ledger lives
    (src/aces-engineering/lib/aces_engineering/__init__.py:20-23)."""
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "aces"


def load(bind: str | None = None, port: int | None = None, static: str | None = None, fixtures: str | None = None,
         overrides: dict[str, Any] | None = None) -> Settings:
    """The effective Settings. Arguments beat the environment, which beats the [console] table, which beats the
    in-package defaults. `overrides` is merged over the loaded aces configuration (tests and `serve` use it).
    Raises SystemExit for a non-loopback bind or an unauthorised --fixtures."""
    cfg = aces_config.load()
    if overrides:
        cfg = _merge(cfg, overrides)
    table = dict(CONSOLE_DEFAULTS)
    table.update({k: v for k, v in (cfg.get("console") or {}).items() if v is not None})
    cfg["console"] = table
    bind = str(bind or table.get("bind") or CONSOLE_DEFAULTS["bind"])
    if not is_loopback(bind):
        raise SystemExit(LAN_REFUSAL.format(bind=bind))
    try:
        port = int(port if port is not None else table.get("port") or CONSOLE_DEFAULTS["port"])
    except (TypeError, ValueError):
        raise SystemExit(f"aces-console: port must be an integer, got {table.get('port')!r}") from None
    if not 1 <= port <= 65535:
        raise SystemExit(f"aces-console: port {port} is out of range")
    state_dir = Path(table.get("state_dir") or (aces_config.STATE / "console"))
    if static:
        static_dir, source = Path(static), "arg"
    elif os.environ.get("ACES_CONSOLE_STATIC"):
        static_dir, source = Path(os.environ["ACES_CONSOLE_STATIC"]), "env"
    else:
        static_dir, source = Path(table.get("static_dir") or CONSOLE_DEFAULTS["static_dir"]), "config"
        if not (static_dir / "index.html").is_file() and (SOURCE_STATIC / "index.html").is_file():
            static_dir, source = SOURCE_STATIC, "source-tree"
    if fixtures:
        if os.environ.get("ACES_CONSOLE_ALLOW_FIXTURES") != "1":
            raise SystemExit(FIXTURES_REFUSAL)
        if not Path(fixtures).is_dir():
            raise SystemExit(f"aces-console: fixtures directory {fixtures} does not exist")
    return Settings(cfg, bind, port, static_dir, source, state_dir, Path(fixtures) if fixtures else None)
