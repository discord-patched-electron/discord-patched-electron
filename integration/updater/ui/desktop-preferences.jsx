/** @jsxImportSource preact */
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Button, Switch, errorMessage } from "./primitives.jsx";

export function useDesktopPreferences() {
  const [state, setState] = useState();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const focusAfterSave = useRef();
  useLayoutEffect(() => {
    if (busy || !focusAfterSave.current) return;
    const focused = focusAfterSave.current;
    focusAfterSave.current = undefined;
    if (
      document.activeElement !== document.body &&
      !(document.activeElement === focused && focused.disabled)
    )
      return;
    const target =
      focused.id === "desktop-discord-links" && focused.disabled
        ? document.getElementById("desktop-discord-links-help")
        : focused;
    if (target?.isConnected && !target.disabled) target.focus({ preventScroll: true });
  });
  const changing = useRef(false);
  const current = useRef(true);
  const load = async () => {
    try {
      const next = await window.Native.desktopLoad();
      if (current.current) {
        setState(next);
        setError(next.error || "");
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
    focusAfterSave.current = document.activeElement;
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
      <div class="setting-row">
        <div>
          <label for="desktop-discord-links">{discordLinkSwitch.label}</label>
          <p id="desktop-discord-links-help" class="hint" tabIndex="-1">
            {model.state?.linksDefault
              ? "This app opens Discord links. You can change this in your system’s default applications."
              : model.state?.linksElsewhere
                ? discordLinkSwitch.elsewhere
                : discordLinkSwitch.description}
          </p>
        </div>
        <Button
          id="desktop-discord-links"
          disabled={model.busy || !model.state || model.state.linksDefault}
          aria-describedby="desktop-discord-links-help"
          onClick={model.claimLinks}
        >
          {model.state?.linksDefault ? "In use" : "Use this app"}
        </Button>
      </div>
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

export function PopoutPreferences({ model }) {
  const values = model.state?.values;
  const canHide = values?.popoutFloating && values?.windowControls && !values?.nativeTitlebar;
  return (
    <section class="settings-group" aria-labelledby="desktop-popout-heading">
      <h3 class="section-label" id="desktop-popout-heading">
        Video popouts
      </h3>
      <DesktopSwitch
        model={model}
        name="popoutFloating"
        label="Float popouts by default"
        description="Ask tiling window managers to float new video and screenshare windows."
      />
      <DesktopSwitch
        model={model}
        name="popoutHideTitlebar"
        label="Hide floating popout titlebar"
        description="Discord window controls and Native titlebar take precedence."
        disabled={!canHide}
        checked={Boolean(canHide && values?.popoutHideTitlebar)}
      />
      <DesktopSwitch
        model={model}
        name="popoutHideMembers"
        label="Hide members when expanding a screenshare"
        description="Collapse the members bar for the selected stream. You can still use Show Members to reopen it."
      />
    </section>
  );
}
