"""A stand-in for `aces-golden` for tests, reached through ACES_GOLDEN_CMD (src/aces-base/aces/cli.py:218-231).

    python fake_golden.py verify <run_dir> --json --no-model

prints the real `aces golden verify --json` record of the VM acceptance of 2026-10-10 verbatim
(rehearsal/reports/2026-10-10-aces-cad-vm/records/golden-handoff-verify.json: 13 checks, 11 ok, rig and resolve not
run) and exits 0, so the console's verify route can be exercised on a machine without aces-engineering. Nothing is
generated: the run_dir inside the record is the VM's, as recorded. Any other invocation exits 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

RECORD = Path(__file__).resolve().parents[4] / "rehearsal" / "reports" / "2026-10-10-aces-cad-vm" / "records" / "golden-handoff-verify.json"


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "verify" and "--json" in argv:
        if not RECORD.is_file():
            print(f"fake_golden: the real verify record {RECORD} is absent", file=sys.stderr)
            return 2
        sys.stdout.write(RECORD.read_text(encoding="utf-8"))
        return 0
    print("fake_golden: only `verify <run_dir> --json [--no-model]` is replayed", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
