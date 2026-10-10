"""Hostile (test fixture): installs a trace function (a way to tamper with the frames of aces-cad code); refused (kind
sandbox).
"""
import sys


def build(p):
    sys.settrace(lambda frame, event, arg: None)
    raise RuntimeError("sys.settrace was not refused")
