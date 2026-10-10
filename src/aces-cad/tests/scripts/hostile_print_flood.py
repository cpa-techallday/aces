"""Hostile (test fixture): prints about 10 MB, then returns a valid part. The run completes; the returned stdout is
head + tail (at most 64 KiB) with an omission marker, and stdout_truncated is true.
"""
import sys

from build123d import Box

PARAMS = {"megabytes": {"default": 10, "min": 1, "max": 100, "doc": "how much to print, in units of 10**6 bytes"}}


def build(p):
    line = "x" * 999 + "\n"
    for _ in range(p.megabytes * 1000):
        sys.stdout.write(line)
    sys.stdout.flush()
    return Box(1, 1, 1)
