#!/usr/bin/env bash
# Derived from the pinned discord_arch_electron launcher; see packaging/upstream.
set -euo pipefail
name=@PKGNAME@
root=/usr/lib/@PKGNAME@
export DPE_APP_ROOT="$root"
# An app.relaunch inherits our codec environment; recompute it from this launch's
# flags so disabling the codec or entering recovery removes our previous preload.
# shellcheck source=integration/launcher/codec-preload.sh
source "$root/integration/launcher/codec-preload.sh"
dpe_restore_codec_environment
config_root=${XDG_CONFIG_HOME:-$HOME/.config}
export DISCORD_PORTAL_APP_ID=${DISCORD_PORTAL_APP_ID:-@DESKTOP_ID@}
export CHROME_DESKTOP=${CHROME_DESKTOP:-@DESKTOP_ID@.desktop}
# Resolve the profile before reading its saved launch preferences.
export DPE_PROFILE_DIR=${DPE_PROFILE_DIR:-"${DISCORD_USER_DATA_DIR:-$config_root}/@PROFILE_NAME@"}
DPE_PROFILE_DIR=$(readlink -m -- "$DPE_PROFILE_DIR")
export DPE_PACKAGE_NAME="$name"
export DPE_DESKTOP_ID=@DESKTOP_ID@
export DPE_FLAGS_MANAGED=1
export DPE_LAUNCHER
DPE_LAUNCHER=${DPE_LAUNCHER:-$(readlink -f -- "${BASH_SOURCE[0]}")}
unset DPE_LAUNCH_ARGS
# This escape hatch runs before reading any flag file, preset JSON or resolver.
# Drop every supplied argument as well: a bad CLI override must not defeat it.
for arg in "$@"; do
  if [[ $arg == --ignore-flags ]]; then export DPE_IGNORE_FLAGS=1; fi
done
flags=()
if [[ ${DPE_IGNORE_FLAGS:-0} == 1 ]]; then
  export DPE_LAUNCH_ARGS='["--ignore-flags"]'
else
  flags_result=$(mktemp)
  trap 'rm -f -- "$flags_result"' EXIT
  # Standard-library-only resolver; never execute flag contents as shell code.
  if python3 -I "$root/integration/launcher/flags.py" emit "$DPE_PROFILE_DIR" "$name" @DESKTOP_ID@ -- "$@" >"$flags_result"; then
    mapfile -d '' -t resolved <"$flags_result"
    export DPE_LAUNCH_ARGS=${resolved[0]}
    flags=("${resolved[@]:1}")
  else
    exit 1
  fi
  rm -f -- "$flags_result"
  trap - EXIT
fi
export DPE_RUNTIME_EXECUTABLE="$root/runtime/electron"
# Apply Ubuntu's installed sandbox rule to both the startup UI and the client.
runtime_command=()
if [[ -f "/etc/apparmor.d/$name" ]] && command -v aa-exec >/dev/null 2>&1; then
  if aa-exec -p "$name" -- true 2>/dev/null; then
    runtime_command=(aa-exec -p "$name" --)
  fi
fi
if [[ -f "$root/client-distribution.json" ]]; then
  export PYTHONDONTWRITEBYTECODE=1
  export PYTHONNOUSERSITE=1
  export PYTHONPATH="$root/python"
  unset PYTHONHOME ELECTRON_RUN_AS_NODE
  export DPE_CLIENT_ROOT="$DPE_PROFILE_DIR/dpe-updater"
  # Packages contain only the updater. Reuse a verified runtime or prepare it
  # with native progress before it can display the normal Electron startup UI.
  DPE_RUNTIME_EXECUTABLE=$(python3 "$root/integration/updater/bootstrap.py" "$root" "$DPE_PROFILE_DIR")
  launch_result=$(mktemp)
  trap 'rm -f -- "$launch_result"' EXIT
  if DPE_LAUNCH_RESULT="$launch_result" DPE_UPDATER_UI=1 "${runtime_command[@]}" "$DPE_RUNTIME_EXECUTABLE" --disable-setuid-sandbox "${flags[@]}"; then
    :
  else
    status=$?
    # The existing instance already received the arguments. Do not activate an
    # update underneath it or report a failed desktop launch.
    if [[ $status == 2 ]]; then exit 0; fi
    exit "$status"
  fi
  incoming=$(python3 "$root/integration/updater/check.py" --application-path "$launch_result")
  if [[ -n "$incoming" ]]; then
    rm -f -- "$launch_result"
    trap - EXIT
    exec "$DPE_LAUNCHER" "$@"
  fi
  installed=$(python3 "$root/integration/updater/check.py" --launch-paths "$launch_result")
  mapfile -t installed_paths <<<"$installed"
  export DPE_RUNTIME_SOURCE=${installed_paths[0]}
  export DPE_CLIENT_HOST=${installed_paths[1]}
  export DPE_RUNTIME_EXECUTABLE=${installed_paths[2]}
  DPE_VOICE_ROOT=${installed_paths[3]}
  rm -f -- "$launch_result"
  trap - EXIT
fi
dpe_preload_openh264 "${flags[@]}"
if [[ ${DPE_CAMERA_FAST_PROBE:-1} != 0 ]]; then
  if [[ ! -f "$root/client-distribution.json" ]]; then
    read -r DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END <"$root/camera-probe.conf"
    export DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END
    export DPE_CAMERA_VOICE_MODULE="$root/modules/discord_voice/discord_voice.node"
  else
    read -r DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END <"$DPE_VOICE_ROOT/camera-probe.conf"
    export DPE_CAMERA_PROBE_START DPE_CAMERA_PROBE_END
    DPE_CAMERA_VOICE_MODULE=$(readlink -f -- "$DPE_VOICE_ROOT/discord_voice.node")
    export DPE_CAMERA_VOICE_MODULE
  fi
  if [[ -f "$root/client-distribution.json" ]]; then
    # LD_LIBRARY_PATH accepts spaces; LD_PRELOAD splits them. Keep this search
    # directory limited to our helper so child programs keep their system libs.
    preload_dir=$(dirname -- "$(readlink -f -- "$DPE_RUNTIME_EXECUTABLE")")/preload
    export LD_LIBRARY_PATH="$preload_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_PRELOAD="libdpe-camera-probe.so${LD_PRELOAD:+:$LD_PRELOAD}"
  else
    export LD_PRELOAD="$root/libdpe-camera-probe.so${LD_PRELOAD:+:$LD_PRELOAD}"
  fi
fi
# Official mod loaders live in the profile's app-version/resources directory.
# The updater preserves the loader and refreshes its underlying bootstrap.
export EQUICORD_USER_DATA_DIR=${EQUICORD_USER_DATA_DIR:-"$DPE_PROFILE_DIR/equicord"}
# We publish the host's loader; each mod's own payload updater stays enabled.
export DISABLE_UPDATER_AUTO_PATCHING=1
# Ubuntu's installed profile grants the user-namespace sandbox to this app.
# Enter that same profile when its verified runtime lives in the user cache.
if [[ -n ${DPE_APPLICATION_STARTED:-} ]]; then
  printf 'started\n' >"$DPE_APPLICATION_STARTED"
fi
exec "${runtime_command[@]}" "$DPE_RUNTIME_EXECUTABLE" "${flags[@]}"
