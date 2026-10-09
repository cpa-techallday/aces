# ACES package builder: Arch base-devel with an unprivileged builder user, makepkg tooling and the local [aces] repo
# wired in as a pacman source so packages that depend on earlier ACES packages resolve during a build.
FROM archlinux:base-devel

RUN pacman -Syu --noconfirm --needed \
      git namcap pacman-contrib sudo \
      python python-build python-installer python-wheel python-setuptools \
      curl ca-certificates \
    && pacman -Scc --noconfirm

RUN useradd -m -s /bin/bash builder \
    && echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder \
    && chmod 0440 /etc/sudoers.d/builder \
    && mkdir -p /repo/x86_64 /srcdest /build \
    && chown -R builder /repo /srcdest /build

# the local repo; the build script creates an empty database before the first pacman -Sy
RUN printf '\n[aces]\nSigLevel = Optional TrustAll\nServer = file:///repo/x86_64\n' >> /etc/pacman.conf

USER builder
WORKDIR /build
