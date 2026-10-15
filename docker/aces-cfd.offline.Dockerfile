# aces-cfd built ON THE RIG, which has no PyPI: the wheels are copied in beside this file.
#
#   (workstation)  pip download CoolProp==8.0.0 --no-deps --only-binary=:all: \
#                    --platform manylinux2014_x86_64 --python-version 3.12 -d wheels
#                  pip wheel . --no-deps -w wheels
#   (rig)          docker build -f aces-cfd.offline.Dockerfile -t aces-cfd:0.1 .
FROM gendesign:0.3
COPY wheels/ /tmp/wheels/
RUN python3 -m pip install --no-cache-dir --break-system-packages --no-index --no-deps \
        /tmp/wheels/coolprop-*.whl /tmp/wheels/aces-*.whl \
 && rm -rf /tmp/wheels \
 && python3 -c "import CoolProp, aces.cfd.simlab_adapter; print('aces-cfd ok')"
LABEL org.opencontainers.image.title="aces-cfd" \
      org.opencontainers.image.vendor="Carbon Project Australia" \
      org.opencontainers.image.description="ACES periodic TPMS calibration CFD (FP64 lattice Boltzmann) for simlab"
