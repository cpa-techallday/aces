"""Hostile (test fixture): writes one big file in the job directory, 1 MiB at a time (2 GiB by default). With
ACES_CAD_MAX_FILE_MB=64, RLIMIT_FSIZE stops it with EFBIG (kind disk, limit file_size) and the file stays at 64 MiB.
"""
from build123d import Box

PARAMS = {"mib": {"default": 2048, "min": 1, "max": 4096, "doc": "how many MiB to write"}}


def build(p):
    chunk = bytes(1 << 20)
    with open("big.bin", "wb") as fh:
        for _ in range(p.mib):
            fh.write(chunk)
    return Box(1, 1, 1)
