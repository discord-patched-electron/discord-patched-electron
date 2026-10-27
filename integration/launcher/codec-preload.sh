#!/usr/bin/env bash
# The imported FFmpeg OpenH264 decoder dlopens its library after the renderer
# sandbox starts. Map the requested library before exec so the renderer can
# reuse it without opening a file outside its sandbox.

dpe_restore_codec_environment() {
  if [[ ${DPE_CODEC_PRELOAD_ACTIVE:-0} == 1 ]]; then
    export LD_PRELOAD=${DPE_CODEC_ORIGINAL_LD_PRELOAD-}
    export LD_LIBRARY_PATH=${DPE_CODEC_ORIGINAL_LD_LIBRARY_PATH-}
    unset DPE_CODEC_PRELOAD_ACTIVE DPE_CODEC_ORIGINAL_LD_PRELOAD DPE_CODEC_ORIGINAL_LD_LIBRARY_PATH
  fi
}

dpe_preload_openh264() {
  [[ ${DPE_IGNORE_FLAGS:-0} != 1 ]] || return 0
  local arg enabled=0 library='' directory filename
  for arg in "$@"; do
    case "$arg" in
      --ignore-flags) return 0 ;;
      --enable-libopenh264 | --enable-libopenh264=*) enabled=1 ;;
      --openh264-library-path=*) library=${arg#*=} ;;
    esac
  done
  [[ $enabled == 1 && -n $library ]] || return 0
  if [[ ! -f $library || ! -r $library ]]; then
    printf 'Software H.264 decoding needs a readable OpenH264 library: %s. Restart after the codec is available.\n' "$library" >&2
    return 0
  fi
  # Keep the lexical path used by FFmpeg: the dynamic loader remembers this
  # name. Resolving a symlink here could make a sandboxed dlopen miss that name.
  directory=$(dirname -- "$library")
  filename=$(basename -- "$library")
  if [[ $directory != /* || $directory == *[:\;\$]* || $filename == *[[:space:]:\$]* ]]; then
    printf 'Cannot preload OpenH264 from this loader path: %s. Use an absolute directory without :, ; or $, and a filename without spaces, : or $.\n' "$library" >&2
    return 0
  fi
  export DPE_CODEC_ORIGINAL_LD_PRELOAD=${LD_PRELOAD-}
  export DPE_CODEC_ORIGINAL_LD_LIBRARY_PATH=${LD_LIBRARY_PATH-}
  export DPE_CODEC_PRELOAD_ACTIVE=1
  # LD_PRELOAD cannot represent spaces; directory search can. The normal
  # OpenAsar cache contains only its codec, not general runtime libraries.
  export LD_LIBRARY_PATH="$directory${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export LD_PRELOAD="$filename${LD_PRELOAD:+:$LD_PRELOAD}"
}
