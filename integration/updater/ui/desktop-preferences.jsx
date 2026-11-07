/** @jsxImportSource preact */
import { useEffect, useRef, useState } from "preact/hooks";
import { Button, InlineBullet, Switch, errorMessage } from "./primitives.jsx";

export function useDesktopPreferences() {
  const [state, setState] = useState();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const changing = useRef(false);
  const current = useRef(true);
  const load = async () => {
    try {
      const next = await window.Native.desktopLoad();
      if (current.current) {
        setState(next);
        setError("");
      }
    } catch (failure) {
      if (current.current) setError(errorMessage(failure));
    }
  };
  useEffect(() => {
    current.current = true;
    void load();
    return () => {
      current.current = false;
    };
  }, []);
  const run = async (request, patch = {}) => {
    if (changing.current || !state) return;
    changing.current = true;
    const previous = state;
    setState({ ...state, values: { ...state.values, ...patch } });
    setBusy(true);
    setError("");
    try {
      const next = await request();
      if (current.current) {
        setState(next);
        setError(next.error || "");
      }
    } catch (failure) {
      if (current.current) {
        setState(previous);
        setError(errorMessage(failure));
      }
    } finally {
      changing.current = false;
      if (current.current) setBusy(false);
    }
  };
  const save = (patch) => run(() => window.Native.desktopSave(patch), patch);
  const claimLinks = () => run(window.Native.desktopClaimLinks);
  return { state, busy, error, load, save, claimLinks };
}

export const discordLinkSwitch = {
  label: "Open Discord links",
  description: "Use this app for discord:// links from your browser and other apps.",
  elsewhere: "Another app opens Discord links right now.",
};

export function LinkPreferences({ model }) {
  return (
    <section class="settings-group" aria-labelledby="desktop-links-heading">
      <h3 class="section-label" id="desktop-links-heading">
        Links
      </h3>
      <DesktopSwitch
        model={model}
        name="discordLinks"
        {...discordLinkSwitch}
        description={
          !model.state?.linksElsewhere ? (
            discordLinkSwitch.description
          ) : !model.state.values.discordLinks ? (
            discordLinkSwitch.elsewhere
          ) : (
            <>
              {discordLinkSwitch.elsewhere}
              <InlineBullet />
              <button
                type="button"
                class="inline-link"
                disabled={model.busy}
                onClick={model.claimLinks}
              >
                Use this app
              </button>
            </>
          )
        }
      />
    </section>
  );
}

export function DesktopSwitch({ model, name, label, description, disabled = false, checked }) {
  return (
    <Switch
      id={`desktop-${name}`}
      label={label}
      description={description}
      checked={checked ?? model.state?.values[name] ?? false}
      disabled={!model.state || model.busy || disabled}
      onChange={(value) => model.save({ [name]: value })}
    />
  );
}

export function DesktopError({ model }) {
  return (
    <>
      <p id="desktop-preferences-error" role="alert" hidden={!model.error}>
        {model.error}
      </p>
      {!model.state && model.error && <Button onClick={model.load}>Try again</Button>}
    </>
  );
}

export function WindowPreferences({ model }) {
  const values = model.state?.values;
  const native = Boolean(values?.nativeTitlebar);
  const controls = !native && Boolean(values?.windowControls);
  return (
    <section
      class="settings-group desktop-window-settings"
      aria-labelledby="desktop-window-heading"
    >
      <h3 class="section-label" id="desktop-window-heading">
        Window
      </h3>
      <DesktopSwitch model={model} name="nativeTitlebar" label="Native titlebar" />
      <DesktopSwitch
        model={model}
        name="windowControls"
        label="Discord window controls"
        disabled={native}
        checked={controls}
      />
      <fieldset
        class="desktop-window-buttons"
        disabled={!controls || model.busy}
        aria-label="Discord window buttons"
      >
        <DesktopSwitch
          model={model}
          name="minimizeButton"
          label="Minimize button"
          disabled={!controls}
          checked={controls && Boolean(values?.minimizeButton)}
        />
        <DesktopSwitch
          model={model}
          name="maximizeButton"
          label="Maximize button"
          disabled={!controls}
          checked={controls && Boolean(values?.maximizeButton)}
        />
        <DesktopSwitch
          model={model}
          name="closeButton"
          label="Close button"
          disabled={!controls}
          checked={controls && Boolean(values?.closeButton)}
        />
      </fieldset>
      <DesktopSwitch model={model} name="minimumWindowSize" label="Enforce minimum window size" />
    </section>
  );
}

export function NotificationPreferences({ model }) {
  return (
    <section class="settings-group" aria-labelledby="desktop-notifications-heading">
      <h3 class="section-label" id="desktop-notifications-heading">
        Notifications
      </h3>
      <DesktopSwitch
        model={model}
        name="notificationFlash"
        label="Flash taskbar for notifications"
        description="Draw attention to Discord in the taskbar when a notification arrives."
      />
      <DesktopSwitch
        model={model}
        name="appBadge"
        label="App badge"
        description="Show an unread-message badge on Discord’s app icon."
      />
      <DesktopSwitch
        model={model}
        name="badgeOnlyMentions"
        label="Badge only for mentions"
        description="Hide the badge for unread messages without mentions."
        disabled={!model.state?.values.appBadge}
      />
    </section>
  );
}
