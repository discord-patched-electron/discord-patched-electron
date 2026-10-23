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
if [[ ${DPE_CAMERA_FAST_PROBE:-1} != 0 ]]; then
    read -r DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END < "$root/camera-probe.conf"
    export DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END
    export DPE_CAMERA_VOICE_MODULE="$root/modules/discord_voice/discord_voice.node"
    export LD_PRELOAD="$root/libdpe-camera-probe.so${LD_PRELOAD:+:$LD_PRELOAD}"
fi
exec "$root/runtime/electron" "$root/resources/app.asar" "${flags[@]}" "$@"
