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

// Match the screen-share audio dialog's inline separator and spacing tokens.
export function InlineBullet() {
  return (
    <span class="dpe-inline-bullet" aria-hidden="true">
      •
    </span>
  );
}

// Share the measured Discord advanced-settings disclosure across settings pages.
export function Disclosure({
  id,
  label,
  expandedLabel,
  description,
  experimental,
  className = "",
  children,
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      id={id}
      class={`dpe-disclosure ${className}`}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary aria-describedby={description ? `${id}-description` : undefined}>
        <span class="disclosure-copy">
          <span class="disclosure-title">
            {experimental && (
              <Icon name="experimental" label="Experimental" className="risk-icon" />
            )}
            <span id={`${id}-label`}>{open && expandedLabel ? expandedLabel : label}</span>
          </span>
          {description && (
            <span id={`${id}-description`} class="hint disclosure-description">
              {description}
            </span>
          )}
        </span>
        <span class="disclosure-action" aria-hidden="true">
          <Icon name="chevron" className="details-chevron" />
        </span>
      </summary>
      <div class="disclosure-content">{children}</div>
    </details>
  );
}

export function Switch({
  id,
  label,
  description,
  checked,
  disabled = false,
  value,
  experimental,
  onChange,
}) {
  const host = useRef();
  const inputRef = useRef();
  const change = useRef(onChange);
  change.current = onChange;
  useLayoutEffect(() => {
    const input = document.createElement("input");
    input.id = id;
    input.type = "checkbox";
    input.checked = checked;
    input.disabled = disabled;
    inputRef.current = input;
    input.setAttribute("aria-labelledby", `${id}-label`);
    input.onchange = () => change.current(input.checked);
    host.current.append(window.DPEControls.switchControl(input));
    return () => host.current?.replaceChildren();
  }, []);
  useLayoutEffect(() => {
    const input = inputRef.current;
    input.checked = checked;
    input.disabled = disabled;
    input.value = value ?? "on";
    if (description) input.setAttribute("aria-describedby", `${id}-help`);
    else input.removeAttribute("aria-describedby");
    if (typeof label === "string") input.setAttribute("aria-label", label);
    else input.removeAttribute("aria-label");
  });
  return (
    <div class="setting-row">
      <div>
        <label id={`${id}-label`} for={id}>
          {experimental && <Icon name="experimental" label="Experimental" className="risk-icon" />}
          {label}
        </label>
        {description && (
          <p id={`${id}-help`} class="hint">
            {description}
          </p>
        )}
      </div>
      <span ref={host} />
    </div>
  );
}

export function Select({
  id,
  label,
  value,
  options,
  disabled = false,
  description,
  placeholder,
  onChange,
}) {
  const host = useRef();
  const input = useRef();
  const control = useRef();
  const change = useRef(onChange);
  change.current = onChange;
  useLayoutEffect(() => {
    const select = document.createElement("select");
    select.id = id;
    select.name = id;
    select.onchange = () => change.current(select.value);
    host.current.append(select);
    input.current = select;
    control.current = window.DPEControls.upgradeSelect(select);
    return () => {
      control.current.destroy();
      host.current?.replaceChildren();
    };
  }, []);
  useLayoutEffect(() => {
    input.current.replaceChildren(...options.map(([key, text]) => new Option(text, key)));
  }, [options]);
  useLayoutEffect(() => {
    input.current.value = value;
    input.current.dataset.placeholder = placeholder || "";
    input.current.disabled = disabled;
    if (description) control.current.trigger.setAttribute("aria-describedby", `${id}-help`);
    else control.current.trigger.removeAttribute("aria-describedby");
    control.current.sync();
  });
  return (
    <div class="desktop-select-setting">
      <label id={`${id}-label`} for={id}>
        {label}
      </label>
      <div ref={host} />
      {description && (
        <p id={`${id}-help`} class="hint">
          {description}
        </p>
      )}
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
  const {
    maximized,
    windowControls: controls = { minimizeButton: true, maximizeButton: true, closeButton: true },
  } = useContext(Presentation);
  const visible =
    controls.minimizeButton || (!welcome && controls.maximizeButton) || controls.closeButton;
  const action = async (name) => {
    try {
      await window.Native.windowAction(name);
    } catch (failure) {
      onError(errorMessage(failure));
    }
  };
  return (
    <header class="window-title" hidden={!visible}>
      <h1 class="sr-only">{welcome ? "Welcome to DPE" : "DPE Settings"}</h1>
      <div class="window-actions">
        <button
          id="b_minimize"
          class="window-action"
          type="button"
          aria-label="Minimise"
          hidden={!controls.minimizeButton}
          disabled={!controls.minimizeButton}
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
            hidden={!controls.maximizeButton}
            disabled={!controls.maximizeButton}
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
          hidden={!controls.closeButton}
          disabled={busy || !controls.closeButton}
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
