/** @jsxImportSource preact */
import { render } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import "../settings-controls.js";
import flagEditor from "../flag-editor.js";
import { Button, fallback, Presentation, WindowTitle } from "./primitives.jsx";
import { useMods } from "./mods.jsx";
import { SettingsShell } from "./settings-shell.jsx";
import { Onboarding } from "./onboarding.jsx";

window.DPEFallbackIcons = fallback;
window.FlagEditor = flagEditor;
const welcome = window.Native.getMode?.() === "welcome";
document.body.dataset.mode = welcome ? "welcome" : "settings";
document.title = welcome ? "Welcome to DPE" : "DPE Settings";

function DiscardFlags() {
  const dialog = useRef();
  useLayoutEffect(
    () => window.LaunchSettings.onConfirmDiscard(() => dialog.current.showModal()),
    [],
  );
  const finish = (discard) => {
    dialog.current.close();
    window.LaunchSettings.discardDecision(discard);
  };
  return (
    <dialog
      ref={dialog}
      class="dpe-settings-confirmation"
      aria-labelledby="discard-flags-title"
      onKeyDown={(event) => {
        if (event.key === "Escape") event.stopPropagation();
      }}
      onCancel={(event) => {
        event.preventDefault();
        finish(false);
      }}
    >
      <h2 id="discard-flags-title">Discard your unsaved flags?</h2>
      <footer>
        <Button autofocus onClick={() => finish(false)}>
          Keep editing
        </Button>
        <Button variant="primary" onClick={() => finish(true)}>
          Discard changes
        </Button>
      </footer>
    </dialog>
  );
}

function App() {
  const [presentation, setPresentation] = useState({
    icons: fallback,
    versions: {},
    theme: document.body.dataset.dpeTheme || "dark",
  });
  const [error, setError] = useState("");
  const model = useMods();
  useLayoutEffect(() => {
    const ready = async () => {
      try {
        if (await window.Native.ready?.()) document.body.dataset.presentationReady = "true";
      } catch {
        // Closing the window can dispose its frame while readiness is in flight.
      }
    };
    const update = (value) => {
      setPresentation(value);
      window.DPEControls.updateIcons(value.icons);
    };
    window.Native.onPresentation(update);
    void window.Native.presentation()
      .then(async (value) => {
        update(value);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        await Promise.allSettled([...document.images].map((image) => image.decode()));
        await document.fonts.ready;
        await ready();
      })
      .catch(ready);
  }, []);
  useLayoutEffect(() => {
    const keyboard = (event) => {
      if (event.key === "Escape" && !event.defaultPrevented && !model.busy)
        void window.Native.windowAction("close").catch((failure) => setError(String(failure)));
    };
    // Mod setup is guarded by BrowserWindow's main-process close handler. A
    // renderer beforeunload guard can lag behind a completed operation and
    // incorrectly trigger the Settings editor's unsaved-flags prompt.
    window.addEventListener("keydown", keyboard);
    return () => {
      window.removeEventListener("keydown", keyboard);
    };
  }, [model.busy]);
  return (
    <Presentation.Provider value={presentation}>
      <WindowTitle welcome={welcome} busy={model.busy} onError={setError} />
      {welcome ? (
        <Onboarding model={model} />
      ) : (
        <>
          <SettingsShell model={model} error={error} onError={setError} />
          <DiscardFlags />
        </>
      )}
    </Presentation.Provider>
  );
}

render(<App />, document.getElementById("app"));
