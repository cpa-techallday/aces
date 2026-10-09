"""FFT-based density filter for topology optimisation on a structured grid.

The classic linear (conic) filter x~ = sum_j w_ij x_j / sum_j w_ij with
w_ij = max(0, rmin - dist_ij), implemented as a zero-padded FFT convolution so the
cost does not grow with the filter radius. Works with NumPy or CuPy arrays.
"""
from __future__ import annotations

import math

import numpy as np


class FFTDensityFilter:
    def __init__(self, nx: int, ny: int, nz: int, h: float, rmin: float, xp, dtype="float64"):
        self.xp = xp
        self.shape = (int(nx), int(ny), int(nz))
        self.dtype = np.dtype(dtype)
        R = max(1, int(math.ceil(rmin / h - 1e-9)))
        self.R = R
        o = xp.arange(-R, R + 1)
        dx, dy, dz = xp.meshgrid(o, o, o, indexing="ij")
        dist = xp.sqrt((dx ** 2 + dy ** 2 + dz ** 2).astype(self.dtype)) * h
        w = xp.maximum(rmin - dist, 0.0).astype(self.dtype)
        self.P = tuple(n + 2 * R for n in self.shape)
        kern = xp.zeros(self.P, dtype=self.dtype)
        kern[:2 * R + 1, :2 * R + 1, :2 * R + 1] = w
        kern = xp.roll(kern, (-R, -R, -R), axis=(0, 1, 2))
        self.Kf = xp.fft.rfftn(kern)
        self.Hs = self._conv(xp.ones(self.shape, dtype=self.dtype))

    def _conv(self, a):
        xp = self.xp
        R = self.R
        nx, ny, nz = self.shape
        ap = xp.zeros(self.P, dtype=self.dtype)
        ap[R:R + nx, R:R + ny, R:R + nz] = a
        c = xp.fft.irfftn(xp.fft.rfftn(ap) * self.Kf, s=self.P)
        return c[R:R + nx, R:R + ny, R:R + nz].astype(self.dtype)

    def apply(self, a):
        """Filtered (physical) densities from design densities."""
        return self._conv(a) / self.Hs

    def apply_T(self, a):
        """Chain rule: sensitivity w.r.t. design densities from sensitivity w.r.t. physical ones."""
        return self._conv(a / self.Hs)
