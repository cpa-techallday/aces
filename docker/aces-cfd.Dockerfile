# ACES calibration CFD for simlab (solver id: aces-tpms-periodic).
#
# Built on the workstation and shipped to the rig, like every simlab image (the rig has no PyPI).
# Base: simlab's gendesign image, which already carries CuPy, numpy, scipy, scikit-image, pydantic,
# PyYAML and optuna.  ACES adds CoolProp and itself, without touching the base's pinned packages.
#
#   python -m pip wheel . --no-deps -w dist            (in the ACES checkout)
#   docker build -f docker/aces-cfd.Dockerfile -t aces-cfd:0.1 .
#   docker run --rm --gpus all --entrypoint python3 aces-cfd:0.1 -m aces.cfd.selftest
FROM gendesign:0.3
COPY dist/aces-*.whl /tmp/wheels/
RUN python3 -m pip install --no-cache-dir --break-system-packages "CoolProp==8.0.0" \
 && python3 -m pip install --no-cache-dir --break-system-packages --no-deps /tmp/wheels/aces-*.whl \
 && rm -rf /tmp/wheels \
 && python3 -c "import CoolProp, aces.cfd.simlab_adapter; print('aces-cfd ok')"
LABEL org.opencontainers.image.title="aces-cfd" \
      org.opencontainers.image.vendor="Carbon Project Australia" \
      org.opencontainers.image.description="ACES periodic TPMS calibration CFD (FP64 lattice Boltzmann) for simlab"
