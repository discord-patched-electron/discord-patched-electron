# Independent audio reliability verification

Baseline: `f90b251caee6b4e7d265c0e450e53a70d97b0aba` (HEAD unchanged).
Review started 30 October and completed 31 October 2026, Europe/London.
Only the supplied checklist, blind claims and two supplied reproduction tests
were read as review evidence. The originating report, reasoning and logs were
not read. Production code and pre-existing tracked files remain unchanged.

## Verdicts

- **A1: DISPUTED as a demonstrated user-facing reliability finding.** The
  conditional X11 wrapper state defect is confirmed for both setters, but a
  realistic native rejection with a still-resolvable window and no subsequent
  cleanup event is not established by these fixtures. High-severity audio leakage
  is not demonstrated. Retain as a conditional exception-handling issue, not a
  confirmed high-severity release finding.
- **A2: CONFIRMED, medium severity.** A live selected application can lose its
  share-audio route when its launcher exits and only its parent PID changes.
  It remains unavailable on later ticks of the same selection. Recovery is a
  new controller generation, including audio off/on, not necessarily video
  reselection. There is no permanent installation damage or isolation breach.

All following code references are baseline repository paths and line numbers.
These are offline control-plane results; no PCM, call or desktop was exercised.

## A1: rejection commits audio selection before native acceptance

The supplied [reproduction](../../tests/audio/repro_x11_rejected_source.test.cjs)
passes twice. It starts A with audio requested, injects a synchronous exception
in either native source setter, then selects B. B remains the last requested
`soundsharePid` after a timer callback and an unrelated bitrate update. Explicit
clear sets PID zero. This proves transport commands, not that samples actually
reach a remote listener or that native video continues showing A.

Baseline `integration/audio/app-audio.cjs:155-175` silences the old PID, sets the
new source and calls `update` before invoking the native setter at `196-204`.
There is no exception rollback. `128-149` resolves and writes the new audio PID;
`178-194` reapplies it on unrelated window transport changes. The synthetic
native object records these writes and deliberately does not emit source-ended.
The supplied injected resolver bypasses X11 resolution and process validation.
Its identities and option shapes match the existing adapter fixtures, but a
throwing mock cannot prove how the proprietary native implementation rejects a
valid selection.

The independent [resolver controls](../../tests/audio/verify_x11_rejection.test.cjs)
run the exact baseline source in a VM with only OS/timer/native boundaries
mocked. They exercise the production `windowPid`, `processIdentity`, `descendant`
and `resolve` functions with local DISPLAY, same-UID non-zombie process stats,
valid numeric start times and one playback PID per application. No host `/proc`,
X server or Pulse server is queried. Both setters still write B before entering
the throwing native method; B survives three ticks and an unrelated update.
This rules out the supplied resolver shortcut as the cause of the state defect.

Refutation controls materially narrow the claim:

- Source-ended immediately writes zero and later unrelated updates do not restore
  B (`app-audio.cjs:114-126`). Clear and destroy also silence (`206-221`).
- If the X11 window disappears, a failed `xprop` resolution makes the next tick
  write zero. An application process surviving its closed window is insufficient
  for persistent B capture (`20-38`, `53-84`, `165-173`).
- X11 routing is optional: `wrap` at `344-356` requires `DPE_APP_AUDIO=1` outside
  Wayland. The ordinary default Wayland path has rollback in
  `integration/audio/wayland-audio.cjs:165-176` and an existing rejection test in
  `tests/audio/wayland.test.cjs:51-67`.

A supported setup is opt-in local X11, an active share of A and a switch to B
with one audio-owning PID. What is missing is evidence of a native failure in
that setup which leaves B resolvable and neither ends nor clears the source.
The exception injected here is a plausible boundary failure, not a demonstrated
production trigger. In particular, “the window disappeared” does not establish
persistence: the independent disappeared-window control refutes that extension.
The pre-existing native integration fixture uses an unknown window at
`tests/audio/preload.cjs:65-69` without expecting an exception; it does not
establish a rejecting-native case either. No native module was run for this review.

Intentional-limitations check: `docs/agent/wayland-audio.md:12-20` documents the
optional X11 adapter and silence on unresolved selection; `77-80` specifies
cleanup. `docs/agent/audio-investigation.md:80-89` describes the X11 resolver and
clear behavior, but is explicitly historical (`3-6`). The code limitation at
`app-audio.cjs:78-80` refuses multiple audio-owning PIDs; this fixture uses one
and is unaffected. The Wayland rejection comment (`wayland-audio.cjs:172`)
explicitly expects silence after native rejection. No comment or agent note
makes retaining a rejected source intentional. Thus defensive rollback is
justified, while meaningful live isolation impact and its severity remain
unverified. Suggested change, not implemented: commit selection only after
acceptance or catch rejection, clear source/requested/hint and timer state,
silence, and rethrow; cover subsequent ticks and transport writes.

