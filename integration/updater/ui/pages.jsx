/** @jsxImportSource preact */
import { useContext, useEffect, useRef, useState } from "preact/hooks";
import {
  Button,
  Icon,
  Presentation,
  RecoveryCommand,
  Switch,
  errorMessage,
} from "./primitives.jsx";

import { TrayIcons } from "./desktop.jsx";
import {
  DesktopError,
  DesktopSwitch,
  NotificationPreferences,
  LinkPreferences,
  WindowPreferences,
} from "./desktop-preferences.jsx";

const groups = [
  [
    "Screen sharing",
    [
      [
        "Automatically detect screen-share audio",
        "On Wayland, select a shared application's audio when its playback can be confidently matched. Otherwise, ask you to choose. Applies to your next share.",
        "audioAutoDetect",
        true,
      ],
      [
        "Skip audio picker for entire screens",
        "On Wayland, share system audio automatically when an entire screen is confirmed, excluding Discord. Turn off to choose audio for screens too. Applies to your next share.",
        "audioSkipScreenPicker",
        true,
      ],
    ],
  ],
  [
    "Privacy",
    [
      [
        "Reduce tracking",
        "Disable the additional analytics and error reporting handled by OpenAsar.",
        "noTrack",
        true,
      ],
      [
        "Hide typing indicator",
        "Stop showing other people when you are typing.",
        "noTyping",
        false,
      ],
    ],
  ],
  [
    "Startup",
    [
      [
        "Match the splash screen theme",
        "Use your client mod’s theme on the startup screen.",
        "themeSync",
        true,
      ],
      ["Quickstart", "Skip some startup checks to open Discord sooner.", "quickstart", false],
      ["Allow multiple windows", "Run more than one Discord instance.", "multiInstance", false],
    ],
  ],
];

export function General({ config, desktop }) {
  return (
    <div>
      <DesktopError model={desktop} />
      {groups.map(([title, settings]) => (
        <section class="settings-group" key={title}>
          <h3 class="section-label">{title}</h3>
          {settings.map(([label, description, key, fallback]) => (
            <Switch
              key={key}
              id={`setting-${key}`}
              label={label}
              description={description}
              checked={config[key] ?? fallback}
              experimental={["quickstart", "multiInstance"].includes(key)}
              onChange={(checked) => {
                config[key] = checked;
                window.Native.set(config);
              }}
            />
          ))}
          {title === "Startup" && (
            <>
              <DesktopSwitch
                model={desktop}
                name="startMinimized"
                label="Start minimized"
                description="Start Discord in the tray, or minimized on the taskbar when the tray is off. Also applies when Discord starts automatically."
              />
              <DesktopSwitch
                model={desktop}
                name="tray"
                label="System tray"
                description="Show Discord in the system tray. Turn off to close its window instead of hiding it in the tray."
              />
            </>
          )}
        </section>
      ))}
      <LinkPreferences model={desktop} />
      <NotificationPreferences model={desktop} />
    </div>
  );
}

let editorLoader;
async function loadEditor() {
  if (window.monaco) return;
  if (!editorLoader)
    editorLoader = (async () => {
      if (!window.require)
        await new Promise((resolve, reject) => {
          const script = document.createElement("script");
          script.src = "monaco-editor/min/vs/loader.js";
          script.onload = resolve;
          script.onerror = reject;
          document.head.append(script);
        });
      window.require.config({ paths: { vs: "monaco-editor/min/vs" } });
      await new Promise((resolve, reject) =>
        window.require(["vs/editor/editor.main"], resolve, reject),
      );
    })();
  return editorLoader;
}

