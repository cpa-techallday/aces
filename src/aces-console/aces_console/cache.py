"""Per-key cache with TTL, last-good staleness and backoff: the data behind every envelope.

    entry = Cache().get(key, ttl, fetch, source=url)      # fetch() -> (ok, data, error)

`get` returns the cached Entry while it is younger than its TTL (or, after a failure, while the backoff interval
runs), otherwise it calls `fetch` once (one lock per key: concurrent GETs and the poller never double-fetch) and
records the outcome. On failure the last good payload is kept with `stale=True` for at most `stale_s` (600 s) after
the first failure, then `data` becomes None; the retry interval doubles from the TTL to at most `max_backoff_s`,
resets on success, and is reported as `next_try_s`. An exception inside `fetch` is a failure with the exception's
one-line text, never a crash of the server.

Entry fields (the envelope's meta): data, fetched_utc, age_s, ok, error, stale, ttl_s, next_try_s, source, etag.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import jsonenc

FetchResult = tuple[bool, Any, "str | None"]
STALE_S = 600.0
MAX_BACKOFF_S = 60.0


def utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Entry:
    """One cached result. `fetched_ts` is when `data` was obtained (None when there is no data); `attempted_ts` is the
    last attempt, successful or not."""

    __slots__ = ("key", "data", "ok", "error", "stale", "ttl_s", "source", "etag", "fetched_ts", "attempted_ts",
                 "last_good_ts", "first_failure_ts", "failures", "interval_s", "max_backoff_s", "fixture", "fetch_n")

    def __init__(self, key: str, ttl_s: float, source: str | None, max_backoff_s: float):
        self.key = key
        self.data: Any = None
        self.ok = False
        self.error: str | None = None
        self.stale = False
        self.ttl_s = float(ttl_s)
        self.source = source
        self.etag: str | None = None
        self.fetched_ts: float | None = None
        self.attempted_ts: float | None = None
        self.last_good_ts: float | None = None
        self.first_failure_ts: float | None = None
        self.failures = 0
        self.interval_s = float(ttl_s)
        self.max_backoff_s = float(max_backoff_s)
        self.fixture: str | None = None
        self.fetch_n = 0  # counts attempts: readers compare it, not the clock (15 ms resolution on Windows)

    @property
    def age_s(self) -> float | None:
        ts = self.fetched_ts if self.fetched_ts is not None else self.attempted_ts
        return None if ts is None else max(0.0, round(time.time() - ts, 3))

    @property
    def fetched_utc(self) -> str | None:
        return utc(self.fetched_ts if self.fetched_ts is not None else self.attempted_ts)

    @property
    def next_try_s(self) -> float | None:
        if self.ok or self.attempted_ts is None:
            return None
        return max(0.0, round(self.attempted_ts + self.interval_s - time.time(), 1))

    @property
    def backoff_s(self) -> float | None:
        return None if self.ok else self.interval_s

    def fresh(self, now: float | None = None) -> bool:
        """True while no new attempt is due (younger than the TTL, or inside the backoff interval)."""
        if self.attempted_ts is None:
            return False
        now = time.time() if now is None else now
        return now - self.attempted_ts < self.interval_s

    def meta(self) -> dict[str, Any]:
        return {"source": self.source, "fetched_utc": self.fetched_utc, "age_s": self.age_s, "ttl_s": self.ttl_s,
                "stale": self.stale, "ok": self.ok, "error": self.error, "next_try_s": self.next_try_s,
                "fixture": self.fixture}


class Cache:
    def __init__(self, stale_s: float = STALE_S):
        self.stale_s = float(stale_s)
        self._entries: dict[str, Entry] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def peek(self, key: str) -> Entry | None:
        return self._entries.get(key)

    def keys(self) -> list[str]:
        return list(self._entries)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def put(self, key: str, data: Any, source: str | None = None, ttl: float = 0.0) -> Entry:
        """Record `data` as a successful fetch made now (for values the server computes itself)."""
        with self._key_lock(key):
            e = self._entries.get(key) or Entry(key, ttl, source, MAX_BACKOFF_S)
            now = time.time()
            e.data, e.ok, e.error, e.stale = data, True, None, False
            e.fetched_ts = e.attempted_ts = e.last_good_ts = now
            e.first_failure_ts, e.failures, e.interval_s = None, 0, float(ttl)
            e.etag = jsonenc.etag(data)
            e.ttl_s = float(ttl)
            e.fetch_n += 1
            if source is not None:
                e.source = source
            self._entries[key] = e
            return e

    def get(self, key: str, ttl: float, fetch: Callable[[], FetchResult], source: str | None = None,
            max_backoff_s: float = MAX_BACKOFF_S, stale_s: float | None = None, force: bool = False) -> Entry:
        """The entry for `key`, fetched now when it is due (or `force`)."""
        with self._key_lock(key):
            e = self._entries.get(key)
            if e is not None and not force and e.fresh():
                return e
            return self._fetch(key, ttl, fetch, source, max_backoff_s, stale_s)

    def refresh(self, key: str, ttl: float, fetch: Callable[[], FetchResult], source: str | None = None,
                max_backoff_s: float = MAX_BACKOFF_S, stale_s: float | None = None) -> Entry:
        """Fetch now regardless of freshness (the switch gates read fresh values)."""
        return self.get(key, ttl, fetch, source, max_backoff_s, stale_s, force=True)

    def _fetch(self, key, ttl, fetch, source, max_backoff_s, stale_s) -> Entry:
        prev = self._entries.get(key)
        e = Entry(key, ttl, source, max_backoff_s)
        if prev is not None:
            e.last_good_ts, e.first_failure_ts, e.failures = prev.last_good_ts, prev.first_failure_ts, prev.failures
            e.fixture, e.fetch_n = prev.fixture, prev.fetch_n
        e.fetch_n += 1
        try:
            ok, data, error = fetch()
        except Exception as exc:  # noqa: BLE001 (a failing fetch is a result, not a crash)
            ok, data, error = False, None, f"{type(exc).__name__}: {exc}"[:200]
        now = time.time()
        e.attempted_ts = now
        if ok:
            e.data, e.ok, e.error, e.stale = data, True, None, False
            e.fetched_ts = e.last_good_ts = now
            e.first_failure_ts, e.failures, e.interval_s = None, 0, float(ttl)
            e.etag = jsonenc.etag(data)
        else:
            e.ok, e.error = False, error or "failed"
            e.failures += 1
            e.first_failure_ts = e.first_failure_ts or now
            e.interval_s = min(float(ttl) * (2 ** e.failures), float(max_backoff_s))
            keep = self.stale_s if stale_s is None else float(stale_s)
            if prev is not None and prev.last_good_ts is not None and now - e.first_failure_ts <= keep:
                e.data, e.stale, e.fetched_ts, e.etag = prev.data, True, prev.fetched_ts, prev.etag
            else:
                e.data, e.stale, e.fetched_ts, e.etag = None, False, None, None
        self._entries[key] = e
        return e
