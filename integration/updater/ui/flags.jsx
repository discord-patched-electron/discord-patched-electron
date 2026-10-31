/** @jsxImportSource preact */
import { useLayoutEffect } from "preact/hooks";
import { RecoveryCommand } from "./primitives.jsx";
import { initializeFlags } from "../launch-settings.js";

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
        <details id="advanced">
          <summary aria-describedby="advanced-description">
            <span class="advanced-copy">
              <span class="advanced-title">
                <span
                  class="dpe-icon risk-icon"
                  data-dpe-icon="experimental"
                  role="img"
                  aria-label="Experimental"
                  title="Experimental"
                ></span>
                <span id="advanced-label">Show Advanced Settings</span>
              </span>
              <span id="advanced-description" class="hint">
                Video, rendering and driver overrides. These may break Discord.
              </span>
            </span>
            <span class="disclosure-action" aria-hidden="true">
              <span class="dpe-icon details-chevron" data-dpe-icon="chevron"></span>
            </span>
          </summary>
          <div class="advanced-content">
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
          </div>
        </details>
        <label for="flags" class="flags-label">
          Flags
        </label>
        <p id="flags-help" class="hint">
          Your overrides, one per line. App defaults are applied separately.
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
            Changing these can break startup. Variables:
            <code>{"{DESKTOP_ID}"}</code>, <code>{"{PROFILE_DIR}"}</code>,{" "}
            <code>{"{OPENH264_LIBRARY_PATH}"}</code>.
          </p>
          <p class="hint">
            OpenH264 Video Codec provided by Cisco Systems, Inc.{" "}
            <a
              href="#openh264-license"
              onClick={(event) => {
                event.preventDefault();
                void window.openConfigLink("openh264-license");
              }}
            >
              License
            </a>
            . To disable or re-enable it, remove or restore <code>--enable-libopenh264</code> below,
            save and restart.
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
          <button class="dpe-button dpe-button--primary" id="save" type="submit" disabled>
            Save
          </button>
        </div>
        <p id="status" role="status"></p>
      </form>
    </section>
  );
}
