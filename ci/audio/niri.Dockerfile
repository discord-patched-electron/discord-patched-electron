FROM discord-electron-audio-fixture
USER root
RUN pacman -Syu --noconfirm --needed git clang lld libxkbcommon libdisplay-info libinput libpipewire pango cairo seatd \
    && pacman -Scc --noconfirm
USER builder
RUN cd /tmp/dpe-toolchain && rustup default "$(rustup show active-toolchain | cut -d ' ' -f 1)"
WORKDIR /build
ENV CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_DEV_INCREMENTAL=false CARGO_BUILD_JOBS=2
