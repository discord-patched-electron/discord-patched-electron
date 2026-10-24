#!/bin/sh
# Package upgrades replace app.asar. Keep the official installers' restore copy
# current so reinstalling or uninstalling a mod cannot restore an older app.
set -eu
root=${1:?App directory required}
if [ -f "$root/resources/_app.asar" ] && [ ! -L "$root/resources/_app.asar" ]; then
  if (cd "$root" && sed -n '\|  resources/app.asar$|p' SHA256SUMS | sha256sum --check --status); then
    backup=$(mktemp "$root/resources/.app-backup.XXXXXX")
    trap 'rm -f "$backup"' EXIT
    cp "$root/resources/app.asar" "$backup"
    chmod 644 "$backup"
    mv -f "$backup" "$root/resources/_app.asar"
  fi
fi
