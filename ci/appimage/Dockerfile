FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates file patchelf gpg gpgv brotli zenity \
    pipewire-bin pulseaudio-utils x11-utils dbus-bin \
    libgtk-3-0 libnss3 libnspr4 libasound2 libatomic1 libcups2 libxss1 libxtst6 \
    libpulse0 libsecret-1-0 libnotify4 libgbm1 libdrm2 \
    && rm -rf /var/lib/apt/lists/*