## A2: mutable ancestry is mistaken for replacement identity

The supplied [reproduction](../../tests/test_repro_audio_reparent.py) passes.
It reuses existing exact video-link/cast fixtures, retains real `Route.sync`,
and supplies synthetic playback nodes for the selected PID and its child,
another application, Discord and the helper child. Only selected parent/child
feeds are started. Changing `(200, application-start, 500)` to
`(200, application-start, 1)` and removing launcher 500 closes those feeds;
three ticks remain unavailable with no route. Generation 2 recreates the same
selected feeds. Command, graph, compositor, process and child boundaries are
mocked, including a guard against real subprocess execution.

The realistic supported trigger is a same-UID niri application launched by a
shell/launcher which exits while the application survives (for example a
background application configured to survive shell exit). Reparenting to init
or a subreaper changes PPID, not PID/start time, its Wayland connection, the
selected window or the exact active cast. This needs no Flatpak namespace,
focus heuristic, browser-tab isolation or unsupported compositor discovery.
The application must survive its parent: if it also exits, silence is correct.

Baseline `integration/audio/pipewire_audio.py:37-49` validates UID and returns
`(pid, start_time, ppid)`. Exact graph and niri correlation happens at `98-126`,
`144-170` and `601-609`. At `628-640`, equality of the entire identity tuple
causes a PPID-only change to take the reused-process branch. The route closes,
PID zero/unavailable is emitted, and `target_key` retains the old tuple. Later
ticks repeat the mismatch rather than rebuilding. The main loop has no timeout
reset for this ordinary return (`692-707`). The JavaScript helper reader maps
unavailable to native PID zero (`wayland-audio.cjs:113-125`).

The independent [identity matrix](../../tests/test_verify_audio_reparent.py)
uses a separately constructed exact graph/cast and the production `/proc` parser
with synthetic stat text, not mocked `process_identity`. Controls establish:

- Unchanged identity keeps the route ready across three ticks.
- PPID alone changes the tuple while `descendant(200, old_identity)` remains true;
  the controller nevertheless closes the route and stays unavailable.
- A changed start time correctly prevents following a replacement process.
- A different UID correctly fails identity validation and leaves no route.
- A new generation restores the same application after the parent-only change.

Intentional-limitations check: the comment at `pipewire_audio.py:634-635`
intentionally prevents following a vanished/reused process without new selection.
A PPID-only change is neither. The existing descendant check at `52-62` already
uses PID/start time for identity and PPID for ancestry traversal. The documented
contract at `docs/agent/wayland-audio.md:25-29,47-51` promises application/descendant
selection with UID/start-time checks, and `43-45` excludes unrelated capabilities.
No documented limitation excludes a surviving application whose launcher exits.
The failure is therefore an overbroad safety check, not the intended PID-reuse
policy itself.

Impact is recoverable share-audio loss, with a misleading dependency warning,
not cross-application capture. “Permanent” in the supplied test name means only
persistence within that unchanged generation. `Controller.update` resets keys
at `470-491`; the wrapper reconfigures on audio request changes at
`wayland-audio.cjs:139-148,189-205`. Suggested change, not implemented: use stable
PID/start-time identity for replacement detection while retaining UID validation
and current parent information for ancestry. Preserve real PID-reuse rejection.

## Validation and artifacts

Worktree: `/home/he3als/.t3/worktrees/discord-patched-electron/t3-48c5d6e9`.
Evidence directory:
`/home/he3als/scratch-ai-repo/discord-patched-electron/out/reliability-review-20261030/verify-audio/`.
It contains exact supplied test copies, independent controls, this report,
`commands.txt`, focused logs, `full-test.log`, `lint.log`, and `integrity.log`.

Every command ran with `unshare --user --map-current-user --net env UV_OFFLINE=1`.
Dependencies used local `uv sync --locked --offline` and a read-only-use symlink
to the permitted original `node_modules`; nothing was installed over that tree.
Both supplied reproductions pass (2 JS, 1 Python). Independent controls pass
(3 JS, 1 Python test with four identity subcases). Full `bun run test` passes
343 Python and 80 JavaScript tests. `bun run lint` passes. No runtime build,
network, account, live service or host audio/device operation was performed.

An initial independent JS control incorrectly expected the last _partial_
transport-options object after source-ended to repeat PID zero. The adapter
correctly passed through the bitrate-only update. The control was corrected to
inspect the last PID-bearing write. Initial focused/full logs are retained as
`*-initial-fixture-error.log`; they are fixture-authoring errors, not additional
product findings. No production code changed to obtain passing results.

Integrity checks compare all pre-existing tracked files with the baseline and
compare supplied test copies byte-for-byte with the authorized originals.
The report links above were checked locally. The original dirty repository and
other reviewer artifacts were not modified; only the authorized `verify-audio`
directory was written there. Excluded updater/backup issues were not investigated.
