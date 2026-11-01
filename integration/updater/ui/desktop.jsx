/** @jsxImportSource preact */
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import {
  Button,
  Disclosure,
  Icon,
  InlineBullet,
  Select,
  Switch,
  errorMessage,
} from "./primitives.jsx";
import { createSpellingState } from "./spelling-state.cjs";

const spelling = createSpellingState({
  spellcheckLoad: (refresh) => window.Native.spellcheckLoad(refresh),
  spellcheckSave: (value) => window.Native.spellcheckSave(value),
});

function languageName(code) {
  try {
    // Linux may expose the POSIX C locale as "c", which is not a BCP 47
    // language tag. Ignore invalid preferences instead of losing every label.
    const locales = navigator.languages.filter((locale) => {
      try {
        return Intl.getCanonicalLocales(locale).length > 0;
      } catch {
        return false;
      }
    });
    return new Intl.DisplayNames([...locales, "en"], { type: "language" }).of(code);
  } catch {
    return code;
  }
}

export function Spellcheck() {
  const [{ value: state, busy, refreshing, error, message }, setSnapshot] = useState(
    spelling.getSnapshot,
  );
  const [query, setQuery] = useState("");
  const [onlyEnabled, setOnlyEnabled] = useState(false);
  const load = spelling.load;
  // Cached controls are interactive on the first paint after returning to the
  // tab, so subscribe before paint rather than waiting for a passive effect.
  useLayoutEffect(() => spelling.subscribe(setSnapshot), []);
  useEffect(() => {
    void load();
    const revalidate = () => {
      if (!document.hidden) void load();
    };
    const timer = setInterval(revalidate, 4000);
    document.addEventListener("visibilitychange", revalidate);
    window.addEventListener("focus", revalidate);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", revalidate);
      window.removeEventListener("focus", revalidate);
    };
  }, []);
  const automatic = state?.useSystemLanguages ?? true;
  const selected = state ? (automatic ? state.languages : state.configuredLanguages) : [];
  const inactive = !state?.enabled && selected.length > 0;
  const save = (enabled, languages, useSystemLanguages = automatic) => {
    if (useSystemLanguages || !languages.length) {
      setQuery("");
      setOnlyEnabled(false);
    }
    return spelling.save({ enabled, languages, useSystemLanguages });
  };
  const downloads = new Map(
    state?.downloads.map(({ language, state: status }) => [language, status]),
  );
  const filtered =
    state?.availableLanguages.filter((language) => {
      const search = query.trim().toLocaleLowerCase();
      return (
        (!onlyEnabled || selected.includes(language)) &&
        (!search || `${languageName(language)} ${language}`.toLocaleLowerCase().includes(search))
      );
    }) || [];
  // The disabled system-mode list still explains which dictionaries are used.
  // Keep manual ordering stable while users toggle and search languages.
  if (automatic)
    filtered.sort((a, b) => Number(selected.includes(b)) - Number(selected.includes(a)));
  return (
    <section class="spelling-settings" aria-label="Spelling settings">
      {!state && !error && (
        <p role="status" class="hint">
          Loading spelling settings…
        </p>
      )}
      {state && (
        <>
          <Switch
            id="spellcheck-enabled"
            label="Check spelling as you type"
            description="Show spelling suggestions while you write."
            checked={state.enabled}
            disabled={busy}
            onChange={(enabled) =>
              save(
                enabled,
                state.configuredLanguages,
                automatic || (enabled && !state.configuredLanguages.length),
              )
            }
          />
          <Switch
            id="spellcheck-automatic"
            label="Use system languages"
            description="Use your system’s preferred languages, or turn this off to choose your own."
            checked={automatic}
            disabled={busy || inactive}
            onChange={(value) =>
              save(
                state.enabled,
                value ? [] : selected.length ? selected : [state.availableLanguages[0]],
                value,
              )
            }
          />
          <fieldset class="spelling-languages" disabled={busy || automatic || inactive}>
            <legend class="spelling-heading">Languages</legend>
            <div class="spelling-tools">
              <div class="spelling-search dpe-input">
                <Icon name="search" />
                <input
                  id="spellcheck-search"
                  type="search"
                  placeholder="Search languages"
                  aria-label="Search languages"
                  value={query}
                  onInput={(event) => setQuery(event.currentTarget.value)}
                />
                <button
                  type="button"
                  class="spelling-clear-search"
                  aria-label="Clear language search"
                  hidden={!query}
                  onClick={() => {
                    setQuery("");
                    document.getElementById("spellcheck-search").focus();
                  }}
                >
                  <Icon name="clear" />
                </button>
              </div>
              <Button
                id="spellcheck-filter"
                icon={onlyEnabled ? "check" : undefined}
                variant={onlyEnabled ? "primary" : "secondary"}
                aria-pressed={String(onlyEnabled)}
                onClick={() => setOnlyEnabled((value) => !value)}
              >
                Only enabled
              </Button>
              <Button
                id="spellcheck-refresh"
                icon="refresh"
                variant="icon"
                aria-label="Refresh dictionary status"
                title="Refresh dictionary status"
                disabled={refreshing}
                onClick={() => load(true)}
              />
            </div>
            <div class="spelling-language-list" aria-label="Spelling languages">
              {filtered.map((language) => {
                const status = downloads.get(language);
                const ready = status === "ready" || status === "cached";
                const description =
                  status === "failed"
                    ? "Download failed"
                    : status === "downloading"
                      ? "Downloading…"
                      : ready
                        ? "Downloaded"
                        : "Downloads when needed";
                return (
                  <div class="spelling-language" data-language={language} key={language}>
                    <Switch
                      id={`spellcheck-language-${language}`}
                      label={
                        <>
                          {languageName(language)}
                          <span class="spelling-language-code"> ({language})</span>
                        </>
                      }
                      value={language}
                      description={
                        <span class="spelling-dictionary-status" data-state={status || "unknown"}>
                          {ready && <Icon name="check" />}
                          {description}
                        </span>
                      }
                      checked={selected.includes(language)}
                      disabled={busy || automatic || inactive}
                      onChange={(value) => {
                        const next = value
                          ? [...selected, language]
                          : selected.filter((item) => item !== language);
                        void save(next.length > 0, next, false);
                      }}
                    />
                  </div>
                );
              })}
              {!filtered.length && (
                <p class="hint" role="status">
                  No languages match your search.
                </p>
              )}
            </div>
          </fieldset>
        </>
      )}
      <p id="spellcheck-status" class="sr-only" role="status">
        {message || (busy ? "Saving spelling settings…" : "")}
      </p>
      <p
        id="spellcheck-error"
        role="alert"
        hidden={!error && !state?.error && !state?.dictionaryError}
      >
        {error || state?.error || state?.dictionaryError}
      </p>
      {!state && error && <Button onClick={() => load(true)}>Try again</Button>}
    </section>
  );
}

