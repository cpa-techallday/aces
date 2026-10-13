#!/usr/bin/env python3
"""Delete orphaned HF blob files listed in /tmp/hf-orphans.json.

Skips the .huggingface-shared-blobs marker (not a data blob).
Prints per-file failures and total freed bytes.
"""
import json, os, sys

with open("/tmp/hf-orphans.json") as f:
    data = json.load(f)

paths = data["orphans"] if isinstance(data, dict) else data
freed = 0
failed = []
skipped = 0
for p in paths:
    if ".huggingface-shared-blobs" in p:
        skipped += 1
        continue
    try:
        size = os.path.getsize(p) if os.path.isfile(p) else 0
        if os.path.isdir(p):
            skipped += 1
            continue
        os.remove(p)
        freed += size
    except FileNotFoundError:
        pass
    except Exception as e:
        failed.append((p, str(e)))

print(f"freed_bytes={freed} ({freed/1e9:.1f} GB)")
print(f"skipped={skipped}")
print(f"failed={len(failed)}")
for p, e in failed[:20]:
    print("FAIL", p, e)
sys.exit(1 if failed else 0)
