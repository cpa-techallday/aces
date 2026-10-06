"""Receiver/transmit chain cascade analysis (Friis) for the radar fast tier.

Every number that enters a cascade must be datasheet-cited at the point of use
(spec template or calibration row); this module only does the arithmetic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def from_db(db: float) -> float:
    return 10.0 ** (db / 10.0)


def to_db(x: float) -> float:
    return 10.0 * math.log10(x)


@dataclass(frozen=True)
class Stage:
    """One cascade stage.  Gain may be negative (a lossy passive)."""

    name: str
    gain_db: float
    nf_db: float
    op1db_dbm: float | None = None  # output 1 dB compression point, if the datasheet gives one


@dataclass(frozen=True)
class CascadeResult:
    gain_db: float
    nf_db: float
    output_p1db_dbm: float | None  # output-referred cascade compression, if all stages declare one


def cascade(stages: list[Stage]) -> CascadeResult:
    """Friis noise-figure and gain cascade over ``stages`` in signal order.

    F = f1 + (f2-1)/g1 + (f3-1)/(g1*g2) + ...
    Output compression walks backward: P1dB_out of the cascade is set by the
    earliest compressing stage referred to its output plane.
    """
    if not stages:
        raise ValueError("cascade needs at least one stage")
    f_tot = 0.0
    g_cum = 1.0
    for i, s in enumerate(stages):
        f = from_db(s.nf_db)
        if i == 0:
            f_tot = f
        else:
            f_tot += (f - 1.0) / g_cum
        g_cum *= from_db(s.gain_db)

    op1db: float | None = None
    if all(s.op1db_dbm is not None for s in stages):
        # Walk from the last stage back to the first, referring each stage's
        # compression point to the cascade output through later-stage gains.
        gains_after = 0.0
        p_out = stages[-1].op1db_dbm
        for s in reversed(stages[:-1]):
            gains_after += s.gain_db  # gain of stages between s and output
            # s compresses when its own output hits its op1db; that level
            # referred to the cascade output adds the gains after s.
            cand = s.op1db_dbm + gains_after
            p_out = min(p_out, cand)
        op1db = p_out

    return CascadeResult(
        gain_db=to_db(g_cum),
        nf_db=to_db(f_tot),
        output_p1db_dbm=op1db,
    )
