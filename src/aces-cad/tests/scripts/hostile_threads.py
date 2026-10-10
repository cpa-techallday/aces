"""Hostile (test fixture): 50 Python threads doing numpy work at once. Threads are allowed (clone with CLONE_THREAD), so
the run completes and returns a valid part.
"""
import threading

import numpy as np
from build123d import Box


def build(p):
    errors = []

    def work(seed):
        try:
            a = np.random.default_rng(seed).random((200, 200))
            float((a @ a).sum())
        except Exception as e:  # noqa: BLE001 (collected and reported below)
            errors.append(repr(e))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise RuntimeError("thread errors: " + "; ".join(errors[:3]))
    return Box(1, 1, 1)
