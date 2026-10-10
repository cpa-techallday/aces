"""Hostile (test fixture): writes many 32 MiB files in the job directory, 0.2 s apart. With ACES_CAD_MAX_RUN_MB=64 the
parent job-directory check (once a second) kills it: kind disk.
"""
import time

from build123d import Box

PARAMS = {
    "files": {"default": 64, "min": 1, "max": 1000, "doc": "how many files to write"},
    "mib": {"default": 32, "min": 1, "max": 256, "doc": "size of each file in MiB"},
}


def build(p):
    chunk = bytes(p.mib << 20)
    for i in range(p.files):
        with open("blob-" + str(i) + ".bin", "wb") as fh:
            fh.write(chunk)
        time.sleep(0.2)
    return Box(1, 1, 1)
