# aces-em:0.1 — EM verification tier rig image (RUNTIME_PLAN §3)
#
# Palace (FEM, S-parameters / eigenmode ports) + gprMax (FDTD) in one image.
# Canonical definition for the rig build host (no wall-clock cap there); on
# DARKNESS the same recipe runs in a detached builder container (see
# build_in_container.sh) because the harness caps foreground commands.
#
# Build:  docker build -t aces-em:0.1 docker/aces-em
# Smoke:  docker run --rm aces-em:0.1 palace --version && python3 -c "import gprMax"
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gfortran cmake ninja-build git pkg-config \
        python3 python3-pip python3-venv ca-certificates wget curl \
        libopenmpi-dev openmpi-bin libopenblas-dev \
        libglu1-mesa libgl1 libxrender1 libxext6 libxft2 libxinerama1 \
        libxcursor1 libxi6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*
# (the libGL/libX set is for the gmsh Python wheel, which dlopens them even
# for headless mesh generation — needed by the adapter's Palace path)

# ---- Palace (superbuild pulls MFEM, libCEED, PETSc, SLEPc, hypre, METIS)
# SLEPc is required for eigenmode (waveguide-port) problems — wr90_through.
ARG PALACE_REF=v0.14.0
RUN git clone --depth 1 --branch ${PALACE_REF} https://github.com/awslabs/palace.git /opt/palace-src
WORKDIR /opt/palace-src
# Upstream bit-rot fixes (dead sundials pin, ninja-vs-make externals) — see script.
COPY fix_superbuild.sh /tmp/fix_superbuild.sh
RUN bash /tmp/fix_superbuild.sh /opt/palace-src
RUN cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DPALACE_WITH_SUPERBUILD=ON \
        -DPALACE_WITH_SLEPC=ON \
        -DPALACE_WITH_OPENMP=ON \
        -DCMAKE_INSTALL_PREFIX=/opt/palace \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build \
    && rm -rf /opt/palace-src
ENV PATH="/opt/palace/bin:${PATH}"
# palace is an mpirun wrapper; containers commonly run as root
ENV OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# ---- gprMax (FDTD tier, pip) + the aces package runtime deps so the image
# can drive `aces.domains.radar.em_adapter` end-to-end.
# NOTE: gprMax's OpenMP backend oversubscribes badly in a container (it
# spawned 71 threads on a 24-core host and thrashed ~50x slower). Run
# containers with `-e OMP_NUM_THREADS=<physical cores>` (16 verified).
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
        gprMax h5py numpy gmsh \
        pydantic pyyaml scipy scikit-image optuna CoolProp
ENV PATH="/opt/venv/bin:${PATH}"

LABEL org.opencontainers.image.title="aces-em" \
      org.opencontainers.image.version="0.1" \
      org.opencontainers.image.description="ACES EM verification tier: Palace FEM + gprMax FDTD"
CMD ["bash"]
