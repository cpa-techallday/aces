"""Run the registry validation cases on whatever device this process sees.

    python -m aces.cfd.selftest [--cpu] [--n 24]

Prints one JSON line per case (square duct and plane channel against exact fRe and Nu_H1),
the device, and exits non-zero if any case is outside 1.5 % (fRe) or 3 % (Nu) at this
resolution.  The output is what goes into the simlab registry's validation record.
"""
from __future__ import annotations

import argparse
import json
import sys

from .simlab_adapter import Adapter


class _Spec:
    id = "aces-cfd-selftest"

    class resources:
        precision = "float64"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--n", type=int, default=24)
    a = ap.parse_args(argv)
    ok = True
    for case in ("square_duct", "plane_channel"):
        out = Adapter().run({"validate": case, "n": a.n, "cpu": a.cpu}, _Spec, ".")
        out.pop("artifacts", None)
        print(json.dumps(out))
        ok &= abs(out["fRe_err_pct"]) < 1.5 and abs(out["Nu_err_pct"]) < 3.0 and out["converged"] == 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