export function Appearance({ config, desktop }) {
  const { theme } = useContext(Presentation);
  const textarea = useRef();
  const host = useRef();
  const editor = useRef();
  const [saved, setSaved] = useState(false);
  const save = (value) => {
    config.css = value;
    window.Native.set(config);
    setSaved(true);
  };
  useEffect(() => {
    let cancelled = false;
    void loadEditor()
      .then(() => {
        if (cancelled) return;
        editor.current = window.monaco.editor.create(host.current, {
          value: config.css || "",
          language: "css",
          theme: document.body.dataset.dpeTheme === "light" ? "vs" : "vs-dark",
          automaticLayout: true,
          minimap: { enabled: false },
          ariaLabel: "Custom CSS",
          fontSize: 14,
        });
        textarea.current.hidden = true;
        host.current.hidden = false;
        editor.current.onDidChangeModelContent(() => save(editor.current.getValue()));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      editor.current?.dispose();
    };
  }, []);
  useEffect(() => {
    if (editor.current) window.monaco.editor.setTheme(theme === "light" ? "vs" : "vs-dark");
  }, [theme]);
  return (
    <div class="appearance-page">
      <DesktopError model={desktop} />
      <WindowPreferences model={desktop} />
      <TrayIcons />
      <div class="appearance-heading">
        <label for="theme-css" class="section-label">
          Custom CSS
        </label>
        <p id="theme-help" class="hint">
          Changes save automatically. Restart Discord to apply them.
        </p>
        <p id="theme-status" class="sr-only" role="status">
          {saved ? "Saved." : ""}
        </p>
      </div>
      <div class="theme-editor-frame">
        <textarea
          ref={textarea}
          id="theme-css"
          class="dpe-input theme-css"
          spellcheck="false"
          aria-describedby="theme-help"
          defaultValue={config.css || ""}
          onInput={(event) => save(event.currentTarget.value)}
        />
        <div ref={host} class="theme-editor" hidden />
      </div>
    </div>
  );
}

const diagnostics = [
  [
    "gpu",
    "Graphics & video support",
    "Check hardware acceleration, your graphics driver and supported video codecs.",
  ],
  [
    "media-internals",
    "Video playback",
    "See decoder errors when a video freezes, stutters or won’t play.",
  ],
  [
    "webrtc-internals",
    "Voice & screen sharing",
    "Open before reproducing a problem to see connection and capture statistics.",
  ],
  [
    "network-errors",
    "Network errors",
    "Look up an error code shown when a download or connection fails.",
  ],
  ["process-internals", "Processes & frames", "Inspect running renderer processes and frames."],
  ["histograms", "Performance counters", "Inspect Chromium’s internal measurements."],
];

export function Diagnostics({ select, active = true }) {
  const [busy, setBusy] = useState();
  const [error, setError] = useState("");
  const open = async (name) => {
    setBusy(name);
    setError("");
    try {
      await window.Native.openDiagnostic(name);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(undefined);
    }
  };
  return (
    <section
      id="troubleshooting-panel"
      hidden={!active}
      role="tabpanel"
      aria-labelledby="t_troubleshooting"
    >
      <section class="help-section">
        <h3>Discord won’t start?</h3>
        <p class="hint">Start without saved launch flags. Your saved settings stay in place.</p>
        <RecoveryCommand />
        <p class="hint">
          If it opens, visit{" "}
          <button
            class="inline-link"
            type="button"
            data-select-tab="flags"
            onClick={() => select("flags")}
          >
            Flags
          </button>{" "}
          to review or reset your changes.
        </p>
      </section>
      <div class="diagnostics-list">
        {diagnostics.map(([name, label, description]) => (
          <div class="diagnostic-row" key={name}>
            <div>
              <h3>
                {label}
                <span class="diagnostic-address">chrome://{name}</span>
              </h3>
              <p class="hint">{description}</p>
            </div>
            <Button data-diagnostic={name} disabled={busy === name} onClick={() => open(name)}>
              Open
              <Icon name="external" />
              <span class="sr-only"> {label}</span>
            </Button>
          </div>
        ))}
      </div>
      <p id="diagnostic-error" role="alert" hidden={!error}>
        {error}
      </p>
    </section>
  );
}
