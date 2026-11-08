FROM archlinux:base@sha256:52dfbd5afb23a220d5ee92dafcb4c788d96c026ed04179890c23adef4c897fa0

# Packaging-only environment. Electron itself is compiled on Ubuntu using its
# pinned Chromium toolchain; Arch supplies makepkg and native-helper build tools.
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
