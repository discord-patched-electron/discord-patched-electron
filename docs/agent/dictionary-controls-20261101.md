# Dictionary filters and tray reset state

Tray row presets matching the main preset no longer leave a redundant override
or an enabled Reset button. The spelling Filter menu uses the shared menu surface
with right-aligned Discord-style square checkboxes for Only enabled and Downloaded.
Both filters combine with language search and are disabled in system-language mode.

Downloaded dictionaries have an Uninstall action after the shared inline bullet.
Removal saves the manual selection first, disables any aliases sharing that file,
removes only matching versioned BDic files, and refreshes cached status. Late
initialization events cannot restore a removed dictionary's status. Filesystem
errors preserve readable settings state; personal learned words are untouched.

A reduced-motion-aware progress icon accompanies Downloading and changes to the
Downloaded checkmark. Electron's dictionary events expose start/success/failure,
not bytes transferred; no fabricated percentage or replacement downloader is used.
See [Electron session events](https://www.electronjs.org/docs/latest/api/session#event-spellcheck-dictionary-download-begin).

Validation: lint and 774 unit tests pass. The native settings harness passed all
42 groups under X11, including actual cached-file deletion, both filters,
download-state transitions, preset reset, disabled system mode, narrow layouts,
RTL, and zoom. The first Wayland run stopped at an unrelated onboarding hover
focus assertion; Wayland-specific interaction is not verified for this change.
Menu screenshots were inspected against the supplied checkbox reference. No
remaining actionable UI-polish findings in the inspected states; approve those
states. The test build reuses the existing native runtime without recompiling it.

Test command: `/mnt/storage/dpe-dictionary-controls-20261101/test.sh`.