export function TrayIcons() {
  const [state, setState] = useState();
  const icons = state?.icons;
  const customized = icons?.some((icon) => icon.custom || icon.preset !== state.preset);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [presetError, setPresetError] = useState("");
  const [feedback, setFeedback] = useState({});
  const current = useRef(false);
  const timers = useRef(new Map());
  const changing = useRef(false);
  useEffect(() => {
    current.current = true;
    window.Native.trayLoad()
      .then((value) => {
        if (current.current) setState(value);
      })
      .catch((failure) => {
        if (current.current) setError(errorMessage(failure));
      });
    return () => {
      current.current = false;
      for (const timer of timers.current.values()) clearTimeout(timer);
    };
  }, []);
  const report = (id, message, failed = false) => {
    clearTimeout(timers.current.get(id));
    setFeedback((previous) => ({ ...previous, [id]: { message, failed } }));
    timers.current.set(
      id,
      setTimeout(
        () => {
          setFeedback((previous) => {
            const next = { ...previous };
            delete next[id];
            return next;
          });
          timers.current.delete(id);
        },
        failed ? 8000 : 3000,
      ),
    );
  };
  const change = async (icon, reset) => {
    if (changing.current) return;
    changing.current = true;
    clearTimeout(timers.current.get(icon.id));
    setFeedback((previous) => ({
      ...previous,
      [icon.id]: { message: reset ? "Restoring preset…" : "Choosing image…" },
    }));
    setBusy(icon.id);
    try {
      const next = await (reset
        ? window.Native.trayReset(icon.id)
        : window.Native.trayChoose(icon.id));
      if (!current.current) return;
      setState(next);
      const updated = next.icons.find((item) => item.id === icon.id);
      if (reset || updated?.preview !== icon.preview || updated?.custom !== icon.custom)
        report(icon.id, reset ? `${updated.source} restored.` : "Image updated.");
      else setFeedback((previous) => ({ ...previous, [icon.id]: undefined }));
    } catch (failure) {
      if (current.current) report(icon.id, errorMessage(failure), true);
    } finally {
      changing.current = false;
      if (current.current) setBusy("");
    }
  };
  const selectPreset = async (preset) => {
    if (changing.current) return;
    changing.current = true;
    const previous = state;
    setState({ ...state, preset });
    setBusy("preset");
    setPresetError("");
    for (const timer of timers.current.values()) clearTimeout(timer);
    timers.current.clear();
    setFeedback({});
    try {
      const next = await window.Native.trayPreset(preset);
      if (current.current) setState(next);
    } catch (failure) {
      if (current.current) {
        setState(previous);
        setPresetError(errorMessage(failure));
      }
    } finally {
      changing.current = false;
      if (current.current) setBusy("");
    }
  };
  const selectIconPreset = async (icon, preset) => {
    if (changing.current) return;
    changing.current = true;
    setBusy(icon.id);
    clearTimeout(timers.current.get(icon.id));
    setFeedback((previous) => ({ ...previous, [icon.id]: undefined }));
    try {
      const next = await window.Native.trayStatePreset(icon.id, preset);
      if (current.current) setState(next);
    } catch (failure) {
      if (current.current) report(icon.id, errorMessage(failure), true);
    } finally {
      changing.current = false;
      if (current.current) setBusy("");
    }
  };
  return (
    <Disclosure
      id="tray-icons"
      className="tray-settings"
      label="Tray Icons"
      expandedLabel="Hide Tray Icons"
    >
      {!icons && !error && (
        <p role="status" class="hint">
          Loading tray icons…
        </p>
      )}
      {state && (
        <>
          <Select
            id="tray-icon-preset"
            label="Icon preset"
            value={customized ? "" : state.preset}
            placeholder="Custom"
            options={state.presets}
            disabled={Boolean(busy)}
            onChange={selectPreset}
          />
          <p id="tray-preset-error" role="alert" hidden={!presetError}>
            {presetError}
          </p>
        </>
      )}
      {icons?.map((icon) => (
        <div class="tray-icon-row" data-tray-state={icon.id} key={icon.id}>
          <div class="tray-icon-label">
            {icon.preview && (
              <img src={icon.preview} alt="" title={icon.source} width="32" height="32" />
            )}
            <div>
              <span>{icon.label}</span>
              <div
                class="hint tray-icon-presets"
                role="group"
                aria-label={`Preset for ${icon.label.toLowerCase()} tray icon`}
                hidden={Boolean(feedback[icon.id])}
              >
                {state.presets.map(([preset, label], index) => (
                  <span class="tray-icon-preset-option" key={preset}>
                    {index > 0 && <InlineBullet />}
                    <button
                      type="button"
                      class="inline-link"
                      data-tray-preset={preset}
                      aria-pressed={icon.preset === preset}
                      aria-disabled={Boolean(busy)}
                      onClick={() => selectIconPreset(icon, preset)}
                    >
                      {label}
                    </button>
                  </span>
                ))}
              </div>
              <p
                class="hint tray-icon-status"
                role="status"
                aria-live={feedback[icon.id]?.failed ? "assertive" : "polite"}
                data-error={String(Boolean(feedback[icon.id]?.failed))}
                hidden={!feedback[icon.id]}
              >
                {feedback[icon.id]?.message}
              </p>
            </div>
          </div>
          <div class="tray-icon-actions">
            <Button
              disabled={Boolean(busy)}
              aria-label={`Choose image for ${icon.label.toLowerCase()} tray icon`}
              onClick={() => change(icon, false)}
            >
              Choose image
            </Button>
            <Button
              disabled={Boolean(busy) || !icon.overridden}
              aria-label={`Reset ${icon.label.toLowerCase()} tray icon`}
              onClick={() => change(icon, true)}
            >
              Reset
            </Button>
          </div>
        </div>
      ))}
      <p role="alert" hidden={!error}>
        {error}
      </p>
    </Disclosure>
  );
}
