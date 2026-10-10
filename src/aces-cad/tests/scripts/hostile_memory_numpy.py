"""Hostile (test fixture): one numpy allocation of 2**33 float64 values (64 GiB): MemoryError under RLIMIT_AS."""
import numpy as np


def build(p):
    return np.ones(2 ** 33)
