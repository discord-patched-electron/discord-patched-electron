#!/usr/bin/env bash
# Derived from the pinned discord_arch_electron launcher; see packaging/upstream.
set -euo pipefail
name=@PKGNAME@
root=/usr/lib/@PKGNAME@
config_root=${XDG_CONFIG_HOME:-$HOME/.config}
flags_file="$config_root/$name-flags.conf"
flags=()
if [[ -r "$flags_file" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ -n "$line" && ! "$line" =~ ^[[:space:]]*# ]]; then
            flags+=("$line")
        fi
    done < "$flags_file"
fi
# The existing desktop bootstrap honors this variable. Never use the stock profile.
export DISCORD_USER_DATA_DIR=${DISCORD_USER_DATA_DIR:-"$config_root/$name"}
exec "$root/runtime/electron" "$root/resources/app.asar" "${flags[@]}" "$@"
