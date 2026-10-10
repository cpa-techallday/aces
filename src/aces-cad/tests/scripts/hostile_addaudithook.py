"""Hostile (test fixture): adds its own audit hook, which could hide events from later hooks; refused (kind sandbox)."""
import sys


def build(p):
    sys.addaudithook(lambda event, args: None)
    raise RuntimeError("sys.addaudithook was not refused")
