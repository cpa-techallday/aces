"""sse.py through the running server: the stamp record format with inline payloads, the 32-stream cap (the 33rd gets
503 too-many-streams), Last-Event-ID replay from the ring (one stamp per topic, the latest), the ping cadence, and the
poller going idle after the last client plus the grace period. GRID CONTROL is the fake replaying the captures."""
from __future__ import annotations

import socket
import time
import unittest

from aces_console import sse

from .fakes.fake_gc import FakeGC
from .support import Console, wait_until


class SseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gc = FakeGC()
        cls.c = Console(gc=cls.gc.url, tag="sse")

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.gc.close()

    def test_stamp_records(self):
        status, hdrs, records, text = self.c.sse("fabric", seconds=4.0)  # longer than the 3 s TTLs: every live source stamps
        self.assertEqual(status, 200)
        self.assertTrue(hdrs["content-type"].startswith("text/event-stream"))
        self.assertEqual(hdrs["cache-control"], "no-store")
        self.assertEqual(hdrs["connection"], "close")
        self.assertTrue(text.startswith("retry: 3000\n\n"), text[:40])
        stamps = [r for r in records if r.get("event") == "stamp"]
        self.assertTrue(stamps, text)
        by_topic = {}
        for r in stamps:
            self.assertEqual(set(r["data"]), {"topic", "seq", "fetched_utc", "etag", "ok", "error", "next_try_s", "changed",
                                              "fixture", "payload"})
            self.assertEqual(int(r["id"]), r["data"]["seq"])
            by_topic[r["data"]["topic"]] = r["data"]
        self.assertIn("rig.status", by_topic)
        st = by_topic["rig.status"]
        self.assertTrue(st["ok"])
        self.assertEqual(st["payload"]["queue_note"], "GRID CONTROL reported -1")
        self.assertEqual(len(st["etag"]), 12)
        self.assertIn("console.health", by_topic)
        self.assertEqual(by_topic["console.health"]["payload"]["mode"], "sim-lab-4")
        self.assertIn("rig.telemetry", by_topic)
        sample = by_topic["rig.telemetry"]["payload"]
        self.assertEqual(set(sample), {"t", "gpus", "cpu", "mem", "src"})
        self.assertEqual(len(sample["gpus"]), 8)
        self.assertEqual(set(sample["gpus"][0]), {"i", "u", "m", "p", "c"})
        self.assertIn("fabric.manifest", by_topic)
        self.assertEqual(set(by_topic["fabric.manifest"]["payload"]), {"age_s", "stale", "path"})
        # rig.snapshot fails here (rig-state is a closed port) and backs off towards 120 s: read its stamp from the ring
        snap = self.c.app.broker.latest("rig.snapshot")
        self.assertIsNotNone(snap)
        self.assertIsNone(snap["payload"], "rig.snapshot is notice-only")
        self.assertFalse(snap["ok"])
        self.assertTrue(snap["error"].startswith("rig-state unreachable: "))
        self.assertIsInstance(snap["next_try_s"], float)
        self.assertIsNone(st["fixture"])

    def test_33rd_client_gets_503(self):
        socks = []
        try:
            for i in range(sse.MAX_CLIENTS):
                s = self.c.open_sse("ops", timeout=0.2)
                socks.append(s)
            self.assertTrue(wait_until(lambda: self.c.app.broker.count() == sse.MAX_CLIENTS, 5.0))
            status, hdrs, records, text = self.c.sse("ops", seconds=0.4)
            self.assertEqual(status, 503)
            self.assertIn('"code":"too-many-streams"', text)
            self.assertTrue(hdrs["content-type"].startswith("application/json"))
        finally:
            for s in socks:
                s.close()
        self.c.app.broker.ping_s = 0.1
        try:
            self.assertTrue(wait_until(lambda: self.c.app.broker.count() == 0, 5.0))
        finally:
            self.c.app.broker.ping_s = sse.PING_S

    def test_last_event_id_replays_the_latest_per_topic(self):
        hub = self.c.app.hub
        s1 = hub.publish("t.replay.a", {"n": 1}, True, None, None)["seq"]
        s2 = hub.publish("t.replay.a", {"n": 2}, True, None, None)["seq"]
        s3 = hub.publish("t.replay.b", {"n": 3}, True, None, None)["seq"]
        self.assertTrue(s1 < s2 < s3)
        status, hdrs, records, text = self.c.sse("ops", seconds=0.5, last_event_id=str(s1))
        replayed = [r["data"] for r in records if r.get("event") == "stamp" and r["data"]["topic"].startswith("t.replay.")]
        self.assertEqual([(r["topic"], r["seq"]) for r in replayed], [("t.replay.a", s2), ("t.replay.b", s3)])
        status, hdrs, records, text = self.c.sse("ops", seconds=0.3, last_event_id=str(s3))
        self.assertEqual([r for r in records if r.get("event") == "stamp" and r["data"]["topic"].startswith("t.replay.")], [])
        status, hdrs, records, text = self.c.sse("ops", seconds=0.3)
        self.assertEqual([r for r in records if r.get("event") == "stamp" and r["data"]["topic"].startswith("t.replay.")], [],
                         "no Last-Event-ID, no replay")
        ring = self.c.app.broker.ring()
        self.assertLessEqual(len(ring), sse.RING)

    def test_ping_cadence(self):
        broker = self.c.app.broker
        broker.ping_s = 0.2
        try:
            status, hdrs, records, text = self.c.sse(None, seconds=1.0)
            pings = [r for r in records if r.get("comment") == "ping"]
            self.assertGreaterEqual(len(pings), 3, text)
        finally:
            broker.ping_s = sse.PING_S
        self.assertEqual(sse.PING_S, 15.0)

    def test_poller_idles_after_the_last_client_plus_grace(self):
        hub, broker = self.c.app.hub, self.c.app.broker
        hub.grace_s = 0.4
        broker.ping_s = 0.1
        try:
            s = self.c.open_sse("fabric")
            self.assertTrue(wait_until(lambda: hub.state == "running" and "fabric" in hub.open_plates(), 3.0))
            s.close()
            self.assertTrue(wait_until(lambda: broker.count() == 0, 3.0))
            self.assertEqual(hub.state, "running", "grace keeps the poller running")
            self.assertTrue(wait_until(lambda: hub.state == "idle", 3.0))
            self.assertEqual(hub.open_plates(), set())
        finally:
            hub.grace_s = 60.0
            broker.ping_s = sse.PING_S


if __name__ == "__main__":
    unittest.main()
