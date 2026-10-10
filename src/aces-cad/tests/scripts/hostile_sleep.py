"""Hostile (test fixture): sleeps for 10**6 s using no CPU, so only the parent wall clock can end it (kind timeout)."""
import time


def build(p):
    time.sleep(10 ** 6)
