"""Hostile (test fixture): output with ANSI escape sequences and control characters, written the way native code
writes (straight to file descriptors 1 and 2) as well as through Python. The run completes, and the stdout and stderr
it returns carry the text without any escape sequence.
"""
import os
import sys

from build123d import Box


def build(p):
    sys.stdout.write("\x1b[1;32mpython stdout line (fixture)\x1b[0m\n")
    sys.stdout.flush()
    os.write(1, b"\x1b[2J\x1b[?25lnative stdout line (fixture)\x1b[0m\n")
    os.write(2, b"\x1b[1;31mOCCT-like error line (fixture)\x1b[0m\x07\x08\n")
    return Box(1, 1, 1)
