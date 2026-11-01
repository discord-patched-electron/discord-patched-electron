#!/bin/sh
set -eu
root=$(dirname -- "$(readlink -f -- "$0")")
export DPE_TOOLS_DIR="$root/usr/bin"
export PATH="$DPE_TOOLS_DIR:$PATH"
export LD_LIBRARY_PATH="$root/usr/desktop/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export SPA_PLUGIN_DIR="$root/usr/lib/spa-0.2"
export PIPEWIRE_MODULE_DIR="$root/usr/lib/pipewire-0.3"
export PIPEWIRE_CONFIG_DIR="$root/usr/share/pipewire"
export XDG_DATA_DIRS="$root/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
# Prefer the user's/host's trust store (including enterprise CAs), with a bundled
# public trust store only when none is available. Never replace an explicit one.
if [ -z "${SSL_CERT_FILE:-}" ]; then
  for certificates in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt "$root/usr/share/dpe-dependencies/certs/ca-certificates.crt"; do
    if [ -r "$certificates" ]; then
      export SSL_CERT_FILE="$certificates"
      break
    fi
  done
fi
exec "$root/usr/lib/dpe/launch.sh" "$@"
