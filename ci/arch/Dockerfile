FROM archlinux:base@sha256:52dfbd5afb23a220d5ee92dafcb4c788d96c026ed04179890c23adef4c897fa0

# Packaging-only environment. Electron itself is compiled on Ubuntu using its
# pinned Chromium toolchain; Arch supplies makepkg and the AUR dependencies.
RUN pacman -Syu --noconfirm --needed base-devel asar brotli unzip \
    python-capstone python-pyelftools desktop-file-utils \
    alsa-lib gtk3 libcups libxss libxtst nss libdrm mesa libpulse pipewire zenity \
    && pacman -Scc --noconfirm \
    && useradd --uid 1000 --create-home builder
USER builder
WORKDIR /packages
