# ACES Data Pull — Rig Download Status

Started 2026-10-10. Target: `/mnt/nvme2/data-library/` on the rig (192.168.1.65).
Driver: `pull.sh` (resumable `curl -C -` loop, safe to re-run; lock file `pull.lock`).
Queue: `manifest.tsv` — 301 files. Log: `pull.log`. Monitor: `tail -f pull.log`.

Repo copies of manifests live in `aces-push/research/data-pull/` (the local
`manifest.tsv` there is the seed; the rig's copy is the live queue — appended in place).

## Queued

| Source | Path on rig | Files | Size | Notes |
|---|---|---|---|---|
| The Well — shear_flow | `the-well/shear_flow/` | 73 | ~229 GB | full valid+test; train L-subset: Re=1e5 × all Schmidt + Sc=1e0 × all Re |
| The Well — rayleigh_benard | `the-well/rayleigh_benard/` | 89 | ~168 GB | full valid+test; train L-subset: Ra=1e8 × all Prandtl + Pr=1 × all Ra |
| AirfRANS | `airfrans/` | 2 | 81.3 GB | preprocessed ML pack (10 GB) + raw OpenFOAM (71 GB) |
| NIST AM Bench 2607 | `am-bench/2607-challenge-description/` | 60 | small | CAD, scan strategy h5, powder data, thermocouples, templates |
| NIST AM Bench 2711 | `am-bench/2711-residual-stress-deflection/` | 24 | small | residual stress/strain + bridge deflection results |
| NIST AM Bench 2715 | `am-bench/2715-thermography/` | 15 | ~0.3 GB | processing scripts + B6 TAM/SCR h5. **Raw Signal/Te05 camera files (up to 119 GB each) deliberately excluded** — disk budget; fetch per-file later if needed |
| KABlab (BU, all 5 datasets) | `kab-lattice/` | 27 | ~4.6 GB | incl. RawTestData_Quasistatic.zip + RawTestData_Impact.zip (the lattice test anchor) + tough-structures ADTS 25k shells + BEAR + impact-TL |
| KAB code | `kab-lattice/code-*/` | 2 repos | small | git clones: Designing-Lattices-for-Impact-Protection, gcs |
| MCB (Purdue/KAIST) | `mcb/` | 2 | ~5 GB | MCB_A.tar.gz 2.5 GB (direct Box link works) + MCB_B.tar.gz |
| ABC subset (HF mirror) | `abc-tactile/` | 9 | ~6 GB | 8 parquet shards of processed ABC + README; dev subset for tier-1 pipeline |

Total queued ≈ **494 GB** against 705 GB free at start (keeps ≥100 GB headroom for
the simlab queue on the same disk).

## Not pulled / blocked

| Item | Why | Unblock |
|---|---|---|
| The Well full train splits (376 + 294 GB) | exceed disk budget | bigger disk, or pull per-combo on demand |
| AM Bench raw thermography Signal/Te05 (~140 GB for B6 alone) | disk budget | pull individual files from mds2-2715 when analysis needs them |
| KAB Google Drive folder | 401 — Google now requires sign-in for folder listing | bypassed: full collection pulled from open.bu.edu DSpace instead |
| JHTDB channel DNS (Re_τ 1000–5200) | ~~needs token~~ **done 2026-10-10** via public testing token + `giverny` client | results in `research/jhtdb/` (repo) and `/mnt/nvme2/data-library/jhtdb/` (rig); heavier queries (time-averaged statistics, more datasets) need a personal CILogon token |
| ABC full chunks (50–100 GB each, 1M models) | disk budget + registration on the official site | pull when tier-1 generative training actually starts |
| DrivAerNet++ (39 TB) | far beyond rig storage | revisit with dedicated storage; smoke-test subset exists on HF |
| Mechanical MNIST (70k FEM) | not in this round's shortlist | open.bu.edu handle 2144/39371 when tier-4 starts |

## Verify after completion

```
ssh pi@192.168.1.65
cat /mnt/nvme2/data-library/VERIFY-REPORT.txt   # authoritative (see below)
du -sh /mnt/nvme2/data-library/*
```

`pull.sh` re-run is idempotent: files matching expected size are skipped.

## Status: COMPLETE (2026-10-13)

**301/301 files, 464 GB, verified.** Authoritative report:
`/mnt/nvme2/data-library/VERIFY-REPORT.txt` (clean-manifest, size-checked —
do NOT trust `pull.log`'s built-in verify: the seed `manifest.tsv` was written
with CRLF line endings, so its size comparisons always failed and the log's
"SIZE MISMATCH" warnings are false alarms; `manifest.clean.tsv` on the rig is
the LF-fixed copy used for the real verify).

Verification performed (all on the rig, 2026-10-13):

- **Size check:** 301/301 exact against the clean manifest
- **AM Bench sha256:** 25/25 sidecar checksums match
- **HDF5:** all 150 (The Well + AM Bench h5) open cleanly in h5py
- **Parquet:** all 8 ABC shards readable (schema: id, label, mesh.vertices, mesh.faces)
- **Archives:** every .zip passes `unzip -t`; both MCB tarballs pass `gzip -t`;
  both AirfRANS zips (71 + 10 GB) have valid central directories
  (OF_dataset.zip = 136,490 entries)
- **One casualty found and repaired:** `kab-lattice/tough-structures/ADTS10001-14999.zip`
  arrived truncated (403 KB — BU server closed early and curl accepted it);
  re-downloaded 2026-10-13 (`repair-adts.log` on the rig)
- **Smoke-load script:** `research/data-pull/smoke_load.py` (this repo) — opens one
  file of every kind in the `simlab-pyfr:0.3` image and dumps structures/schemas;
  re-run it after any future pull

Known caveats:

- The Well train splits are the L-subsets per the original plan (full trains
  remain on-demand — disk budget)
- AM Bench 2715 raw camera files deliberately excluded (up to 119 GB each)
- abc-tactile is the 8-shard dev subset, not full ABC
