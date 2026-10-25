#!/usr/bin/env bash
# Derived from the pinned discord_arch_electron launcher; see packaging/upstream.
set -euo pipefail
name=@PKGNAME@
root=/usr/lib/@PKGNAME@
export DPE_APP_ROOT="$root"
config_root=${XDG_CONFIG_HOME:-$HOME/.config}
export DISCORD_PORTAL_APP_ID=${DISCORD_PORTAL_APP_ID:-@DESKTOP_ID@}
export CHROME_DESKTOP=${CHROME_DESKTOP:-@DESKTOP_ID@.desktop}
flags_file="$config_root/$name-flags.conf"
flags=(--class=@DESKTOP_ID@)
declare -A feature_state=() feature_value=()
add_flag() {
  local line=$1 feature key state
  case "$line" in
    --enable-features=* | --disable-features=*)
      state=${line%%=*}
      local -a features
      IFS=, read -r -a features <<<"${line#*=}"
      for feature in "${features[@]}"; do
        [[ -n "$feature" ]] || continue
        key=${feature%%[:<]*}
        feature_state["$key"]=$state
        feature_value["$key"]=$feature
      done
      ;;
    *) flags+=("$line") ;;
  esac
}
read_flags() {
  [[ -r "$1" ]] || return 0
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    if [[ -n "$line" && ! "$line" =~ ^[[:space:]]*# ]]; then
      add_flag "$line"
    fi
  done <"$1"
}
if [[ ${DPE_DEFAULT_FLAGS:-1} != 0 ]]; then
  if [[ -n ${WAYLAND_DISPLAY:-} ]]; then
    add_flag --ozone-platform=wayland
  else
    add_flag --ozone-platform=x11
  fi
  add_flag --enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL
fi
# Legacy file remains supported. The channel-level file and CLI take precedence.
read_flags "$flags_file"
read_flags "$config_root/${name/-prerelease-patched-electron-bin/-patched-electron}.conf"
for arg in "$@"; do add_flag "$arg"; done
enabled=() disabled=()
for key in "${!feature_state[@]}"; do
  if [[ ${feature_state[$key]} == --enable-features ]]; then
    enabled+=("${feature_value[$key]}")
  else
    disabled+=("${feature_value[$key]}")
  fi
done
if ((${#enabled[@]})); then flags+=("--enable-features=$(
  IFS=,
  echo "${enabled[*]}"
)"); fi
if ((${#disabled[@]})); then flags+=("--disable-features=$(
  IFS=,
  echo "${disabled[*]}"
)"); fi
# Use the standard channel profile unless a test or user explicitly overrides it.
export DISCORD_USER_DATA_DIR=${DISCORD_USER_DATA_DIR:-"$config_root/@PROFILE_NAME@"}
DISCORD_USER_DATA_DIR=$(readlink -m -- "$DISCORD_USER_DATA_DIR")
export DPE_RUNTIME_EXECUTABLE="$root/runtime/electron"
if [[ -f "$root/client-distribution.json" ]]; then
  export PYTHONDONTWRITEBYTECODE=1
  export PYTHONNOUSERSITE=1
  export PYTHONPATH="$root/python"
  unset PYTHONHOME ELECTRON_RUN_AS_NODE
  export DPE_CLIENT_ROOT="$DISCORD_USER_DATA_DIR/dpe-updater"
  channel=${name#discord-}
  export DPE_LAUNCHER
  DPE_LAUNCHER=$(readlink -f -- "${BASH_SOURCE[0]}")
  # Show progress before starting Python or downloading anything. The UI commits
  # complete updates, then this launcher starts the selected runtime with its
  # camera preload and the user's existing mod loader.
  DPE_UPDATER_UI=1 "$root/runtime/electron" "${flags[@]}"
  DPE_RUNTIME_EXECUTABLE=$(python3 "$root/integration/updater/runtime.py" "$root" "$DISCORD_USER_DATA_DIR" --offline)
  DPE_CLIENT_HOST=$(python3 "$root/integration/updater/client.py" "$DPE_CLIENT_ROOT" "${channel%%-*}" --offline)
  export DPE_CLIENT_HOST
fi
if [[ ${DPE_CAMERA_FAST_PROBE:-1} != 0 ]]; then
  if [[ ! -f "$root/client-distribution.json" ]]; then
    read -r DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END <"$root/camera-probe.conf"
    export DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END
    export DPE_CAMERA_VOICE_MODULE="$root/modules/discord_voice/discord_voice.node"
  else
    read -r DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END <"$DPE_CLIENT_HOST/modules/discord_voice/camera-probe.conf"
    export DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END
    DPE_CAMERA_VOICE_MODULE=$(readlink -f -- "$DPE_CLIENT_HOST/modules/discord_voice/discord_voice.node")
    export DPE_CAMERA_VOICE_MODULE
  fi
  export LD_PRELOAD="$root/libdpe-camera-probe.so${LD_PRELOAD:+:$LD_PRELOAD}"
fi
# Equicord's official installer patches resources/app.asar in place.
# Keep its settings in this channel's profile; package updates own the bootstrap.
export EQUICORD_USER_DATA_DIR=${EQUICORD_USER_DATA_DIR:-"$DISCORD_USER_DATA_DIR/equicord"}
export DISABLE_UPDATER_AUTO_PATCHING=1
# Ubuntu's installed profile grants the user-namespace sandbox to this app.
# Enter that same profile when its verified runtime lives in the user cache.
if [[ "$DPE_RUNTIME_EXECUTABLE" != "$root/runtime/electron" && -f "/etc/apparmor.d/$name" ]] && command -v aa-exec >/dev/null 2>&1; then
  if aa-exec -p "$name" -- true 2>/dev/null; then
    exec aa-exec -p "$name" -- "$DPE_RUNTIME_EXECUTABLE" "${flags[@]}"
  fi
fi
exec "$DPE_RUNTIME_EXECUTABLE" "${flags[@]}"
