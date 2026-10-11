"""Server-sent events: the one stream every open page listens on (build spec 3.2).

    GET /api/events?plate=<id>   ->   text/event-stream

Records:

    id: <seq>
    event: stamp
    data: {"topic": "rig.status", "seq": 412, "fetched_utc": "...", "etag": "<sha256[:12]>", "ok": true,
           "error": null, "next_try_s": null, "changed": true, "fixture": null, "payload": {...} | null}

`retry: 3000` opens the stream; `: ping` every 15 s keeps it alive; in fixtures mode the first record is
`event: fixture` with {"fixture": "FIXTURE 2026-10-11"}. At most 32 streams: the 33rd is refused (the route answers 503
{code: "too-many-streams"} and the page falls back to polling the GET routes). `Last-Event-ID` replays every stamp with
a greater `seq` from a 512-stamp ring, the latest per topic only. The Broker also tells the poller which plates are open
and when the last client left (the poller's 60 s grace).
"""
from __future__ import annotations

import collections
import itertools
import queue
import select
import socket
import threading
import time
from typing import Any

from . import jsonenc

MAX_CLIENTS = 32
RING = 512
PING_S = 15.0
RETRY_MS = 3000
_CLIENT_QUEUE = 1024
_EOF_CHECK_S = 1.0  # how often an idle stream looks for the peer having gone away
BYE = object()


def format_record(stamp: dict[str, Any]) -> bytes:
    return (f"id: {stamp.get('seq')}\nevent: stamp\ndata: {jsonenc.dumps(stamp)}\n\n").encode("utf-8")


def _peer_gone(conn: socket.socket) -> bool:
    """True when the client closed its end: the socket is readable and a peek returns no bytes (an SSE client never
    sends after its request, so readable data is left alone)."""
    try:
        readable, _, _ = select.select([conn], [], [], 0)
        if not readable:
            return False
        return conn.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


class Client:
    __slots__ = ("id", "plate", "queue", "connected_ts")

    def __init__(self, cid: int, plate: str | None):
        self.id = cid
        self.plate = plate
        self.queue: queue.Queue = queue.Queue(maxsize=_CLIENT_QUEUE)
        self.connected_ts = time.time()


class Broker:
    def __init__(self, max_clients: int = MAX_CLIENTS, ring: int = RING, ping_s: float = PING_S,
                 retry_ms: int = RETRY_MS, fixture: str | None = None):
        self.max_clients = max_clients
        self.ping_s = float(ping_s)
        self.retry_ms = int(retry_ms)
        self.fixture = fixture
        self._ring: collections.deque = collections.deque(maxlen=ring)
        self._clients: dict[int, Client] = {}
        self._ids = itertools.count(1)
        self._seq = 0
        self._lock = threading.Lock()
        self.last_client_left_ts: float | None = None
        self.closing = False

    # --- clients -----------------------------------------------------------------------------------------------------

    def add(self, plate: str | None) -> Client | None:
        """Register a client, or None when the cap is reached."""
        with self._lock:
            if self.closing or len(self._clients) >= self.max_clients:
                return None
            c = Client(next(self._ids), plate)
            self._clients[c.id] = c
            return c

    def remove(self, client: Client) -> None:
        with self._lock:
            if self._clients.pop(client.id, None) is not None and not self._clients:
                self.last_client_left_ts = time.time()

    def count(self) -> int:
        return len(self._clients)

    def open_plates(self) -> set[str]:
        with self._lock:
            return {c.plate for c in self._clients.values() if c.plate}

    # --- stamps ------------------------------------------------------------------------------------------------------

    def publish(self, stamp: dict[str, Any]) -> dict[str, Any]:
        """Assign the next seq, keep the stamp in the ring and hand it to every connected client."""
        with self._lock:
            self._seq += 1
            stamp = dict(stamp, seq=self._seq)
            self._ring.append(stamp)
            clients = list(self._clients.values())
        for c in clients:
            try:
                c.queue.put_nowait(stamp)
            except queue.Full:  # a slow consumer: drop its oldest stamp, the latest state wins
                try:
                    c.queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    c.queue.put_nowait(stamp)
                except queue.Full:
                    pass
        return stamp

    def seq(self) -> int:
        return self._seq

    def ring(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._ring)

    def latest(self, topic: str) -> dict[str, Any] | None:
        with self._lock:
            for st in reversed(self._ring):
                if st.get("topic") == topic:
                    return st
        return None

    def replay(self, last_event_id: str | int | None) -> list[dict[str, Any]]:
        """Stamps with seq greater than `last_event_id`, the latest per topic, in seq order."""
        if last_event_id is None or str(last_event_id).strip() == "":
            return []
        try:
            after = int(str(last_event_id).strip())
        except ValueError:
            return []
        latest: dict[str, dict[str, Any]] = {}
        with self._lock:
            for st in self._ring:
                if st.get("seq", 0) > after:
                    latest[st.get("topic")] = st
        return sorted(latest.values(), key=lambda s: s.get("seq", 0))

    # --- the stream --------------------------------------------------------------------------------------------------

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
                "Connection": "close", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer"}

    def serve(self, handler, plate: str | None, last_event_id: str | None) -> bool:
        """Write the stream on `handler` (a BaseHTTPRequestHandler) until the client leaves or close_all() runs.
        Returns False, writing nothing, when the client cap is reached."""
        client = self.add(plate)
        if client is None:
            return False
        handler.close_connection = True
        try:
            handler.send_response(200)
            for k, v in self.headers().items():
                handler.send_header(k, v)
            handler.end_headers()
            w = handler.wfile
            w.write(f"retry: {self.retry_ms}\n\n".encode("utf-8"))
            if self.fixture:
                w.write(("event: fixture\ndata: " + jsonenc.dumps({"fixture": self.fixture}) + "\n\n").encode("utf-8"))
            for st in self.replay(last_event_id):
                w.write(format_record(st))
            w.flush()
            conn = getattr(handler, "connection", None)
            last_write = time.monotonic()
            while not self.closing:
                try:
                    item = client.queue.get(timeout=min(self.ping_s, _EOF_CHECK_S))
                except queue.Empty:
                    if conn is not None and _peer_gone(conn):
                        break
                    if time.monotonic() - last_write >= self.ping_s:
                        w.write(b": ping\n\n")
                        w.flush()
                        last_write = time.monotonic()
                    continue
                if item is BYE:
                    w.write(b"event: bye\ndata: {}\n\n")
                    w.flush()
                    break
                w.write(format_record(item))
                w.flush()
                last_write = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError, ValueError):
            pass
        finally:
            self.remove(client)
        return True

    def close_all(self, bye: bool = True) -> None:
        """Tell every stream to end (an `event: bye` first when `bye`)."""
        with self._lock:
            self.closing = True
            clients = list(self._clients.values())
        for c in clients:
            try:
                c.queue.put_nowait(BYE if bye else None)
            except queue.Full:
                pass
