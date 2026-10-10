#!/usr/bin/env bash
# Vendor a snapshot of the desktop-side engineering code from cudax-sim-runtime (read only) into vendor/:
#   vendor/simlab     experiment runtime: spec, run, worker, evidence, evidence bundles, report, registry, capacity,
#                     tracking, surrogate, solver adapters (the rig-only queue, leases and MCP server come along so
#                     the snapshot stays whole; the aces-simlab wrapper keeps them off the desktop CLI)
#   vendor/gendesign  generative design: voxel H8 FEA, SIMP, FFT filter, export, evaluators, parametric, nozzle,
#                     lattice (GPU), Warp kernels (GPU), Fusion bridge, progress beacon
#   vendor/examples   generic structural cases from cases/ (self-test inputs and user examples)
# Left out: __pycache__, simlab/solvers.json.bak-*, simlab/console (a Starlette app plus its static front-end; the
# SIMLAB console runs on the rig at :8032 and aces-consoles opens it as a web app).
# Text is normalised to LF (the workstation tree is CRLF; this repo stores LF) and nothing else is changed.
# SNAPSHOT records the source, the UTC sync time, the source commit and a sha256 over the vendored tree.
#
#   bash src/aces-engineering/sync.sh [--commit REV] [path to cudax-sim-runtime]
#        (default path: $CUDAX_SIM_RUNTIME or the workstation path)
#
# --commit REV copies from a clean clone (git clone --no-local) checked out at REV, so the snapshot is exactly a
# commit's bytes whatever the working tree holds (another session's uncommitted changes included). Without --commit
# the working tree is copied as it is, with a warning when simlab/, gendesign/ or cases/ hold uncommitted changes.
#
# Every hash is sha256sum -b: Git Bash prints "hash *path" and Linux prints "hash  path" in text mode, so only the
# binary form gives the same manifest, and the same content_sha256, on both. The aces-engineering PKGBUILD re-checks
# it with: cd vendor && find . -type f | LC_ALL=C sort | sed 's|^\./||' | xargs sha256sum -b | sha256sum
#
# Snapshot procedure (the integrator; never edit vendor/ or SNAPSHOT by hand):
#   bash src/aces-engineering/sync.sh --commit rig-golden-1 /c/Users/shedm/cudax-sim-runtime
#   then copy source_commit (the full sha) and content_sha256 from SNAPSHOT into _snapshot_commit and
#   _snapshot_content_sha256 in pkgbuilds/aces-engineering/PKGBUILD, and commit vendor/, SNAPSHOT and the PKGBUILD.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
rev=""
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) [ $# -ge 2 ] || { echo "sync.sh: --commit needs a revision" >&2; exit 2; }; rev="$2"; shift 2 ;;
    --commit=*) rev="${1#--commit=}"; shift ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    -*) echo "sync.sh: unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -le 1 ] || { echo "usage: sync.sh [--commit REV] [path to cudax-sim-runtime]" >&2; exit 2; }
src="${1:-${CUDAX_SIM_RUNTIME:-/c/Users/shedm/cudax-sim-runtime}}"
examples=(cantilever3d.json bracket_topopt.json exp_beam_validation.yaml exp_cantilever_volfrac.yaml
          bracket_param.py bracket_build123d_check.py)

tmp=""
cleanup() { if [ -n "$tmp" ]; then rm -rf "$tmp"; fi; }
trap cleanup EXIT
if [ -n "$rev" ]; then
  git -C "$src" rev-parse --verify --quiet "$rev^{commit}" >/dev/null ||
    { echo "sync.sh: $rev is not a commit in $src" >&2; exit 1; }
  tmp="$(mktemp -d)"
  git clone --quiet --no-local "$src" "$tmp/clone"
  git -C "$tmp/clone" -c advice.detachedHead=false checkout --quiet "$(git -C "$src" rev-parse "$rev^{commit}")"
  from="$tmp/clone"
  full="$(git -C "$from" rev-parse HEAD)"
  dirty="$(git -C "$from" status --porcelain -- simlab gendesign cases | wc -l | tr -d ' ')"
  source_line="$src @ $rev (clean clone)"
  commit_line="$full (clean clone at $rev; $dirty uncommitted changes)"
else
  from="$src"
  source_line="$src"
  if full="$(git -C "$src" rev-parse HEAD 2>/dev/null)"; then
    dirty="$(git -C "$src" status --porcelain -- simlab gendesign cases | wc -l | tr -d ' ')"
    if [ "$dirty" != 0 ]; then
      echo "sync.sh: warning: $dirty uncommitted changes under simlab/ gendesign/ cases/ in $src are copied as they" \
           "are; use --commit REV to vendor a commit's bytes" >&2
    fi
    commit_line="$full ($dirty uncommitted changes under simlab/ gendesign/ cases/ at sync time)"
  else
    commit_line="TODO: cudax-sim-runtime is not under git yet; record its commit here at the first sync after it is"
  fi
fi
for d in simlab gendesign cases; do
  [ -d "$from/$d" ] || { echo "sync.sh: $from/$d not found" >&2; exit 1; }
done

out="$here/vendor"
rm -rf "$out"
mkdir -p "$out/examples"
cp -r "$from/simlab" "$from/gendesign" "$out/"
for f in "${examples[@]}"; do cp "$from/cases/$f" "$out/examples/$f"; done
find "$out" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$out" -name '*.pyc' -delete
rm -f "$out"/simlab/solvers.json.bak-*
rm -rf "$out/simlab/console"
find "$out" -type f \( -name '*.py' -o -name '*.json' -o -name '*.yaml' \) -exec sed -i 's/\r$//' {} +

# content hash: sha256 over the sorted "sha256 *path" manifest (sha256sum -b) of every vendored file
manifest="$(cd "$out" && find . -type f | LC_ALL=C sort | sed 's|^\./||' | xargs sha256sum -b)"
content="$(printf '%s\n' "$manifest" | sha256sum -b | cut -d' ' -f1)"
{
  echo "# Vendored snapshot of cudax-sim-runtime (written by sync.sh; do not edit by hand)"
  echo "source: $source_line"
  echo "synced_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source_commit: $commit_line"
  echo "content_sha256: $content"
  echo "files: $(printf '%s\n' "$manifest" | wc -l | tr -d ' ')"
  echo "excluded: __pycache__, simlab/solvers.json.bak-*, simlab/console (rig-hosted UI)"
  echo "line_endings: normalised to LF on copy (source files are CRLF)"
  echo "manifest:"
  printf '%s\n' "$manifest"
} > "$here/SNAPSHOT"
echo "sync.sh: $(printf '%s\n' "$manifest" | wc -l | tr -d ' ') files from $source_line -> vendor/, content sha256 $content"
