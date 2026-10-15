#!/bin/bash
# ACES data-library resumable pull.
# Reads manifest.tsv: <url>\t<dest>\t<expected_bytes>
# Resumes partial files (curl -C -), retries forever with backoff,
# skips files that already match expected size. Safe to re-run anytime.
BASE=/mnt/nvme2/data-library
MANIFEST=$BASE/manifest.tsv
LOG=$BASE/pull.log
LOCK=$BASE/pull.lock

if [ -f "$LOCK" ]; then
  echo "already running (lock exists)"; exit 0
fi
touch "$LOCK"
trap 'rm -f "$LOCK"' EXIT

echo "=== pull started $(date -Is) ===" >> "$LOG"

while IFS=$'\t' read -r url dest size; do
  [ -z "$url" ] && continue
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && [ -n "$size" ] && [ "$(stat -c%s "$dest" 2>/dev/null)" = "$size" ]; then
    continue  # already complete
  fi
  echo "[$(date -Is)] fetching $dest" >> "$LOG"
  until curl -fL -C - --retry 20 --retry-delay 20 --retry-all-errors \
        --connect-timeout 30 --speed-time 120 --speed-limit 1024 \
        -o "$dest" "$url" >> "$LOG" 2>&1; do
    echo "[$(date -Is)] retry $url" >> "$LOG"
    sleep 20
  done
  if [ -n "$size" ] && [ "$(stat -c%s "$dest")" != "$size" ]; then
    echo "[$(date -Is)] SIZE MISMATCH $dest" >> "$LOG"
  fi
done < "$MANIFEST"

echo "=== pull finished $(date -Is) ===" >> "$LOG"

# verification report
{
  echo "=== verify $(date -Is) ==="
  missing=0; done_n=0
  while IFS=$'\t' read -r url dest size; do
    [ -z "$url" ] && continue
    if [ -f "$dest" ] && [ "$(stat -c%s "$dest" 2>/dev/null)" = "$size" ]; then
      done_n=$((done_n+1))
    else
      missing=$((missing+1)); echo "INCOMPLETE: $dest"
    fi
  done < "$MANIFEST"
  echo "complete=$done_n incomplete=$missing"
  df -h /mnt/nvme2 | tail -1
} >> "$LOG"
