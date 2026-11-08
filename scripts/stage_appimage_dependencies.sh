#!/bin/bash
# Run only inside ci/appimage's pinned Ubuntu image, never against host libraries.
set -euo pipefail
tools=$1
output=$2
mkdir -p "$output/usr/bin" "$output/usr/lib" "$output/usr/share/dpe-dependencies/licenses"
tar -xzf "$tools/python.tar.gz" -C "$output/usr"
ln -s ../python/bin/python3 "$output/usr/bin/python3"
export APPIMAGE_EXTRACT_AND_RUN=1
export NO_STRIP=1
arguments=()
# Only bootstrap tools are bundled. Audio, desktop integration, GUI libraries,
# graphics drivers and their plugins/configuration belong to the host.
for executable in gpg gpgv brotli; do
  arguments+=(--executable "/usr/bin/$executable")
done
# linuxdeploy patches helper-relative ELF paths; never export their library
# closure to Electron, host shells or desktop/audio subprocesses.
"$tools/linuxdeploy" --appdir "$output" "${arguments[@]}"
mkdir -p "$output/usr/share/dpe-dependencies/certs"
cp /etc/ssl/certs/ca-certificates.crt "$output/usr/share/dpe-dependencies/certs/"
# Include distro copyright/source notices, including transitive dependencies.
for notice in /usr/share/doc/*/copyright; do
  cp -L "$notice" "$output/usr/share/dpe-dependencies/licenses/$(basename "$(dirname "$notice")").copyright"
done
dpkg-query -W -f='${binary:Package}\t${Version}\n' >"$output/usr/share/dpe-dependencies/packages.tsv"
