"""poller.py and cache.py on their own: plate gating, TTL cadence, backoff doubling with the cap and the reset on
success, last-good staleness and its 600 s window, inline versus notice-only payloads, the changed flag, the grace
period, and the health rows. Fetch callables here are counters, not data: nothing is sampled or fabricated."""
from __future__ import annotations

import time
import unittest

from aces_console import cache as cache_mod, poller, sse


class CacheTest(unittest.TestCase):
    def test_ttl_stale_and_backoff(self):
        c = cache_mod.Cache(stale_s=600)
        calls = []
        state = {"ok": True}

        def fetch():
            calls.append(1)
            return (True, {"n": len(calls)}, None) if state["ok"] else (False, None, "unreachable: refused")

        e1 = c.get("k", 3.0, fetch, source="u")
        self.assertEqual((e1.ok, e1.data, e1.stale, e1.error, e1.source, e1.ttl_s), (True, {"n": 1}, False, None, "u", 3.0))
        self.assertEqual(c.get("k", 3.0, fetch).data, {"n": 1}, "fresh: no second fetch")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(e1.next_try_s)
        self.assertEqual(len(e1.etag), 12)
        state["ok"] = False
        e2 = c.refresh("k", 3.0, fetch, max_backoff_s=60)
        self.assertEqual((e2.ok, e2.stale, e2.data, e2.error), (False, True, {"n": 1}, "unreachable: refused"))
        self.assertEqual(e2.interval_s, 6.0)
        self.assertGreater(e2.next_try_s, 0)
        self.assertEqual(e2.backoff_s, 6.0)
        for expected in (12.0, 24.0, 48.0, 60.0, 60.0):
            e = c.refresh("k", 3.0, fetch, max_backoff_s=60)
            self.assertEqual(e.interval_s, expected)
            self.assertTrue(e.stale)
        self.assertEqual(c.get("k", 3.0, fetch).failures, 6, "inside the backoff interval: no fetch")
        e = c.refresh("k", 3.0, fetch, max_backoff_s=120)
        self.assertEqual(e.interval_s, 120.0)
        state["ok"] = True
        e = c.refresh("k", 3.0, fetch)
        self.assertEqual((e.ok, e.stale, e.failures, e.interval_s, e.next_try_s), (True, False, 0, 3.0, None))
        # the stale window
        state["ok"] = False
        e = c.refresh("k", 3.0, fetch)
        self.assertTrue(e.stale)
        e.first_failure_ts -= 601
        e = c.refresh("k", 3.0, fetch)
        self.assertEqual((e.stale, e.data), (False, None))
        # an exception inside fetch is a failure, not a crash
        def boom():
            raise OSError("disk")
        e = c.refresh("x", 1.0, boom)
        self.assertEqual((e.ok, e.error), (False, "OSError: disk"))
        meta = e.meta()
        self.assertEqual(set(meta), {"source", "fetched_utc", "age_s", "ttl_s", "stale", "ok", "error", "next_try_s", "fixture"})

    def test_put_and_peek(self):
        c = cache_mod.Cache()
        self.assertIsNone(c.peek("a"))
        e = c.put("a", {"v": 1}, source="computed", ttl=5)
        self.assertEqual((c.peek("a").data, e.ok, e.ttl_s), ({"v": 1}, True, 5.0))
        c.invalidate("a")
        self.assertIsNone(c.peek("a"))


