"""The console's JSON encoder: finite, masked, strict.

Copied in spirit from the SIMLAB console's `_finite` + `JSONResponse.render` (cudax simlab/console/app.py:40-53) with
the two changes the build spec (3.1) requires: `default` is limited to pathlib paths and datetime/date (never
`default=str`), and values under a key that names a credential are masked.

- NaN and +/-inf become null (solver outputs carry them; JSON cannot).
- A scalar under a key matching MASK_RE ((?i)token|secret|passw|api[_-]?key|authorization) becomes "[masked]".
  Containers under such a key are walked, not masked, so `tokens: {grid_control: {configured, source}}` survives;
  booleans are never masked (they carry no value); the key `token_source` (where a token came from, never its value)
  is exempt by name. Masking is defence in depth: no route ever puts a token into a payload on purpose.
- Anything else that is not JSON raises TypeError, so a bug surfaces as a 500 instead of a leaked repr.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import pathlib
import re
from typing import Any

MASK_RE = re.compile(r"(?i)token|secret|passw|api[_-]?key|authorization")
MASKED = "[masked]"
UNMASKED_KEYS = frozenset({"token_source", "tokens"})


def _default(o: Any) -> Any:
    if isinstance(o, pathlib.PurePath):
        return str(o)
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def clean(o: Any) -> Any:
    """Return a copy of `o` with non-finite floats as None and credential-named scalars masked."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        out: dict[str, Any] = {}
        for k, v in o.items():
            ks = k if isinstance(k, str) else str(k)
            if isinstance(v, (dict, list, tuple)):
                out[ks] = clean(v)
            elif v is not None and not isinstance(v, bool) and ks not in UNMASKED_KEYS and MASK_RE.search(ks):
                out[ks] = MASKED
            else:
                out[ks] = clean(v)
        return out
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def dumps(o: Any, indent: int | None = None, sort_keys: bool = False) -> str:
    """JSON text of `o` after clean(): compact by default, ensure_ascii off, allow_nan off."""
    return json.dumps(clean(o), ensure_ascii=False, allow_nan=False, indent=indent, sort_keys=sort_keys,
                      separators=(",", ":") if indent is None else None, default=_default)


def encode(o: Any) -> bytes:
    return dumps(o).encode("utf-8")


def etag(o: Any) -> str:
    """The first 12 hex characters of sha256 over the canonical (sorted, compact) JSON of `o`: the SSE stamp etag."""
    return hashlib.sha256(dumps(o, sort_keys=True).encode("utf-8")).hexdigest()[:12]
