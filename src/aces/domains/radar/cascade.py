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
    input_p1db_dbm: float | None = None  # the same point referred to the input: output P1dB - gain


def cascade(stages: list[Stage]) -> CascadeResult:
    """Friis noise-figure and gain cascade over ``stages`` in signal order.

    F = f1 + (f2-1)/g1 + (f3-1)/(g1*g2) + ...

    Compression is cascaded OUTPUT-referred, from each stage's datasheet output P1dB in mW:

        1 / P1dB_out = sum_i 1 / (OP1dB_i * G_after_i),   G_after_i = gain of the stages after i

    the same form as the cascaded intercept point (Pozar, Microwave Engineering, intermodulation of
    cascaded components); for P1dB it is the usual first-order estimate.  The input-referred value is
    P1dB_out / G_total (small-signal gain), which equals 1 / sum_i 1 / (IP1dB_i / G_before_i) with
    IP1dB_i = OP1dB_i / G_i.  Both are None unless every stage gives an output P1dB.
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
    ip1db: float | None = None
    if all(s.op1db_dbm is not None for s in stages):
        inv = 0.0
        g_after = 1.0  # linear gain from the output of stage i to the cascade output
        for s in reversed(stages):
            inv += 1.0 / (from_db(s.op1db_dbm) * g_after)
            g_after *= from_db(s.gain_db)
        op1db = to_db(1.0 / inv)
        ip1db = op1db - to_db(g_cum)

    return CascadeResult(
        gain_db=to_db(g_cum),
        nf_db=to_db(f_tot),
        output_p1db_dbm=op1db,
        input_p1db_dbm=ip1db,
    )
