# JHTDB Channel-Flow Anchor

Pulled 2026-10-10 via the `giverny` client (pyJHTDB is deprecated) using the JHTDB
public testing token. Script: `pull_jhtdb.py` (also on the rig at
`/mnt/nvme2/data-library/jhtdb/`). Raw output: `jhtdb-channel.json`.

## What was pulled

| Dataset | Re_τ | Time | Query | Points |
|---|---|---|---|---|
| `channel` | 1000 | t=0.0 | mean U(y) over 16 (x,z) columns, 240 clustered y-rows + wall gradient | ≤4096/request |
| `channel5200` | 5200 | t=1 (valid range 1–11, integer) | same | ≤4096/request |

Everything below is derived from the queried data + the published Re_τ only —
no literature values baked in: ν from the measured wall gradient and Re_τ,
u_τ from ν, U_b from integrating the measured profile.

## Results

| Dataset | Re_b (derived) | U_b/u_τ | cf = τ_w/(½ρU_b²) | Darcy f = 4cf |
|---|---|---|---|---|
| channel (Re_τ 1000) | 36,718 | 18.36 | 0.00593 | 0.02373 |
| channel5200 (Re_τ 5200) | 240,108 | 23.09 | 0.00375 | 0.01501 |

Self-consistency: cf = 2/(U_b/u_τ)² holds exactly by construction; the interest
is that the *measured* U_b/u_τ values (18.36, 23.09) land on the canonical
log-law values for these Re_τ. Between the two Reynolds numbers the derived cf
follows Blasius f ∝ Re^(−1/4) to within 1.1% (ratio 1.581 vs 1.599) — the pull
pipeline (grid, interpolation Lag8, FD4 wall gradient, testing-token point caps)
is therefore validated end-to-end.

## Role in the calibration registry

This is the **canonical smooth-wall reference line** for §1b of the training
data library: cf(Re_b) ≈ 0.0059 @ 37k → 0.0038 @ 240k, Darcy f 0.024 → 0.015.

Against our fitted TPMS factors (D30 registry): gyroid f = 4.0855×(Re/28.8)^0.344
is ~3 orders of magnitude above the smooth-channel line at any comparable
Re — as it must be for a form-drag-dominated lattice — while the *scaling law*
comparison is the useful part: smooth turbulent wall friction decays as
Re^(−0.25), so any future medium/geometry whose fitted exponent drifts far from
physical wall-drag scaling without a documented reason (form drag, laminar
regime) should trip review, same spirit as the 0.15 scatter refusal line.

Full 240-row profiles (y, y+, U, V, W, U+) are in `jhtdb-channel.json` for
plotting law-of-the-wall overlays against future rig runs.

## Caveats

- Testing token caps requests at 4096 points; profiles used 16-column spatial
  averaging of one snapshot per dataset — not time-averaged statistics. Mean U
  converges well under this; don't use this data for RMS/second-order moments.
- channel5200 time index must be an integer in [1, 11] (t=0.0 is rejected).
- The pyJHTDB package is deprecated; use `givernylocal` (SciServer).
