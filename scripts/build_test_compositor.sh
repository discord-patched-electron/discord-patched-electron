#!/usr/bin/env bash
set -euo pipefail
# Test-only compositor pin: includes PipeWire shared-memory capture without a GPU.
revision=e19df12cf81ffe805609fee497da1a097066ad2f
if [[ -x /build/niri && -f /build/niri-source && $(cat /build/niri-source) == "$revision" ]]; then
  if /build/niri --version >/dev/null; then exit 0; fi
fi
mkdir -p /build/source
if [[ ! -d /build/source/.git ]]; then
  git -C /build/source init
  git -C /build/source remote add origin https://github.com/niri-wm/niri.git
fi
git -C /build/source fetch --depth=1 origin "$revision"
git -C /build/source checkout --detach "$revision"
test "$(git -C /build/source rev-parse HEAD)" = "$revision"
cd /build/source
cargo build --locked --no-default-features --features dbus,xdp-gnome-screencast
install -m755 target/debug/niri /build/niri
strip /build/niri
printf '%s\n' "$revision" > /build/niri-source
cd /build
sha256sum niri > niri.sha256
