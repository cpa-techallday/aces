"""The poller and the Hub: plate-gated polling with TTLs and backoff, feeding the SSE broker (build spec 3.2).

    hub.register(topic, fetch, ttl_s, plates=("fabric",), inline=True, max_backoff_s=60, source=url)
    hub.publish(topic, payload | None, ok, error, next_try_s)
    entry = hub.get(topic)            # what a GET route reads: cached, else fetched once synchronously
    entry = hub.refresh(topic)        # fresh, never from cache (the switch gates)

A source is a topic with a fetch callable (`fetch() -> (ok, data, error)`), a TTL, the plates that keep it polled
("*" = any connected client) and whether its payload is inlined in the stamp (True, or a callable that reduces the
data to the inline shape; notice-only sources publish `payload: null` and the page refetches when `changed`). The
poller thread runs only while the Broker has at least one client, plus `grace_s` (60 s) after the last leaves; with no
page open there is no rig traffic. A failing source doubles its interval to at most `max_backoff_s` (60 s; rig-state
120 s), resets on success and reports `error` and `next_try_s` through /api/health.sources and its stamps. A stamp is
published on every completed fetch (so `fetched_utc` advances) with `changed` set by comparing etags.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from .cache import Cache, Entry

FetchFn = Callable[[], tuple[bool, Any, "str | None"]]
GRACE_S = 60.0
TICK_S = 0.25


class Source:
    __slots__ = ("topic", "fetch", "ttl_s", "plates", "inline", "max_backoff_s", "source", "enabled", "on_result",
                 "next_due", "last_etag", "last_fetch_n", "polls", "stamps")

    def __init__(self, topic: str, fetch: FetchFn, ttl_s: float, plates: Iterable[str], inline, max_backoff_s: float,
                 source: str | None, enabled: Callable[[], bool] | None, on_result: Callable[[Entry], None] | None):
        self.topic = topic
        self.fetch = fetch
        self.ttl_s = float(ttl_s)
        self.plates = frozenset(plates or ("*",))
        self.inline = inline
        self.max_backoff_s = float(max_backoff_s)
        self.source = source
        self.enabled = enabled
        self.on_result = on_result
        self.next_due = 0.0
        self.last_etag: str | None = None
        self.last_fetch_n = 0
        self.polls = 0
        self.stamps = 0

    def gated_open(self, open_plates: set[str]) -> bool:
        return "*" in self.plates or bool(self.plates & open_plates)

    def is_enabled(self) -> bool:
        if self.enabled is None:
            return True
        try:
            return bool(self.enabled())
        except Exception:  # noqa: BLE001
            return False


class Hub:
    def __init__(self, cache: Cache, broker, grace_s: float = GRACE_S, tick_s: float = TICK_S,
                 fixture: str | None = None, log: Callable[[str], None] | None = None):
        self.cache = cache
        self.broker = broker
        self.grace_s = float(grace_s)
        self.tick_s = float(tick_s)
        self.fixture = fixture
        self.log = log or (lambda msg: None)
        self._sources: dict[str, Source] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = False
        self._last_active: float | None = None
        self._open: set[str] = set()
        self.ticks = 0

    # --- registration ------------------------------------------------------------------------------------------------

    def register(self, topic: str, fetch: FetchFn, ttl_s: float, plates: Iterable[str] = ("*",), inline=False,
                 max_backoff_s: float = 60.0, source: str | None = None, enabled: Callable[[], bool] | None = None,
                 on_result: Callable[[Entry], None] | None = None) -> Source:
        """Register (or replace) a polled source. Returns the Source."""
        src = Source(topic, fetch, ttl_s, plates, inline, max_backoff_s, source, enabled, on_result)
        with self._lock:
            self._sources[topic] = src
        return src

    def unregister(self, topic: str) -> None:
        with self._lock:
            self._sources.pop(topic, None)

    def source(self, topic: str) -> Source | None:
        return self._sources.get(topic)

    def topics(self) -> list[str]:
        return list(self._sources)

    # --- reads -------------------------------------------------------------------------------------------------------

    def peek(self, topic: str) -> Entry | None:
        return self.cache.peek(topic)

    def get(self, topic: str, force: bool = False) -> Entry:
        """The entry for a registered topic: the cache when fresh, otherwise one synchronous fetch (publishing)."""
        src = self._sources[topic]
        prev = self.cache.peek(topic)
        before = prev.fetch_n if prev is not None else 0
        entry = self.cache.get(topic, src.ttl_s, src.fetch, source=src.source, max_backoff_s=src.max_backoff_s,
                               force=force)
        if entry.fetch_n != before:
            self._after_fetch(src, entry)
        return entry

    def refresh(self, topic: str) -> Entry:
        return self.get(topic, force=True)

    # --- publishing --------------------------------------------------------------------------------------------------

    def publish(self, topic: str, payload: Any, ok: bool, error: str | None, next_try_s: float | None,
                changed: bool | None = None, etag: str | None = None, fetched_utc: str | None = None) -> dict[str, Any]:
        stamp = {"topic": topic, "fetched_utc": fetched_utc or _now_utc(), "etag": etag, "ok": bool(ok),
                 "error": error, "next_try_s": next_try_s, "changed": True if changed is None else bool(changed),
                 "fixture": self.fixture, "payload": payload}
        return self.broker.publish(stamp)

    def _inline_payload(self, src: Source, entry: Entry) -> Any:
        if not src.inline or entry.data is None:
            return None
        if callable(src.inline):
            try:
                return src.inline(entry.data)
            except Exception as exc:  # noqa: BLE001
                self.log(f"poller: inline reducer of {src.topic} failed: {type(exc).__name__}: {exc}")
                return None
        return entry.data

    def _after_fetch(self, src: Source, entry: Entry) -> None:
        with self._lock:
            if src.last_fetch_n == entry.fetch_n:
                return  # already published by the other reader (poll thread vs GET)
            src.last_fetch_n = entry.fetch_n
            src.next_due = (entry.attempted_ts or time.time()) + entry.interval_s
            changed = entry.etag is not None and entry.etag != src.last_etag
            if entry.etag is not None:
                src.last_etag = entry.etag
            src.stamps += 1
        entry.fixture = self.fixture
        self.publish(src.topic, self._inline_payload(src, entry), entry.ok, entry.error, entry.next_try_s, changed,
                     entry.etag, entry.fetched_utc)
        if src.on_result is not None:
            try:
                src.on_result(entry)
            except Exception as exc:  # noqa: BLE001
                self.log(f"poller: on_result of {src.topic} failed: {type(exc).__name__}: {exc}")

    # --- the loop ----------------------------------------------------------------------------------------------------

    @property
    def state(self) -> str:
        return "running" if self._active else "idle"

    def open_plates(self) -> set[str]:
        return set(self._open)

    def poll_once(self, now: float | None = None) -> list[str]:
        """Poll every due, gated, enabled source once. Returns the topics fetched."""
        now = time.time() if now is None else now
        fetched: list[str] = []
        if not self._active:
            return fetched  # no client and no grace: no rig traffic
        with self._lock:
            sources = list(self._sources.values())
        for src in sources:
            if not src.gated_open(self._open) or now < src.next_due or not src.is_enabled():
                continue
            src.polls += 1
            prev = self.cache.peek(src.topic)
            before = prev.fetch_n if prev is not None else 0
            entry = self.cache.get(src.topic, src.ttl_s, src.fetch, source=src.source, max_backoff_s=src.max_backoff_s)
            if entry.fetch_n != before:
                self._after_fetch(src, entry)
                fetched.append(src.topic)
            else:
                src.next_due = (entry.attempted_ts or now) + entry.interval_s
        return fetched

    def _update_activity(self, now: float) -> None:
        n = self.broker.count()
        if n > 0:
            self._active = True
            self._last_active = now
            self._open = self.broker.open_plates()
        elif self._last_active is not None and now - self._last_active < self.grace_s:
            self._active = True  # grace: keep the plates that were open
        else:
            self._active = False
            self._open = set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            self._update_activity(now)
            if self._active:
                try:
                    self.poll_once(now)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"poller: tick failed: {type(exc).__name__}: {exc}")
            self.ticks += 1
            self._stop.wait(self.tick_s)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aces-console-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._active = False

    # --- health ------------------------------------------------------------------------------------------------------

    def sources_health(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            sources = list(self._sources.values())
        for src in sources:
            e = self.cache.peek(src.topic)
            row: dict[str, Any] = {"fetched_utc": None, "ok": None, "error": "not polled yet", "next_try_s": None,
                                   "backoff_s": None, "ttl_s": src.ttl_s, "plates": sorted(src.plates),
                                   "polled": src.gated_open(self._open) and src.is_enabled(), "source": src.source,
                                   "enabled": src.is_enabled(), "polls": src.polls, "stale": False}
            if e is not None and e.attempted_ts is not None:
                row.update({"fetched_utc": e.fetched_utc, "ok": e.ok, "error": e.error, "next_try_s": e.next_try_s,
                            "backoff_s": e.backoff_s, "stale": e.stale})
            out[src.topic] = row
        return out


def _now_utc() -> str:
    from .cache import utc
    return utc(time.time()) or ""
