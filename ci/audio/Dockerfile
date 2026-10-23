FROM discord-electron-arch-packaging
USER root
RUN pacman -Syu --noconfirm --needed python-numpy pipewire pipewire-pulse wireplumber xorg-server-xvfb xorg-xauth xorg-xprop \
    && pacman -Scc --noconfirm
USER builder
WORKDIR /audio
