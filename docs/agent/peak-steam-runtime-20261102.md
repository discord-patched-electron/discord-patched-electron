# PEAK / Proton game-capture socket visibility

A real Steam Linux Runtime sniper check on this machine found the host's
`$XDG_RUNTIME_DIR/dpe-gamecapture` absent inside the container. The hook bundle
under `/mnt/storage` was visible. All graphics APIs using the bundled hook would
still fail discovery because its private pathname socket could not be reached.
This differs from upstream OBS's abstract socket.

The launcher now creates and validates its owned mode-0700 socket directory
before a container starts, then appends that directory and the hook bundle to
`PRESSURE_VESSEL_FILESYSTEMS_RO`. Existing mounts are preserved. Mounting the
parent directory keeps late broker startup/recreated sockets visible. Read-only
mounting permits socket connections without exposing the whole runtime directory
or granting filesystem writes. Existing stock-hook launch behavior is unchanged.

[Valve's container directory-sharing documentation](https://github.com/ValveSoftware/steam-runtime/blob/master/doc/steamlinuxruntime-known-issues.md#sharing-directories-with-the-container)
provides the supported mount mechanism. [Upstream OBS usage](https://github.com/nowrep/obs-vkcapture#usage)
also distinguishes native Steam from Flatpak Steam; this fix does not claim to
bypass Flatpak's outer sandbox.

Validation: a bounded real sniper container connected to a synthetic private
SEQPACKET socket through the read-only directory mount and exchanged a fixed
non-image test packet (`out/peak-container/check.py`). No game, GPU capture,
Discord account, or live call was used. Launcher regression tests cover preserving
mount options and private-directory creation/mode/symlink rejection. PEAK itself
and its three graphics APIs remain unverified, so this fixes a demonstrated
container compatibility defect rather than proving full game compatibility.
Restart the game through the updated wrapper for the environment change to apply.

## Startup hang: preload ordering

The subsequent no-window report had a different cause. Read-only process inspection
found Steam’s `steam-launch-wrapper` spinning before Proton or `PEAK.exe` existed,
with DPE’s GL hook before Steam’s overlay in `LD_PRELOAD`. The existing process was
not stopped, debug-attached, or modified.

A bounded reproduction using `/bin/true` and the real Steam launch wrapper confirmed:
preloading the capture library first timed out after three seconds, whereas keeping
Steam’s overlay first exited successfully. Each library alone also exited normally.
This isolates the startup hang to loader/interposer ordering, without claiming a
specific internal recursion stack. The launcher now appends its GL hook after the
existing preload entries. Vulkan-only mode omits that hook entirely.

`out/peak-preload/check.py` and `result.json` retain the before/after reproduction.
`current.py` and `current-result.json` execute the current launcher environment through
`steam-launch-wrapper -- /bin/true` with both of Steam’s actual overlay paths. Default
DPE capture and `OBS_VKCAPTURE=1` both exit successfully; overlay order is preserved.
Only these short synthetic children were timed out. PEAK itself was not relaunched,
and no existing game or Steam process was stopped. A previously hung launch will
need to be stopped by the user and started again to receive the corrected environment.
