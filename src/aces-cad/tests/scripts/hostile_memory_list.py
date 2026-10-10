"""Hostile (test fixture): a list that grows by 64 MiB blocks until RLIMIT_AS stops it (kind memory, exit 4).

bytes(n) asks for zeroed memory (calloc), so the address space grows to the limit without touching every page.
"""


def build(p):
    hoard = []
    while True:
        hoard.append(bytes(64 << 20))
