/** @jsxImportSource preact */
import { useCallback, useContext, useEffect, useRef, useState } from "preact/hooks";
import { Button, Presentation, errorMessage } from "./primitives.jsx";

const choices = [
  {
    key: "vencord",
    name: "Vencord",
    description: "A focused collection of plugins and themes for Discord.",
  },
  {
    key: "equicord",
    name: "Equicord",
    description: "Built on Vencord, with more community plugins.",
  },
];

export function useMods() {
  const [state, setState] = useState();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);
  const [action, setAction] = useState();
  const [progress, setProgress] = useState();
  const [error, setError] = useState("");
  const generation = useRef(0);
  const active = useRef(false);
  const load = useCallback(async () => {
    if (active.current) return;
    const ticket = ++generation.current;
    try {
      const next = await window.ModSettings.load();
      if (ticket !== generation.current) return;
      if (next.error) throw new Error(next.error);
      setState(next);
      setError("");
      setBusy(Boolean(next.busy));
      setProgress(next.progress);
    } catch (failure) {
      if (ticket === generation.current) setError(errorMessage(failure));
    }
  }, []);
  useEffect(() => {
    void load();
    window.ModSettings.onProgress(setProgress);
  }, []);
  useEffect(() => {
    document.body.dataset.modBusy = String(busy);
    window.dispatchEvent(new Event("dpe-mod-busy"));
  }, [busy]);
  const apply = async (choice, complete = false) => {
    if (active.current || busy || !state) return false;
    const focused = document.activeElement;
    active.current = true;
    generation.current++;
    if (complete) setPending(true);
    else setBusy(true);
    setAction(complete ? undefined : choice);
    setError("");
    setProgress(undefined);
    try {
      const next = await (complete
        ? window.ModSettings.complete()
        : window.ModSettings.apply(choice));
      if (next.installed) setState(next);
      if (next.error) throw new Error(next.error);
      return true;
    } catch (failure) {
      setError(errorMessage(failure));
      return false;
    } finally {
      active.current = false;
      if (complete) setPending(false);
      else setBusy(false);
      setAction(undefined);
      setProgress(undefined);
      requestAnimationFrame(() => {
        if (focused?.isConnected && !focused.disabled) focused.focus({ preventScroll: true });
      });
    }
  };
  return { state, busy, pending, action, progress, error, load, apply };
}

export function ModRow({ mod, model }) {
  const { theme } = useContext(Presentation);
  const installed = model.state?.installed === mod.key;
  const pending = model.busy && model.action === (installed ? "none" : mod.key);
  const label = pending
    ? installed
      ? "Uninstalling…"
      : "Installing…"
    : installed
      ? "Uninstall"
      : "Install";
  const image =
    mod.key === "vencord" && theme !== "light" ? "vencord-dark-logo.png" : `${mod.key}-logo.png`;
  return (
    <div class="mod-row" data-mod={mod.key}>
      <img class="mod-logo" src={image} alt="" aria-hidden="true" />
      <div class="mod-copy">
        <h4>
          {mod.name}
          <span class="mod-version">
            {installed && model.state.installedVersion ? ` (${model.state.installedVersion})` : ""}
          </span>
        </h4>
        <p class="hint">{mod.description}</p>
      </div>
      <Button
        id={`mods-install-${mod.key}`}
        className="mod-install"
        data-install-mod={mod.key}
        data-installed={String(installed)}
        aria-label={`${label.replace("…", "")} ${mod.name}`}
        disabled={model.busy || model.pending || !model.state}
        icon={installed ? "trash" : "download"}
        onClick={() => model.apply(installed ? "none" : mod.key)}
      >
        <span class="mod-action-label">{label}</span>
      </Button>
    </div>
  );
}

export function ModProgress({ model }) {
  const { progress, busy } = model;
  const determinate =
    Number.isFinite(progress?.current) && Number.isFinite(progress?.total) && progress.total > 0;
  const value = determinate
    ? Math.min(100, Math.max(0, (progress.current / progress.total) * 100))
    : undefined;
  return (
    <div class="mod-progress-slot" aria-live="polite">
      <div class="mod-progress" hidden={!busy}>
        <p id="mods-progress-label" class="hint">
          {progress?.label ||
            (model.action === "none"
              ? "Preparing uninstall…"
              : model.action === "complete"
                ? "Finishing setup…"
                : "Preparing installation…")}
        </p>
        <div
          class="mod-progress-track"
          role="progressbar"
          aria-labelledby="mods-progress-label"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-valuenow={value}
          data-indeterminate={String(!determinate)}
        >
          <span style={determinate ? { width: `${value}%` } : undefined} />
        </div>
      </div>
    </div>
  );
}

export function Mods({ model, welcome = false, active = true }) {
  return (
    <section
      id="mods-panel"
      hidden={!active}
      role={welcome ? undefined : "tabpanel"}
      aria-labelledby={welcome ? "welcome-title" : "t_mods"}
    >
      <section class="mods-section" aria-labelledby="mods-section-title">
        <h3 id="mods-section-title" class="section-label">
          {welcome ? "Choose a client mod" : "Client mods"}
        </h3>
        <div id="mods-choices" class="mod-list" aria-busy={String(model.busy || !model.state)}>
          {choices.map((mod) => (
            <ModRow key={mod.key} mod={mod} model={model} />
          ))}
        </div>
        <p id="mods-preserved" class="hint">
          Updates preserve your installed mod.
        </p>
      </section>
      <p
        id="mods-state"
        class="hint"
        role="status"
        hidden={welcome || !model.state?.restartRequired}
      >
        Restart Discord to apply your changes.
      </p>
      <p id="mods-error" role="alert" hidden={!model.error}>
        {model.error}
      </p>
      {!model.state && model.error && (
        <Button id="mods-retry" onClick={model.load}>
          Retry
        </Button>
      )}
      <ModProgress model={model} />
    </section>
  );
}
