"""GPU leases in redis: one key per GPU, SET NX with a TTL, refreshed by the holder while a case runs.
A dead worker's lease expires on its own; a live one is refreshed every heartbeat."""
from __future__ import annotations

import os
import socket
from typing import Dict, Iterable, Optional

import redis

DEFAULT_REDIS = "redis://127.0.0.1:6390/0"
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


def redis_url() -> str:
    return os.environ.get("SIMLAB_REDIS_URL", DEFAULT_REDIS)


def get_redis() -> "redis.Redis":
    return redis.Redis.from_url(redis_url())


def owner_id(tag: str = "") -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{tag}"


class LeaseManager:
    def __init__(self, conn: Optional["redis.Redis"] = None, ttl_s: int = 900, prefix: str = "simlab:lease:gpu:"):
        self.r = conn or get_redis()
        self.ttl = int(ttl_s)
        self.prefix = prefix
        self._release = self.r.register_script(_RELEASE)

    def key(self, gpu: int) -> str:
        return f"{self.prefix}{int(gpu)}"

    def held(self) -> Dict[int, str]:
        out = {}
        for k in self.r.scan_iter(match=self.prefix + "*"):
            v = self.r.get(k)
            if v is not None:
                out[int(k.decode().rsplit(":", 1)[-1])] = v.decode()
        return out

    def acquire(self, gpu: int, owner: str) -> bool:
        return bool(self.r.set(self.key(gpu), owner, nx=True, ex=self.ttl))

    def acquire_any(self, candidates: Iterable[int], owner: str) -> Optional[int]:
        for g in candidates:
            if self.acquire(g, owner):
                return int(g)
        return None

    def refresh(self, gpu: int, owner: str) -> bool:
        k = self.key(gpu)
        v = self.r.get(k)
        if v is not None and v.decode() == owner:
            return bool(self.r.expire(k, self.ttl))
        return False

    def release(self, gpu: int, owner: str) -> bool:
        return bool(self._release(keys=[self.key(gpu)], args=[owner]))

    def force_release(self, gpu: int) -> bool:
        return bool(self.r.delete(self.key(gpu)))
