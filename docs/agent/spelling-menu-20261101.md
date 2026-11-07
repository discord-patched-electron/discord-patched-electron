# Spelling diagnosis and Languages shortcut

The initial tests below covered newly typed text, not an existing message opened
for editing. The latter exposed a separate Chromium regression, fixed in the
[prefilled-editor follow-up](spelling-edit-20261101.md).

The reported `convienient` typo is rejected by Chromium's dictionaries, including
`en-GB` plus `en` (the saved manual selection). The native Electron probe reproduces
an empty misspelling result while the unfinished word remains at the caret. With
a trailing space, both textarea and contenteditable inputs report the typo,
suggest `convenient`, and accept replacement. This is Chromium's deferred checking,
not evidence of a missing dictionary. A signed-in Discord composer was not driven.

Discord's current spelling hook opens support article `360000877191` for Languages.
The desktop adapter now routes that precise HTTPS article navigation from trusted
Discord origins to `config.open("spelling", contents)`. Main-window and popout
handlers retain their other external-link behavior. Translated menu labels,
keyboard activation, native suggestions and learned-word IPC stay owned by Discord.
This also routes direct links to the same spelling help article inside Discord to
Spelling. Unsupported desktop patch layouts fail closed before activation.

Validation artifacts are under `out/spelling-fix/`:

- `bilingual-final/result.json`: real Electron 46.0.0-alpha.1 English/French
  dictionaries, the reported typo in both editor types, suggestions and replacement.
- `english-complete/repeat.json`: `en-GB`/`en`, offline restart, zero dictionary
  downloads, personal-word persistence, typo detection and replacement.
- `settings-route/result.json`: a disposable Discord-origin page's real
  `window.open()` request opens the actual Spelling controls, then reuses the
  existing settings window and switches it back to Spelling.
- Both retained desktop-core layouts accept the new validated patch sites.
- `tests-final.log`: 550 Python and 262 JavaScript tests passed. Repository lint
  also passed at that point. A subsequent lint run (`lint-final.log`) encountered
  formatting changes in the concurrently edited `tests/test_camera_flip.py`;
  the spelling changes pass scoped lint and formatting checks.
- The complete settings harness was attempted; unrelated footer/Mods focus
  assertions prevented it reaching Spelling. The focused navigation probe above
  passed. No new visual-parity claim is made.

`tests/spellcheck/main.cjs` supports `DPE_SPELLCHECK_ENGLISH=1` for the reported
language combination, and the existing `DPE_SPELLCHECK_REPEAT=1` for its offline
restart. Use a fresh disposable output on the first pass. Rich-text replacement
preserves Chromium's trailing nonbreaking space.
