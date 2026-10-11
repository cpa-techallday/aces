"""Server-side tokens: where they come from, never what they are.

    gc_token()     -> (value | None, source)   source: "ACES_GC_TOKEN" | "SIMLAB_GC_TOKEN" | "gridcontrol.env" | "not set"
    simlab_token() -> (value | None, source)   source: "ACES_SIMLAB_TOKEN" | "SIMLAB_MCP_TOKEN" | "simlab.env" | "not set"

The GRID CONTROL token (header x-gc-token) is read in this order (build spec 2): $ACES_GC_TOKEN, $SIMLAB_GC_TOKEN (the
variable the rig console uses, cudax simlab/console/app.py:66), then KEY=VALUE lines of $ACES_USER_CONFIG/gridcontrol.env
(default ~/.config/aces/gridcontrol.env). The simlab bearer token follows aces-engineering's simlab_token() exactly
(src/aces-engineering/lib/aces_engineering/mcp_client.py:43-101), copied here so this package does not depend on
aces-engineering. On POSIX a group- or world-readable token file produces one warning naming the file, never the value.

Nothing in this module logs, prints, raises or returns a token except through the two functions above, and no route
of the console returns their values: `sources()` reports only `{configured, source}`. `scrub(text)` replaces any
configured token value inside free text (an upstream reply excerpt, a subprocess tail) with "[masked]".
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Callable

GC_ENVS = ("ACES_GC_TOKEN", "SIMLAB_GC_TOKEN")
GC_FILE = "gridcontrol.env"
SIMLAB_ENVS = ("ACES_SIMLAB_TOKEN", "SIMLAB_MCP_TOKEN")
SIMLAB_FILE = "simlab.env"
MASKED = "[masked]"
_WARNED: set[str] = set()


def user_config_dir() -> Path:
    return Path(os.environ.get("ACES_USER_CONFIG") or (Path.home() / ".config" / "aces"))


def _env_file_token(path: Path, keys: tuple[str, ...]) -> str | None:
    """The first of `keys` with a value in a KEY=VALUE file (# comments, optional `export`, optional quotes)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if " #" in value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key in keys and value:
            found.setdefault(key, value)
    for key in keys:
        if key in found:
            return found[key]
    return None


def _warn_if_readable(path: Path, warn: Callable[[str], None] | None) -> None:
    if os.name != "posix" or str(path) in _WARNED:
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        _WARNED.add(str(path))
        text = f"warning: {path} is readable by other users (mode {stat.S_IMODE(mode):o}); chmod 600 {path}"
        if warn is not None:
            warn(text)
        else:
            print(text, file=sys.stderr)


def _token(envs: tuple[str, ...], filename: str, warn: Callable[[str], None] | None) -> tuple[str | None, str]:
    for key in envs:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value, key
    path = user_config_dir() / filename
    if path.is_file():
        token = _env_file_token(path, envs)
        if token:
            _warn_if_readable(path, warn)
            return token, filename
    return None, "not set"


def gc_token(*, warn: Callable[[str], None] | None = None) -> tuple[str | None, str]:
    """(GRID CONTROL token or None, source). Never log the first element."""
    return _token(GC_ENVS, GC_FILE, warn)


def simlab_token(*, warn: Callable[[str], None] | None = None) -> tuple[str | None, str]:
    """(simlab-mcp bearer token or None, source). Never log the first element."""
    return _token(SIMLAB_ENVS, SIMLAB_FILE, warn)


def sources() -> dict[str, dict[str, object]]:
    """What /api/config reports: configured or not, and where from. No values."""
    gc, gc_src = gc_token()
    sl, sl_src = simlab_token()
    return {"grid_control": {"configured": gc is not None, "source": gc_src},
            "simlab": {"configured": sl is not None, "source": sl_src}}


def scrub(text: str, *tokens: str | None) -> str:
    """`text` with every given token value (default: the configured ones) replaced by "[masked]"."""
    if not isinstance(text, str) or not text:
        return text
    values = [t for t in tokens if t] if tokens else [t for t in (gc_token()[0], simlab_token()[0]) if t]
    for value in values:
        if value and value in text:
            text = text.replace(value, MASKED)
    return text
