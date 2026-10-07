#!/usr/bin/env bash
# In-container Palace build for hosts where the foreground shell is wall-capped.
# Mirrors docker/aces-em/Dockerfile; checkpoints progress to /build/STATUS so
# `docker logs` tells the truth about where it is.
set -uo pipefail

say() { echo "[aces-em-build $(date +%H:%M:%S)] $*"; }

say "apt install"
apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    build-essential gfortran cmake ninja-build git pkg-config \
    python3 python3-pip python3-venv ca-certificates wget curl \
    libopenmpi-dev openmpi-bin libopenblas-dev || { say "APT FAILED"; exit 1; }

say "clone palace ${PALACE_REF:-v0.14.0}"
git clone --depth 1 --branch "${PALACE_REF:-v0.14.0}" https://github.com/awslabs/palace.git /opt/palace-src || { say "CLONE FAILED"; exit 1; }

cd /opt/palace-src
say "superbuild bit-rot fixes"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "${SCRIPT_DIR}/fix_superbuild.sh" /opt/palace-src || { say "FIX FAILED"; exit 1; }

say "cmake configure"
cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DPALACE_WITH_SUPERBUILD=ON \
    -DPALACE_WITH_SLEPC=ON \
    -DPALACE_WITH_OPENMP=ON \
    -DCMAKE_INSTALL_PREFIX=/opt/palace || { say "CONFIGURE FAILED"; exit 1; }

say "build START (this is the long part: MFEM/PETSc/SLEPc/hypre from source)"
cmake --build build -j"$(nproc)" || { say "BUILD FAILED"; exit 1; }
cmake --install build || { say "INSTALL FAILED"; exit 1; }

say "gprMax pip install"
python3 -m venv /opt/venv
/opt/venv/bin/pip install --no-cache-dir -q gprMax h5py numpy || { say "PIP FAILED"; exit 1; }

export PATH=/opt/palace/bin:/opt/venv/bin:$PATH
say "smoke: palace --version"
palace --version || { say "PALACE SMOKE FAILED"; exit 1; }
say "smoke: gprMax import"
python3 -c "import gprMax; print('gprMax ok')" || { say "GPRMAX SMOKE FAILED"; exit 1; }

say "BUILD COMPLETE"
