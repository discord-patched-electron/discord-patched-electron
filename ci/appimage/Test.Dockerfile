FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
# No added packages: the bootstrap must work offline without host Python,
# GnuPG, Brotli or a desktop/audio stack. GUI launches require host desktop libs.
