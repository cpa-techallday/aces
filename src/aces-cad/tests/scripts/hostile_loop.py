"""Hostile (test fixture): an endless loop. The parent wall clock (and RLIMIT_CPU) must end it: kind timeout, exit 4."""


def build(p):
    n = 0
    while True:
        n += 1
