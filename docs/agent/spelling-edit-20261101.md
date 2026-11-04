# Native spelling checks for message editing

The earlier dictionary probe did not exercise prefilled text. Chromium 158 marks
script-driven focus separately from direct mouse/keyboard focus. Its idle and
cold spellcheck controllers reject that focus even when Discord creates the
editor in response to the user's Edit action. A later `spellcheck` attribute
update also cancels pending checking. Reproductions using prefilled Slate-style
markup returned no misspelling until native input changed the text.

The additive [native patch](../../patches/ports/46/prefilled-spellcheck.patch)
allows those checks while a transient user activation exists in a top-level
HTTPS Discord document (`discord.com`, `canary.discord.com`, `ptb.discord.com`).
It changes only spelling-controller eligibility, not focus, DOM contents,
selection, input events or undo history. Other origins, embedded frames,
script-only activation and disabled spelling retain Chromium's existing checks.
The unrestricted testing feature was used only to isolate the cause; it is not
enabled in the fix or launcher.

The source lock pins the additive patch and candidate tree
`2f4f4b05d42131670c238e60c7d91b918d7aabc0`. Imported patches remain unchanged.
The outer patch was independently applied to an alternate index of the prior
candidate tree and reproduced the new tree. Native compilation reused the idle
retained workspace and its objects with two jobs; no full rebuild or compiler
restart was needed. Its previous metadata is backed up in the local artifact.

Evidence under `out/spelling-edit/`:

- `discord-before.log`: prefilled, late-content and late-attribute cases fail on
  the previous native runtime without typing anything.
- `native-final/editing.json`: ten native cases pass on the packaged runtime,
  including mouse/keyboard entry, text arriving after focus, later spellcheck
  attributes, correction/undo, unchanged DOM/caret/focus, disabled checking,
  no user gesture, same-origin subframes, unrelated and lookalike origins.
- `build-final.log`, `artifact.json`, `artifact/`: completed incremental build
  and exported runtime, with local provenance rather than a fabricated CI run.
- `tests.log`, `lint-final.log`: 554 Python and 265 JavaScript tests passed, as did lint.
- `dictionaries/result.json`: English dictionary checks, suggestions, replacement
  and live toggle still pass on the rebuilt runtime.
- `launch/launch-report.json`: packaged app passes fresh Welcome and signed-out
  Discord launch, with pinned client modules verified.
- `test.sh`: runnable local app with the new native runtime and Languages shortcut.
  It defaults to a fresh manual profile; `DPE_PROFILE_DIR` can select a manual
  test profile. Application/runtime updates are disabled for this local test.

Runtime archive SHA-256:
`b5562836a11e9c434781320e7559aafbf6d6d84b93206632f85a4dc07e5cd146`.
The fix needs this rebuilt runtime (or a future release containing the patch);
updating JavaScript alone cannot fix the native focus gate. No signed-in message
was edited automatically. The native fixture hosts local pages with Discord
origins, without Discord API requests or account data.
