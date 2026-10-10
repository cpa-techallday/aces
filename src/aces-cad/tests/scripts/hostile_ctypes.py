"""Hostile (test fixture): loads the C library through ctypes; refused (kind sandbox)."""
import ctypes
import os


def build(p):
    ctypes.CDLL("msvcrt" if os.name == "nt" else "libc.so.6")
    raise RuntimeError("loading a library through ctypes was not refused")
