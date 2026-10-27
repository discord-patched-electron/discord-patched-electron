# External links and isolated test profiles

On 2026-10-27, both DPE settings links and ordinary Discord links appeared not
to open in Zen. The cause was the test command's empty `XDG_CONFIG_HOME`, not a
reproduced Zen defect. [Recorded evidence](../evidence/external-links.json).

## Trace and correction

The user's usual `mimeapps.list` selects `zen.desktop` for HTTP and HTTPS.
Overriding `XDG_CONFIG_HOME` hides that file. The desktop then chose
`dms-open.desktop`, whose command is `dms open %u`. That command reported
`DMS is not running. Please start DMS first.` `xdg-open` nevertheless returned 0,
so both a mocked Electron API and an opener exit-code check missed the failure.
The [freedesktop association lookup rules](https://specifications.freedesktop.org/mime-apps/latest/file.html)
put the configuration-directory overrides ahead of distribution defaults.

A process trace of the same loopback HTTP test page established the difference:
normal desktop config launched Zen and the server received the browser request;
an empty config selected DMS and no request arrived. The installed Zen desktop
entry already includes `%u`, so no desktop-file or browser modification is needed.

The fresh-profile command now changes `DPE_PROFILE_DIR`, the mod data directory
and `TMPDIR` while preserving desktop config. The global flags file consequently
remains shared, as documented. Tests needing an isolated flags directory can copy
only the user's MIME association files into that temporary config before launch.
Do not change the desktop browser association or install a second opener merely
to compensate for an isolated test environment.

The speculative custom opener was removed. Settings links use the normal Electron
shell API with fixed URL identifiers and a main-frame IPC guard. There is no
Zen-specific workaround, shell interpolation, process-environment rewrite or
Electron rebuild. Ordinary Discord external links retain Discord's native handler.

## End-to-end verification

Using the actual portable app and a fresh login-only profile, the GitHub and
NVIDIA driver links were clicked in the served settings window. The process trace
showed their exact URLs reaching `/opt/zen-browser-bin/zen-bin`, and niri reported
the matching loaded public page titles in the existing Zen window. A public Terms
URL opened from Discord's renderer through its native window-open handler also
loaded in Zen. The user separately confirmed working links in the visible test
instance. The registration form's protocol-relative Canary Terms anchor was not
used as proof: that attempted click did not dispatch an external opener; the
recorded Discord check explicitly uses `window.open('https://discord.com/terms')`.

No account login was automated. No desktop screenshot, message, call or camera
capture was used. Recorded window titles are limited to the three public test
pages. Raw diagnostic artifacts stay under `out/link-trace/`.

All 17 native settings checks, 319 Python tests, 75 JavaScript tests and lint pass.
The native fixture exercises Electron's real opener process boundary with a
recording executable; the separate desktop checks establish actual page loading.

The diagnostic instance used `--remote-debugging-address=127.0.0.1` and a temporary
port. The Flags editor correctly shows these as session-only comments. They are
not application defaults, saved overrides or release launcher arguments. A normal
launch has neither switch.
