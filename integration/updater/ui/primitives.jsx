/** @jsxImportSource preact */
import { createContext } from "preact";
import { useContext, useLayoutEffect, useRef, useState } from "preact/hooks";
import fallbackIcons from "../../discord-ui/icons.cjs";

export const fallback = fallbackIcons();
export const Presentation = createContext({ icons: {}, versions: {}, theme: "dark" });
export const errorMessage = (failure) =>
  String(failure?.message || failure).replace(/^Error invoking remote method '[^']+': Error: /, "");

export function Icon({ name, label, className = "" }) {
  const { icons = {} } = useContext(Presentation);
  return (
    <span
      class={`dpe-icon ${className}`}
      data-dpe-icon={name}
      role={label ? "img" : undefined}
      aria-label={label}
      title={label}
      aria-hidden={label ? undefined : "true"}
      dangerouslySetInnerHTML={{ __html: icons[name] || fallback[name] || "" }}
    />
  );
}

export function Button({ icon, children, variant = "secondary", className = "", ...props }) {
  return (
    <button type="button" class={`dpe-button dpe-button--${variant} ${className}`} {...props}>
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

export function Switch({ id, label, description, checked, experimental, onChange }) {
  const host = useRef();
  useLayoutEffect(() => {
    const input = document.createElement("input");
    input.id = id;
    input.type = "checkbox";
    input.checked = checked;
    input.setAttribute("aria-label", label);
    input.setAttribute("aria-labelledby", `${id}-label`);
    input.setAttribute("aria-describedby", `${id}-help`);
    input.onchange = () => onChange(input.checked);
    host.current.append(window.DPEControls.switchControl(input));
  }, []);
  return (
    <div class="setting-row">
      <div>
        <label id={`${id}-label`} for={id}>
          {experimental && <Icon name="experimental" label="Experimental" className="risk-icon" />}
          {label}
        </label>
        <p id={`${id}-help`} class="hint">
          {description}
        </p>
      </div>
      <span ref={host} />
    </div>
  );
}

export function RecoveryCommand() {
  const { recoveryCommand } = useContext(Presentation);
  const button = useRef();
  useLayoutEffect(() => window.DPEControls.copyButton(button.current), []);
  return (
    <div class="command">
      <pre>
        <code>
          {recoveryCommand || "discord-canary-prerelease-patched-electron-bin --ignore-flags"}
        </code>
      </pre>
      <button
        ref={button}
        class="command-copy"
        type="button"
        aria-label="Copy command"
        title="Copy command"
      />
      <span class="sr-only" role="status" />
    </div>
  );
}

function WindowGlyph({ name }) {
  return (
    <svg
      class="window-glyph"
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      stroke-width="1"
    >
      {name === "minimize" ? (
        <path d="M1 6.5h10" />
      ) : name === "maximize" ? (
        <rect x="1.5" y="1.5" width="9" height="9" />
      ) : name === "restore" ? (
        <>
          <path d="M3.5 1.5h7v7h-2" />
          <rect x="1.5" y="3.5" width="7" height="7" />
        </>
      ) : (
        <path d="m1.5 1.5 9 9m0-9-9 9" />
      )}
    </svg>
  );
}

export function WindowTitle({ welcome, busy, onError }) {
  const { maximized } = useContext(Presentation);
  const action = async (name) => {
    try {
      await window.Native.windowAction(name);
    } catch (failure) {
      onError(errorMessage(failure));
    }
  };
  return (
    <header class="window-title">
      <h1 class="sr-only">{welcome ? "Welcome to DPE" : "DPE Settings"}</h1>
      <div class="window-actions">
        <button
          id="b_minimize"
          class="window-action"
          type="button"
          aria-label="Minimise"
          onClick={() => action("minimize")}
        >
          <WindowGlyph name="minimize" />
        </button>
        {!welcome && (
          <button
            id="b_maximize"
            class="window-action"
            type="button"
            aria-label={maximized ? "Restore" : "Maximise"}
            onClick={() => action("maximize")}
          >
            <WindowGlyph name={maximized ? "restore" : "maximize"} />
          </button>
        )}
        <button
          id="b_close"
          class="window-action window-close"
          type="button"
          aria-label={welcome ? "Close setup" : "Close settings"}
          disabled={busy}
          onClick={() => action("close")}
        >
          <WindowGlyph name="close" />
        </button>
      </div>
    </header>
  );
}

export function Versions({ modState }) {
  const { versions = {} } = useContext(Presentation);
  const [feedback, setFeedback] = useState("");
  const timer = useRef();
  const entries = Object.entries(versions);
  if (modState?.installed && modState.installed !== "none")
    entries.push([
      modState.installed === "equicord" ? "Equicord" : "Vencord",
      modState.installedVersion || "",
    ]);
  const labels = { dpe: "DPE", discord: "Discord", electron: "Electron", openasar: "OpenAsar" };
  const copy = async () => {
    try {
      await window.Native.copyVersions();
      setFeedback("Copied version information");
    } catch {
      setFeedback("Could not copy. Try again.");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setFeedback(""), 2000);
  };
  return (
    <button
      id="versions"
      class="versions"
      type="button"
      aria-label="Copy version information"
      title={feedback || "Click to copy version information"}
      onClick={copy}
    >
      {entries.map(([key, value]) => (
        <span key={key}>{key === "linux" ? value : `${labels[key] || key} ${value}`.trim()}</span>
      ))}
      <span class="version-feedback" aria-hidden="true">
        <Icon name={feedback.startsWith("Copied") ? "check" : "copy"} />
      </span>
      <span class="sr-only" role="status">
        {feedback}
      </span>
    </button>
  );
}
