# Native Linux mouse preferences, 1 November 2026

Later [settings refinements](settings-refinements-20261101.md) add the autoscroll
paste dependency. The newer native window-controls runtime retains this paste
patch and supersedes the local artifact recorded below.

The Flags tab exposes **Middle-click autoscroll**, **Disable middle-click paste**
and **Disable smooth scrolling** using the existing switch components. They save
through the normal flags editor and apply on the next launch.

## Native implementation

The catalog uses Chromium's `MiddleClickAutoscroll` feature and
`--disable-smooth-scrolling` switch. Primary-selection paste uses
`--blink-settings=middleClickPasteAllowed=false`. The flag resolver and editor
merge Blink settings individually, preserving unrelated settings and allowing a
later explicit setting to override an earlier one. Autoscroll always forces
primary paste off in the final launch arguments, including when manual or CLI
flags request paste. The editable configuration keeps the independent paste
choice, which returns when autoscroll is disabled. While forced, the paste switch
is checked and disabled. Explicit Blink feature overrides follow Chromium's
native enable/disable precedence, and recovery mode still passes no flags.

The pinned Chromium 158 source already checks `middleClickPasteAllowed` inside
`SelectionController::HandlePasteGlobalSelection`, after DOM mouse handlers run.
It controls only PRIMARY paste. GTK's `gtk-enable-primary-paste` remains the
default when no explicit override exists. No global desktop preferences, clipboard
contents, event listeners or mouse dispatch paths are changed by DPE.

An unpatched runtime initially honors the override but loses it after
`webContents.setWebRTCIPHandlingPolicy()` refreshes renderer preferences. The new
maintained [follow-up patch](../../patches/ports/46/middle-click-paste.patch)
reapplies only the explicit `middleClickPasteAllowed` command-line setting after
the Linux renderer preference update. Other Blink settings are not reapplied.
The original imported Discord patches remain intact.

Relevant files in the pinned native checkout:

- `third_party/blink/renderer/core/exported/web_view_impl.cc`: initial command-line
  settings and `UpdateRendererPreferences`.
- `third_party/blink/renderer/core/editing/selection_controller.cc`:
  `HandlePasteGlobalSelection`.
- `third_party/blink/renderer/platform/runtime_enabled_features.json5`:
  `MiddleClickAutoscroll`; the generated base feature accepts `--enable-features`.
- `content/browser/web_contents/web_contents_impl.cc`: native smooth scrolling
  switch handling.
- `ui/gtk/gtk_ui.cc`: desktop primary-paste preference.

## Verification

The retained Electron 46.0.0-alpha.1 / Chromium 158.0.8114.0 runtime was rebuilt
with two Ninja jobs inside the existing four-CPU, 26 GiB container. The physical
checkout and all compiled objects were retained. Incremental compilation and
distribution packaging took 66 seconds; the subsequent Ninja check reported no
work. No full Chromium rebuild or running compiler restart was needed.

The offline [native input fixture](../../tests/middle-click/main.cjs) passed in
default, paste-disabled and combined paste-disabled/autoscroll modes:

- PRIMARY paste in native textarea and contenteditable follows the selected mode.
- The override survives a WebRTC renderer-preference update.
- Normal Ctrl+V clipboard paste still works.
- Middle-click links reach the native window-open handler.
- Canvas and iframe activity fixtures receive middle mousedown, mouseup and
  auxclick, including when their own handlers consume the default action.
- Native autoscroll moves an overflow pane only when enabled.

The fixture uses `sendInputEvent`, including screen coordinates required by native
autoscroll. It runs in Xvfb with disposable profiles and no Discord account. It
does not claim coverage of every live Discord activity or Wayland compositor.
Focused flag coverage passed 44 Python tests and 11 JavaScript tests.

Run a mode from the repository root, substituting a verified runtime and a fresh
output directory:

```sh
env -u ELECTRON_RUN_AS_NODE -u WAYLAND_DISPLAY \
  DPE_INPUT_OUTPUT=/tmp/dpe-input-check DPE_INPUT_MODE=autoscroll \
  xvfb-run -a --server-num=211 /path/to/runtime/electron \
  --disable-setuid-sandbox tests/middle-click
```

Repeat with `DPE_INPUT_MODE=default` and `DPE_INPUT_MODE=blocked` to compare system
defaults and the paste-only override.

## Local artifact and cache identity

The locally verified artifact is at
`/mnt/storage/dpe-middle-click-20261101/artifact`; pass that directory to
`scripts.build_local_app --artifact`. It contains `artifact-provenance.json`,
`capacity.json`, `source.json`, `binary/runtime-dist.zip` and the extracted
`runtime/electron`. Packaging validation accepts its current runtime identity.

- Runtime key: `7369d3cab08718b40ae1cc37ec653baff6bd239c8da079cfa8d80886652a41d4`.
- Electron tree: `63b7bc8327f19de7d5e51ed74c2cc63d1ac175c7`.
- Archive SHA-256: `b9be0f7cfc717cc20c4614aa1c8258d58d3e42785738bc83541e2c8623fc9c38`.

The retained metadata migration checked unchanged non-port native inputs, the
exact appended follow-up patch, Electron's staged tree and pinned upstream commit.
The new cache identity points to the existing physical workspace. Prior metadata,
the native diff, compiler logs and native fixture reports are preserved alongside
the artifact. Its source report remains `runtime_validated: false` and
`publishable: false`: these targeted mouse checks do not replace the complete
release media-validation suite.
