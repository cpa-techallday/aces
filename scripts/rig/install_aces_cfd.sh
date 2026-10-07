#!/usr/bin/env bash
# Register aces-tpms-periodic in the rig's simlab and place the ACES experiment specs.
# /opt/simlab-runtime is root-owned, so the edits run in a throwaway container (pi is in the docker group).
set -euo pipefail
B=~/aces-cfd-build
IMG_ID=$(docker image inspect aces-cfd:0.1 --format '{{.Id}}')
docker run --rm --entrypoint python3 \
  -v /opt/simlab-runtime/simlab:/s -v "$B":/b:ro \
  simlab-queue:0.3 /b/register_solver.py /s/solvers.json "$IMG_ID"
docker run --rm --entrypoint sh -v /opt/simlab-runtime/cases:/c -v "$B":/b:ro simlab-queue:0.3 -c \
  'set -e; mkdir -p /c/aces; stamp=$(date +%Y%m%d-%H%M%S)
   for f in aces-cfd-validation.yaml aces-cfd-resolution.yaml aces-cal-air-01.yaml; do
     if [ -e "/c/aces/$f" ]; then
       if cmp -s "/b/$f" "/c/aces/$f"; then continue; fi
       cp -p "/c/aces/$f" "/c/aces/$f.bak-$stamp"   # never overwrite a case file without a backup (audit L4)
     fi
     cp "/b/$f" "/c/aces/$f"
   done
   ls -la /c/aces'
echo "--- registry as simlab sees it"
docker exec simlab-queue-1 python -m simlab solvers | grep -E 'aces|gendesign-beam' || true
for f in aces-cfd-validation aces-cfd-resolution aces-cal-air-01; do
  echo "--- simlab validate $f"
  docker exec -w /work simlab-queue-1 python -m simlab validate cases/aces/$f.yaml 2>&1 | tail -6
done
