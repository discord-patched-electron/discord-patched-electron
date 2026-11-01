/** @jsxImportSource preact */
import { useEffect, useLayoutEffect, useState } from "preact/hooks";
import { Disclosure, RecoveryCommand } from "./primitives.jsx";
import { initializeFlags } from "../launch-settings.js";

export function FlagsChangeNotice() {
  const [state, setState] = useState({
    changed: document.body.dataset.flagsChanged === "true",
    busy: false,
  });
  useEffect(() => {
    const update = (event) => setState(event.detail);
    window.addEventListener("dpe-flags-state", update);
    return () => window.removeEventListener("dpe-flags-state", update);
  }, []);
  return (
    <div id="flags-change-note" hidden={!state.changed}>
      <p class="sidebar-note" role="status">
        Flags have changed and will apply next launch.{" "}
        <button
          type="button"
          class="inline-link"
          id="flags-undo"
          disabled={state.busy}
          onClick={() => window.dispatchEvent(new Event("dpe-flags-undo"))}
        >
          Undo
        </button>
      </p>
    </div>
  );
}

// The adapter owns the validated form controls. Preact owns its stable layout.
export function Flags({ active }) {
  useLayoutEffect(() => initializeFlags(), []);
  return <FlagsView active={active} />;
}

export function FlagsView({ active }) {
  return (
    <section id="flags-panel" role="tabpanel" aria-labelledby="t_flags" hidden={!active}>
      <div class="page-heading">
        <h2>Flags</h2>
      </div>
      <aside class="recovery-help" id="flags-recovery-help" aria-labelledby="recovery-title">
        <h3 id="recovery-title">Won’t start?</h3>
        <p>Run this command to start without saved flags:</p>
        <RecoveryCommand />
      </aside>
      <p id="recovery" class="hint" hidden>
        Recovery mode. Saved flags are off.
      </p>
      <p id="warnings" class="hint" hidden></p>
      <p id="error" role="alert" hidden></p>
      <p id="loading" role="status">
        Loading…
      </p>
      <form id="settings" hidden>
        <div class="flag-row">
          <div>
            <label for="display">Display</label>
            <p class="hint">Choose Wayland or X11.</p>
          </div>
          <select id="display" name="display">
            <option value="auto">Automatic</option>
            <option value="wayland">Wayland</option>
            <option value="x11">X11</option>
          </select>
        </div>
        <section class="settings-group" aria-labelledby="input-options-heading">
          <h3 id="input-options-heading" class="section-label">
            Scrolling & mouse
          </h3>
          <div id="input-options"></div>
        </section>
        <Disclosure
          id="advanced"
          label="Show Advanced Settings"
          expandedLabel="Hide Advanced Settings"
          description="Video, rendering and driver overrides. These may break Discord."
          experimental
        >
          <div class="flag-row">
            <div>
              <label for="backend">
                <span class="risk-icon" role="img" aria-label="May break Discord"></span>Renderer
              </label>
              <p class="hint">Choose how the GPU draws the app.</p>
            </div>
            <select id="backend" name="backend"></select>
          </div>
          <div id="advanced-options"></div>
        </Disclosure>
        <label for="flags" class="flags-label">
          Flags
        </label>
        <p id="flags-help" class="hint">
          Your overrides, one per line. Valid changes save automatically and apply next launch.
        </p>
        <textarea
          id="flags"
          name="flags"
          rows="6"
          spellcheck="false"
          autocapitalize="off"
          aria-describedby="flags-help"
        ></textarea>
        <div id="essential-section">
          <label for="essential-flags" class="flags-label">
            <span
              class="risk-icon danger"
              role="img"
              aria-label="May break startup"
              title="May break startup"
            ></span>
            Essential flags
          </label>
          <p id="essential-help" class="hint">
            Changing these can break startup. Variables: <code>{"{DESKTOP_ID}"}</code>,{" "}
            <code>{"{PROFILE_DIR}"}</code>, <code>{"{OPENH264_LIBRARY_PATH}"}</code>.
          </p>
          <textarea
            id="essential-flags"
            name="essential-flags"
            rows="4"
            spellcheck="false"
            autocapitalize="off"
            aria-describedby="essential-help"
            disabled
          ></textarea>
        </div>
        <div class="flag-actions">
          <label class="essential-toggle">
            <input id="edit-essential" type="checkbox" aria-controls="essential-flags" />
            Edit essential flags
          </label>
          <button class="dpe-button dpe-button--secondary" id="reset-flags" type="button">
            Reset
          </button>
          <button class="dpe-button dpe-button--secondary" id="discard" type="button">
            Reload
          </button>
        </div>
        <p id="status" class="sr-only" role="status"></p>
      </form>
    </section>
  );
}
