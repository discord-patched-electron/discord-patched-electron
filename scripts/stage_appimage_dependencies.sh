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
for executable in gpg gpgv brotli zenity pactl pw-dump pw-cli pw-cat pw-loopback xprop dbus-send; do
  arguments+=(--executable "/usr/bin/$executable")
done
# Explicit dependencies cover Electron dlopen paths; graphics drivers, libc and
# display-server libraries remain host-owned per linuxdeploy's exclusion policy.
for library in libgtk-3.so.0 libnss3.so libnssutil3.so libsmime3.so libssl3.so libsoftokn3.so libfreebl3.so libfreeblpriv3.so libnssckbi.so libnspr4.so libasound.so.2 libatomic.so.1 libcups.so.2 libXss.so.1 libXtst.so.6 libpulse.so.0 libsecret-1.so.0 libnotify.so.4 libpipewire-0.3.so.0 libcap.so.2 libgmp.so.10 libcom_err.so.2 libexpat.so.1 libfontconfig.so.1 libfreetype.so.6 libfribidi.so.0 libgpg-error.so.0 libharfbuzz.so.0 libuuid.so.1 libz.so.1; do
  path="/usr/lib/x86_64-linux-gnu/$library"
  if [ ! -f "$path" ]; then path="/usr/lib/x86_64-linux-gnu/nss/$library"; fi
  arguments+=(--library "$path")
done
"$tools/linuxdeploy" --appdir "$output" "${arguments[@]}"
# PipeWire's client tools load modules dynamically and need their own matching
# client plugins, not the host's daemon or its service configuration.
for directory in pipewire-0.3 spa-0.2; do
  cp -a "/usr/lib/x86_64-linux-gnu/$directory" "$output/usr/lib/"
done
cp -a /usr/share/pipewire "$output/usr/share/"
cp -a /usr/share/glib-2.0 "$output/usr/share/"
cp -a /usr/share/zenity "$output/usr/share/"
mkdir -p "$output/usr/share/dpe-dependencies/certs"
cp /etc/ssl/certs/ca-certificates.crt "$output/usr/share/dpe-dependencies/certs/"
# Include distro copyright/source notices, including transitive dependencies.
for notice in /usr/share/doc/*/copyright; do
  cp -L "$notice" "$output/usr/share/dpe-dependencies/licenses/$(basename "$(dirname "$notice")").copyright"
done
dpkg-query -W -f='${binary:Package}\t${Version}\n' >"$output/usr/share/dpe-dependencies/packages.tsv"
# Scan dynamic plugins too, without changing the standalone Python distribution.
"$tools/linuxdeploy" --appdir "$output"

# Electron has no bundled ELF to patch: prepare a separate GUI library closure
# for its downloaded runtime. Helper-only libraries (notably readline) must not
# enter the inherited search path used by the host shell or graphics stack.
desktop_arguments=()
for library in libgtk-3.so.0 libnss3.so libnssutil3.so libsmime3.so libssl3.so libsoftokn3.so libfreebl3.so libfreeblpriv3.so libnssckbi.so libnspr4.so libasound.so.2 libatomic.so.1 libcups.so.2 libXss.so.1 libXtst.so.6 libpulse.so.0 libsecret-1.so.0 libnotify.so.4 libexpat.so.1 libfontconfig.so.1 libfreetype.so.6 libfribidi.so.0 libgpg-error.so.0 libharfbuzz.so.0 libuuid.so.1 libz.so.1 libgmp.so.10 libcom_err.so.2; do
  path="/usr/lib/x86_64-linux-gnu/$library"
  if [ ! -f "$path" ]; then path="/usr/lib/x86_64-linux-gnu/nss/$library"; fi
  desktop_arguments+=(--library "$path")
done
"$tools/linuxdeploy" --appdir "$output/usr/desktop" "${desktop_arguments[@]}"
