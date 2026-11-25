FROM archlinux:base@sha256:52dfbd5afb23a220d5ee92dafcb4c788d96c026ed04179890c23adef4c897fa0 AS arch-base

# Packaging-only environment. Electron itself is compiled on Ubuntu using its
# pinned Chromium toolchain; Arch supplies makepkg and native-helper build tools.
ARG DEPENDENCY_WEEK
RUN pacman -Syu --noconfirm --needed base-devel asar brotli unzip \
    python-capstone python-pyelftools desktop-file-utils \
    git rustup clang libva libcap cmake ninja nasm wayland libglvnd vulkan-headers vulkan-icd-loader \
    alsa-lib gtk3 libcups libxss libxtst nss libdrm mesa libpulse pipewire pipewire-audio zenity \
    && pacman -Scc --noconfirm \
    && useradd --uid 1000 --create-home builder
USER builder
COPY rust-toolchain.toml /tmp/dpe-toolchain/rust-toolchain.toml
RUN cd /tmp/dpe-toolchain && rustup show
WORKDIR /packages

FROM arch-base AS audio-fixture
USER root
RUN pacman -Syu --noconfirm --needed python-numpy pipewire pipewire-pulse wireplumber xorg-server-xvfb xorg-xauth xorg-xprop \
    niri python-dbus python-gobject zenity wtype fftw libsoxr orc speexdsp tdb webrtc-audio-processing-1 \
    && pacman -Scc --noconfirm
# Extract a second server for the isolated compatibility fixture. Do not install
# it over pipewire-pulse or change the image's normal audio provider.
RUN mkdir -p /opt/dpe-pulse /tmp/dpe-pulse-packages \
    && pacman -Sw --noconfirm --cachedir /tmp/dpe-pulse-packages pulseaudio \
    && bsdtar -xf /tmp/dpe-pulse-packages/pulseaudio-*.pkg.tar.zst -C /opt/dpe-pulse \
    && rm -rf /tmp/dpe-pulse-packages
USER builder
WORKDIR /audio

FROM arch-base AS packaging
