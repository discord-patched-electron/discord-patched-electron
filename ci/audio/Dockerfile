FROM discord-electron-arch-packaging
USER root
RUN pacman -Syu --noconfirm --needed python-numpy pipewire pipewire-pulse wireplumber xorg-server-xvfb xorg-xauth xorg-xprop \
    niri python-dbus python-gobject zenity fftw libsoxr orc speexdsp tdb webrtc-audio-processing-1 \
    && pacman -Scc --noconfirm
# Extract a second server for the isolated compatibility fixture. Do not install
# it over pipewire-pulse or change the image's normal audio provider.
RUN mkdir -p /opt/dpe-pulse /tmp/dpe-pulse-packages \
    && pacman -Sw --noconfirm --cachedir /tmp/dpe-pulse-packages pulseaudio \
    && bsdtar -xf /tmp/dpe-pulse-packages/pulseaudio-*.pkg.tar.zst -C /opt/dpe-pulse \
    && rm -rf /tmp/dpe-pulse-packages
USER builder
WORKDIR /audio
