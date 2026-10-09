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
#   bash src/aces-engineering/sync.sh [path to cudax-sim-runtime]    (default: $CUDAX_SIM_RUNTIME or the workstation path)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="${1:-${CUDAX_SIM_RUNTIME:-/c/Users/shedm/cudax-sim-runtime}}"
examples=(cantilever3d.json bracket_topopt.json exp_beam_validation.yaml exp_cantilever_volfrac.yaml
          bracket_param.py bracket_build123d_check.py)
for d in simlab gendesign cases; do
  [ -d "$src/$d" ] || { echo "sync.sh: $src/$d not found" >&2; exit 1; }
done

out="$here/vendor"
rm -rf "$out"
mkdir -p "$out/examples"
cp -r "$src/simlab" "$src/gendesign" "$out/"
for f in "${examples[@]}"; do cp "$src/cases/$f" "$out/examples/$f"; done
find "$out" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$out" -name '*.pyc' -delete
rm -f "$out"/simlab/solvers.json.bak-*
rm -rf "$out/simlab/console"
find "$out" -type f \( -name '*.py' -o -name '*.json' -o -name '*.yaml' \) -exec sed -i 's/\r$//' {} +

# content hash: sha256 over the sorted "sha256  path" manifest of every vendored file
manifest="$(cd "$out" && find . -type f | LC_ALL=C sort | sed 's|^\./||' | xargs sha256sum)"
content="$(printf '%s\n' "$manifest" | sha256sum | cut -d' ' -f1)"
if commit="$(git -C "$src" rev-parse HEAD 2>/dev/null)"; then
  dirty="$(git -C "$src" status --porcelain -- simlab gendesign cases | wc -l)"
  commit="$commit ($dirty uncommitted changes under simlab/ gendesign/ cases/ at sync time)"
else
  commit="TODO: cudax-sim-runtime is not under git yet; record its commit here at the first sync after it is"
fi
{
  echo "# Vendored snapshot of cudax-sim-runtime (written by sync.sh; do not edit by hand)"
  echo "source: $src"
  echo "synced_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source_commit: $commit"
  echo "content_sha256: $content"
  echo "files: $(printf '%s\n' "$manifest" | wc -l | tr -d ' ')"
  echo "excluded: __pycache__, simlab/solvers.json.bak-*, simlab/console (rig-hosted UI)"
  echo "line_endings: normalised to LF on copy (source files are CRLF)"
  echo "manifest:"
  printf '%s\n' "$manifest"
} > "$here/SNAPSHOT"
echo "sync.sh: $(printf '%s\n' "$manifest" | wc -l | tr -d ' ') files from $src -> vendor/, content sha256 $content"
