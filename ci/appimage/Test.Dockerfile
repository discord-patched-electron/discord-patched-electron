FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
# Only the platform ABI/graphics libraries deliberately excluded by linuxdeploy.
# No Python, GnuPG, Brotli, GTK, NSS, PulseAudio or PipeWire client packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 libx11-6 libx11-xcb1 libxcb1 libwayland-client0 libwayland-cursor0 \
    libwayland-egl1 libgl1 libegl1 libgbm1 libdrm2 \
    && rm -rf /var/lib/apt/lists/*
