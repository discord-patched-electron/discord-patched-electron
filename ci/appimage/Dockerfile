FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates file patchelf gpg gpgv brotli \
    && rm -rf /var/lib/apt/lists/*
