#!/usr/bin/env bash
# Compare the rig's solvers.json with its newest backup: only the aces-tpms-periodic key may differ.
set -euo pipefail
cd /opt/simlab-runtime/simlab
for f in solvers.json solvers.json.bak-*; do
  printf '%s CR=%s lines=%s\n' "$f" "$(grep -c $'\r' "$f")" "$(wc -l < "$f")"
done
B=$(ls -t solvers.json.bak-* | head -1)  # newest backup (audit L4)
echo "--- semantic diff vs $B (only the new key should appear)"
python3 - "$B" <<'PY'
import json, sys
a = json.load(open(sys.argv[1])); b = json.load(open("solvers.json"))
print("added:", sorted(set(b) - set(a)), "removed:", sorted(set(a) - set(b)),
      "changed:", sorted(k for k in a if k in b and a[k] != b[k]))
PY
echo "--- text lines changed (ignoring CR)"
diff <(sed 's/\r$//' "$B") <(sed 's/\r$//' solvers.json) | grep -c '^[<>]' || true