class HubTest(unittest.TestCase):
    def setUp(self):
        self.cache = cache_mod.Cache()
        self.broker = sse.Broker()
        self.hub = poller.Hub(self.cache, self.broker, grace_s=0.3, tick_s=0.05)
        self.calls = {"a": 0, "b": 0, "c": 0, "f": 0}

    def _fetch(self, name, ok=True):
        def fetch():
            self.calls[name] += 1
            return (True, {"name": name, "n": self.calls[name]}, None) if ok else (False, None, f"unreachable: {name}")
        return fetch

    def test_plate_gating_and_inline(self):
        self.hub.register("a", self._fetch("a"), 1.0, ("fabric",), inline=True)
        self.hub.register("b", self._fetch("b"), 1.0, ("ops",), inline=False)
        self.hub.register("c", self._fetch("c"), 1.0, ("*",), inline=lambda d: {"only": d["n"]})
        self.assertEqual(self.hub.state, "idle")
        self.assertEqual(self.hub.poll_once(), [], "no client: nothing polled")
        client = self.broker.add("fabric")
        self.hub._update_activity(time.time())
        self.assertEqual(self.hub.state, "running")
        self.assertEqual(sorted(self.hub.poll_once()), ["a", "c"])
        self.assertEqual(self.calls, {"a": 1, "b": 0, "c": 1, "f": 0})
        self.assertEqual(self.hub.poll_once(), [], "inside the TTL: nothing due")
        stamps = {s["topic"]: s for s in self.broker.ring()}
        self.assertEqual(stamps["a"]["payload"], {"name": "a", "n": 1})
        self.assertEqual(stamps["c"]["payload"], {"only": 1})
        self.assertTrue(stamps["a"]["changed"])
        self.assertTrue(stamps["a"]["ok"])
        self.assertIsNone(stamps["a"]["next_try_s"])
        self.assertEqual(set(stamps["a"]), {"topic", "seq", "fetched_utc", "etag", "ok", "error", "next_try_s", "changed", "fixture", "payload"})
        self.broker.remove(client)
        client = self.broker.add("ops")
        self.hub._update_activity(time.time())
        src = self.hub.source("b")
        self.assertEqual(self.hub.poll_once(), ["b"])
        self.assertIsNone(self.broker.latest("b")["payload"], "notice-only")
        health = self.hub.sources_health()
        self.assertEqual(set(health), {"a", "b", "c"})
        self.assertEqual(set(health["a"]), {"fetched_utc", "ok", "error", "next_try_s", "backoff_s", "ttl_s", "plates", "polled",
                                            "source", "enabled", "polls", "stale"})
        self.assertFalse(health["a"]["polled"], "fabric is no longer open")
        self.assertTrue(health["b"]["polled"])
        self.assertEqual(health["b"]["plates"], ["ops"])
        self.broker.remove(client)

    def test_changed_flag_and_get_publishes_once(self):
        value = {"v": 1}
        self.hub.register("same", lambda: (True, dict(value), None), 0.0, ("*",), inline=True)
        self.broker.add("x")
        self.hub._update_activity(time.time())
        self.hub.poll_once()
        self.hub.poll_once()
        stamps = [s for s in self.broker.ring() if s["topic"] == "same"]
        self.assertEqual([s["changed"] for s in stamps], [True, False])
        value["v"] = 2
        self.hub.poll_once()
        self.assertTrue(self.broker.ring()[-1]["changed"])
        self.hub.register("g", self._fetch("a"), 10.0, ("*",), inline=True)
        n = len(self.broker.ring())
        self.hub.get("g")
        self.hub.get("g")
        self.assertEqual(len(self.broker.ring()), n + 1, "a GET fetches once within the TTL and publishes once")
        self.hub.refresh("g")
        self.assertEqual(len(self.broker.ring()), n + 2)

    def test_backoff_reports_through_stamps_and_health(self):
        self.hub.register("f", self._fetch("f", ok=False), 1.0, ("*",), inline=True, max_backoff_s=4.0)
        self.broker.add("x")
        self.hub._update_activity(time.time())
        self.assertEqual(self.hub.poll_once(), ["f"])
        st = self.broker.latest("f")
        self.assertEqual((st["ok"], st["error"], st["payload"]), (False, "unreachable: f", None))
        self.assertIsInstance(st["next_try_s"], float)
        src = self.hub.source("f")
        e = self.cache.peek("f")
        self.assertEqual(e.interval_s, 2.0)
        self.assertEqual(self.hub.poll_once(), [], "backoff: not due yet")
        src.next_due = 0
        e.attempted_ts -= 10
        self.assertEqual(self.hub.poll_once(), ["f"])
        self.assertEqual(self.cache.peek("f").interval_s, 4.0)
        h = self.hub.sources_health()["f"]
        self.assertEqual((h["ok"], h["error"], h["backoff_s"]), (False, "unreachable: f", 4.0))

    def test_enabled_gate(self):
        flag = {"on": False}
        self.hub.register("tok", self._fetch("a"), 1.0, ("*",), enabled=lambda: flag["on"])
        self.broker.add("x")
        self.hub._update_activity(time.time())
        self.assertEqual(self.hub.poll_once(), [])
        self.assertFalse(self.hub.sources_health()["tok"]["enabled"])
        flag["on"] = True
        self.assertEqual(self.hub.poll_once(), ["tok"])

    def test_thread_grace(self):
        self.hub.register("a", self._fetch("a"), 0.2, ("*",), inline=True)
        self.hub.start()
        try:
            time.sleep(0.15)
            self.assertEqual(self.hub.state, "idle")
            self.assertEqual(self.calls["a"], 0, "no client, no fetch")
            client = self.broker.add("fabric")
            time.sleep(0.5)
            self.assertEqual(self.hub.state, "running")
            self.assertGreaterEqual(self.calls["a"], 2, "TTL 0.2 s: polled repeatedly")
            self.broker.remove(client)
            time.sleep(0.1)
            self.assertEqual(self.hub.state, "running", "inside the grace period")
            time.sleep(0.6)
            self.assertEqual(self.hub.state, "idle")
            n = self.calls["a"]
            time.sleep(0.4)
            self.assertEqual(self.calls["a"], n, "idle: no fetches")
        finally:
            self.hub.stop()


if __name__ == "__main__":
    unittest.main()
