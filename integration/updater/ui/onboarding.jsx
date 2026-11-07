/** @jsxImportSource preact */
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Button, Switch, errorMessage } from "./primitives.jsx";
import { Mods } from "./mods.jsx";
import { discordLinkSwitch } from "./desktop-preferences.jsx";

export function Onboarding({ model }) {
  const [page, setPage] = useState(0);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [links, setLinks] = useState();
  const operation = useRef(false);
  useEffect(() => {
    let active = true;
    window.Native.desktopLoad()
      .then(({ linksDefault, linksElsewhere }) => {
        if (active)
          setLinks({
            enabled: linksDefault || !linksElsewhere,
            elsewhere: linksElsewhere,
            current: linksDefault,
          });
      })
      .catch(() => {
        if (active) setLinks({ enabled: false, elsewhere: true });
      });
    return () => {
      active = false;
    };
  }, []);
  useLayoutEffect(() => {
    document.body.dataset.welcomePage = page === 1 ? "ready" : "mods";
    return () => delete document.body.dataset.welcomePage;
  }, [page]);
  useLayoutEffect(() => {
    if (page !== 1) return;
    const copy = document.querySelector(".welcome-links");
    const updateShade = () => {
      document.body.style.setProperty(
        "--welcome-copy-bottom",
        `${Math.ceil(copy.getBoundingClientRect().bottom)}px`,
      );
    };
    updateShade();
    const observer = new ResizeObserver(updateShade);
    observer.observe(copy);
    observer.observe(copy.parentElement);
    return () => {
      observer.disconnect();
      document.body.style.removeProperty("--welcome-copy-bottom");
    };
  }, [page]);
  const next = async () => {
    if (operation.current || model.busy || model.pending) return;
    operation.current = true;
    setWorking(true);
    try {
      if (await model.apply(undefined, true)) {
        setPage(1);
        requestAnimationFrame(() => document.getElementById("welcome-title")?.focus());
      }
    } finally {
      operation.current = false;
      setWorking(false);
    }
  };
  const finish = async (action) => {
    if (operation.current || model.busy || !links) return;
    operation.current = true;
    setWorking(true);
    setError("");
    try {
      if (links.enabled) await window.Native.desktopClaimLinks();
      const result = await window.Native.setupAction(action);
      if (result?.error) throw new Error(result.error);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      operation.current = false;
      setWorking(false);
    }
  };
  const back = async () => {
    if (operation.current || model.busy) return;
    operation.current = true;
    setWorking(true);
    await model.load();
    setPage(0);
    setError("");
    operation.current = false;
    setWorking(false);
  };
  return (
    <WelcomeView
      model={model}
      page={page}
      working={working}
      error={error}
      links={links}
      setLinks={setLinks}
      next={next}
      finish={finish}
      back={back}
    />
  );
}

export function WelcomeView({ model, page, working, error, links, setLinks, next, finish, back }) {
  return (
    <div class="onboarding">
      <div class="welcome-artwork" aria-hidden="true" hidden={page !== 1}>
        <img src="welcome-background.png" alt="" />
        <div class="welcome-top-shade">
          <span class="welcome-blur welcome-blur-soft" />
          <span class="welcome-blur welcome-blur-medium" />
          <span class="welcome-blur welcome-blur-strong" />
        </div>
      </div>
      <main class="onboarding-body">
        <h2 id="welcome-title" tabIndex="-1">
          Welcome!
        </h2>
        {page === 0 ? (
          <>
            <p class="setup-intro">
              Add plugins and themes to Discord Patched Electron. You can install a mod now or
              change this later in Settings.
            </p>
            <Mods model={model} welcome />
          </>
        ) : (
          <>
            <p class="setup-intro">
              You’re ready to open Discord. You can also adjust your settings first.
            </p>
            <div class="welcome-links">
              <Switch
                id="setup-discord-links"
                label={discordLinkSwitch.label}
                description={
                  links?.current
                    ? "This app already opens Discord links."
                    : links?.elsewhere
                      ? discordLinkSwitch.elsewhere
                      : discordLinkSwitch.description
                }
                checked={links?.enabled ?? false}
                disabled={working || !links || links.current}
                onChange={(enabled) => setLinks({ ...links, enabled })}
              />
            </div>
          </>
        )}
        <p id="setup-error" role="alert" hidden={!error}>
          {error}
        </p>
      </main>
      <footer key={page} class="onboarding-actions">
        {page === 0 ? (
          <>
            <span class="hint">A client mod is optional.</span>
            <Button
              id="setup-next"
              variant="primary"
              disabled={working || model.busy || model.pending || !model.state}
              onClick={next}
            >
              Next
            </Button>
          </>
        ) : (
          <>
            <Button id="setup-back" disabled={working} onClick={back}>
              Back
            </Button>
            <div class="panel-actions">
              <Button
                id="setup-configure"
                disabled={working || model.busy || !links}
                onClick={() => finish("configure")}
              >
                Configure more
              </Button>
              <Button
                id="setup-done"
                variant="primary"
                disabled={working || model.busy || !links}
                onClick={() => finish("done")}
              >
                Restart to finish
              </Button>
            </div>
          </>
        )}
      </footer>
    </div>
  );
}
