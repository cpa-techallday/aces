"""Settings: the Onshape API keys, the base URL, the ACES paths, and the redactor that keeps the keys out of output.

Keys and their base URL come from the same source:

1. Both ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY in the environment: the keys come from the environment, the base URL
   is the environment's ONSHAPE_BASE_URL or the default, and the credentials file is not read at all.
2. Exactly one of them in the environment: an AuthError naming the missing one.
3. Neither: the credentials file <ACES_USER_CONFIG>/onshape.env (default ~/.config/aces/onshape.env) is read. With
   both keys there, the base URL is the environment's ONSHAPE_BASE_URL, else the file's, else the default.

onshape.env holds KEY=value lines (an optional leading `export ` is ignored, one pair of matching quotes is removed,
blank lines and lines starting with # are skipped). Inline comments are not supported: `KEY=abc # note` gives the
value `abc # note`, which the key rule then rejects. An empty value counts as unset, in the file and in the
environment. On Linux the file must be mode 0600 and belong to the user. Key values are never echoed, printed,
logged or written anywhere; every front end passes its output through a Redactor built from them.
"""
from __future__ import annotations

import base64
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from .errors import AuthError, ConfigError

DEFAULT_BASE_URL = "https://cad.onshape.com/api/v17"
ACCESS_VAR = "ONSHAPE_ACCESS_KEY"
SECRET_VAR = "ONSHAPE_SECRET_KEY"
BASE_URL_VAR = "ONSHAPE_BASE_URL"
FILE_KEYS = (ACCESS_VAR, SECRET_VAR, BASE_URL_VAR)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
KEY_RE = r"[\x21-\x7e]{1,512}"
DEV_PORTAL = "https://cad.onshape.com/appstore/dev-portal"
REDACTED = "[redacted]"


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _get(env: Mapping[str, str] | None, name: str) -> str | None:
    """An environment value, stripped; empty counts as unset."""
    value = _environ(env).get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    value = _get(env, "ACES_USER_CONFIG")
    return Path(value) if value else _home() / ".config" / "aces"


def work_dir(env: Mapping[str, str] | None = None) -> Path:
    value = _get(env, "ACES_WORK")
    return Path(value) if value else _home() / "Work"


def credentials_path(env: Mapping[str, str] | None = None) -> Path:
    return user_config_dir(env) / "onshape.env"


def missing_keys_message(path: Path | str) -> str:
    return (f"no Onshape API keys: set {ACCESS_VAR} and {SECRET_VAR}, or write them as KEY=value lines to {path} "
            f"(chmod 600). Create keys in the Onshape developer portal: {DEV_PORTAL}")


