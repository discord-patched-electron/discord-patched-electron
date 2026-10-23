FROM discord-electron-audio-fixture
USER root
RUN pacman -Syu --noconfirm --needed rust git clang lld libxkbcommon libdisplay-info libinput libpipewire pango cairo seatd \
    && pacman -Scc --noconfirm
USER builder
WORKDIR /build
ENV CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_DEV_INCREMENTAL=false CARGO_BUILD_JOBS=2
