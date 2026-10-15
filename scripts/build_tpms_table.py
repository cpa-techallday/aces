"""Rebuild src/aces/domains/hx/data/tpms_manufacturability.json.

Measured, not looked up: minimum and mean wall thickness along the mid-surface normal,
channel neck (largest passable sphere) for each fluid network, and isolated fluid
pockets, for every TPMS kind over the wall-ratio range the hx model searches.
Run after any change to aces.domains.hx.tpms; tests check the table against live
measurements.

    python scripts/build_tpms_table.py
"""
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

import numpy as np

from aces.domains.hx import tpms

RATIOS = [round(r, 2) for r in np.arange(0.03, 0.3501, 0.01)]
N = 48


def one(job):
    kind, r = job
    return kind, asdict(tpms.measure_manufacturability(kind, r, N))


def main():
    jobs = [(k, r) for k in tpms.KINDS for r in RATIOS]
    with ProcessPoolExecutor() as ex:
        results = list(ex.map(one, jobs))
    table = tpms.build_table([], N=N)
    for kind, row in results:
        table["kinds"].setdefault(kind, []).append(row)
    for k in table["kinds"]:
        table["kinds"][k].sort(key=lambda r: r["ratio"])
    tpms.TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tpms.TABLE_PATH.write_text(json.dumps(table, indent=1))
    print(f"wrote {tpms.TABLE_PATH}: {len(results)} rows")


if __name__ == "__main__":
    main()