class Redactor:
    """Replaces every known secret value (at least 8 characters long) with "[redacted]"."""

    MIN_LENGTH = 8

    def __init__(self, secrets: Iterable[str]):
        values: set[str] = set()
        for s in secrets:
            if isinstance(s, str) and len(s) >= self.MIN_LENGTH:
                values.add(s)
                # the same value as it appears inside JSON text, written with or without \u escapes for non-ASCII
                for ascii_only in (False, True):
                    escaped = json.dumps(s, ensure_ascii=ascii_only)[1:-1]
                    if escaped != s:
                        values.add(escaped)
        self._values = tuple(sorted(values, key=len, reverse=True))

    def __repr__(self) -> str:
        return f"Redactor({len(self._values)} values)"

    def __bool__(self) -> bool:
        return bool(self._values)

    def redact(self, text: str) -> str:
        if not isinstance(text, str) or not self._values:
            return text
        for value in self._values:
            if value in text:
                text = text.replace(value, REDACTED)
        return text

    def redact_obj(self, obj) -> tuple[object, bool]:
        """Redact every string inside dicts and lists, keys included; the bool says whether anything was replaced."""
        changed = False

        def walk(o):
            nonlocal changed
            if isinstance(o, str):
                r = self.redact(o)
                if r != o:
                    changed = True
                return r
            if isinstance(o, dict):
                return {walk(k): walk(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [walk(x) for x in o]
            return o

        return walk(obj), changed


def basic_credential(access_key: str, secret_key: str) -> str:
    """base64("access:secret"), the value after "Basic " in the Authorization header."""
    return base64.b64encode(f"{access_key}:{secret_key}".encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class Settings:
    base_url: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    source: str                      # "environment" or the credentials file path
    base_url_source: str             # "environment", the credentials file path, or "default"
    allow_http_loopback: bool = False

    def redactor(self) -> Redactor:
        return Redactor([self.access_key, self.secret_key, basic_credential(self.access_key, self.secret_key)])


def redactor_from_env(env: Mapping[str, str] | None = None) -> Redactor:
    """A redactor for the two key variables of the environment, when set (no file is read)."""
    access = _get(env, ACCESS_VAR)
    secret = _get(env, SECRET_VAR)
    values = [v for v in (access, secret) if v]
    if access and secret:
        values.append(basic_credential(access, secret))
    return Redactor(values)


def _strip_userinfo(url: str) -> str:
    return re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/?#]*@", r"\1", url)


def _printable(text: str) -> str:
    """Control and non-ASCII characters escaped, so a bad value shows as what it is."""
    return text.encode("unicode_escape").decode("ascii")


def validate_base_url(url: str, allow_http_loopback: bool = False) -> str:
    """Return the base URL without a trailing slash, or raise ConfigError.

    https only (http only with the loopback flag and a loopback host); no userinfo, query or fragment; the path must be
    /api/v<N>. The raw characters are checked before parsing, because urlsplit silently drops tabs and newlines.
    """
    def bad() -> ConfigError:
        shown = _printable(_strip_userinfo(url if isinstance(url, str) else repr(url)))
        return ConfigError(f"{BASE_URL_VAR} must look like https://<host>/api/v<N> (default {DEFAULT_BASE_URL}); "
                           f"got {shown}")

    if not isinstance(url, str) or not re.fullmatch(r"[\x21-\x7e]+", url):
        raise bad()
    trimmed = url.rstrip("/")
    try:
        parts = urlsplit(trimmed)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 (raises ValueError for a bad port)
    except ValueError:
        raise bad() from None
    scheme = parts.scheme.lower()
    if "@" in parts.netloc or parts.query or parts.fragment or "?" in trimmed or "#" in trimmed or not host:
        raise bad()
    if scheme == "https":
        pass
    elif scheme == "http" and allow_http_loopback and host in LOOPBACK_HOSTS:
        pass
    else:
        raise bad()
    if not re.fullmatch(r"/api/v\d+", parts.path):
        raise bad()
    return trimmed


def read_env_file(path: Path) -> dict[str, str]:
    """Parse onshape.env. Empty values are dropped; keys other than the three recognised ones are ignored."""
    path = Path(path)
    try:
        st = os.stat(path)
    except OSError as e:
        raise AuthError(f"cannot read {path}: {e.strerror or type(e).__name__}") from None
    if os.name == "posix":
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise AuthError(f"{path} is readable by others (mode {mode:04o}); run: chmod 600 {path}")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise AuthError(f"{path} is owned by another user (uid {st.st_uid}); it must belong to you")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise AuthError(f"cannot read {path}: {e.strerror or type(e).__name__}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AuthError(f"{path} is not UTF-8 text") from None
    values: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise AuthError(f"{path} line {n} is not KEY=value (comments start a line with #)")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in FILE_KEYS:
            continue
        if value:
            values[key] = value
        else:
            values.pop(key, None)
    return values


def _check_key(name: str, value: str, source: str) -> None:
    if not re.fullmatch(KEY_RE, value):
        raise AuthError(f"{name} from {source} contains spaces, control or non-ASCII characters "
                        f"(onshape.env takes no inline comments)")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Keys, base URL and their sources (see the module docstring). Raises AuthError or ConfigError."""
    allow = _get(env, "ACES_ONSHAPE_ALLOW_HTTP_LOOPBACK") == "1"
    access = _get(env, ACCESS_VAR)
    secret = _get(env, SECRET_VAR)
    path = credentials_path(env)
    env_url = _get(env, BASE_URL_VAR)
    if access and secret:
        _check_key(ACCESS_VAR, access, "the environment")
        _check_key(SECRET_VAR, secret, "the environment")
        base = validate_base_url(env_url or DEFAULT_BASE_URL, allow)
        return Settings(base, access, secret, "environment", "environment" if env_url else "default", allow)
    if access or secret:
        missing, present = (SECRET_VAR, ACCESS_VAR) if access else (ACCESS_VAR, SECRET_VAR)
        raise AuthError(f"{missing} is not set but {present} is: set both, or unset both and use {path}")
    if not path.exists():
        raise AuthError(missing_keys_message(path))
    values = read_env_file(path)
    f_access = values.get(ACCESS_VAR)
    f_secret = values.get(SECRET_VAR)
    if f_access and f_secret:
        _check_key(ACCESS_VAR, f_access, str(path))
        _check_key(SECRET_VAR, f_secret, str(path))
        if env_url:
            base, base_source = env_url, "environment"
        elif values.get(BASE_URL_VAR):
            base, base_source = values[BASE_URL_VAR], str(path)
        else:
            base, base_source = DEFAULT_BASE_URL, "default"
        return Settings(validate_base_url(base, allow), f_access, f_secret, str(path), base_source, allow)
    if f_access or f_secret:
        present, missing = (ACCESS_VAR, SECRET_VAR) if f_access else (SECRET_VAR, ACCESS_VAR)
        raise AuthError(f"{path} sets {present} but not {missing}")
    raise AuthError(missing_keys_message(path))


def _seconds(env: Mapping[str, str] | None, name: str, default: float) -> float:
    value = _get(env, name)
    if value is None:
        return default
    try:
        number = float(value)
    except ValueError:
        raise ConfigError(f"{name} must be a number of seconds; got {_printable(value)}") from None
    if not number > 0 or number != number or number == float("inf"):
        raise ConfigError(f"{name} must be a positive number of seconds; got {_printable(value)}")
    return number


def poll_settings(env: Mapping[str, str] | None = None) -> tuple[float, float]:
    """(first translation poll interval, total translation timeout) in seconds."""
    initial = max(0.01, _seconds(env, "ACES_ONSHAPE_POLL_S", 1.0))
    timeout = _seconds(env, "ACES_ONSHAPE_TRANSLATION_TIMEOUT_S", 600.0)
    return initial, timeout
