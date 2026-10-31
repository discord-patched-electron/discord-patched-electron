/** @jsxImportSource preact */
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { Icon, Versions, errorMessage } from "./primitives.jsx";
import { General, Appearance, Diagnostics } from "./pages.jsx";
import { Flags } from "./flags.jsx";
import { Mods } from "./mods.jsx";

const tabs = [
  ["settings", "General", "settings"],
  ["theming", "Appearance", "appearance"],
  ["mods", "Mods", "mods"],
  ["flags", "Flags", "flags"],
  ["troubleshooting", "Troubleshooting", "troubleshooting"],
];
const projectLinks = [
  ["github", "GitHub", "https://github.com/discord-patched-electron/discord-patched-electron"],
  [
    "releases",
    "Release notes",
    "https://github.com/discord-patched-electron/discord-patched-electron/releases",
  ],
  [
    "issues",
    "Report an issue",
    "https://github.com/discord-patched-electron/discord-patched-electron/issues",
  ],
];

export function ProjectMenu({ onError }) {
  const trigger = useRef();
  const popup = useRef();
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => {
    if (open) {
      const rect = trigger.current.getBoundingClientRect();
      Object.assign(popup.current.style, {
        left: `${rect.left}px`,
        top: `${rect.bottom + 4}px`,
        minWidth: `${rect.width}px`,
      });
      popup.current.showPopover();
      popup.current.querySelector("a").focus();
    } else popup.current.hidePopover();
  }, [open]);
  useEffect(() => {
    const dismiss = (event) => {
      if (!popup.current.contains(event.target) && !trigger.current.contains(event.target))
        setOpen(false);
    };
    const focus = (event) => {
      if (event.key === "Tab") setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", focus);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", focus);
    };
  }, []);
  const keydown = (event) => {
    const items = [...popup.current.querySelectorAll("a")];
    let next;
    if (event.key === "ArrowDown")
      next = (items.indexOf(document.activeElement) + 1) % items.length;
    if (event.key === "ArrowUp")
      next = (items.indexOf(document.activeElement) + items.length - 1) % items.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      items[next].focus();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      trigger.current.focus();
    }
  };
  const follow = async (event, name) => {
    event.preventDefault();
    setOpen(false);
    onError("");
    try {
      await window.Native.openLink(name);
    } catch (failure) {
      onError(errorMessage(failure));
    }
  };
  return (
    <div class="project-header">
      <button
        ref={trigger}
        class="project-identity"
        id="project-menu-trigger"
        type="button"
        aria-haspopup="menu"
        aria-expanded={String(open)}
        aria-controls="project-menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span class="project-avatar">
          <img src="dpe-logo.png" alt="" aria-hidden="true" />
        </span>
        <span class="project-name">
          DPE<span class="project-caption">Project links</span>
        </span>
        <Icon name="chevron" />
      </button>
      <div
        ref={popup}
        id="project-menu"
        class="project-menu"
        role="menu"
        popover="manual"
        onKeyDown={keydown}
      >
        {projectLinks.map(([name, label, href]) => (
          <a
            key={name}
            id={name === "github" ? "config-github" : undefined}
            role="menuitem"
            href={href}
            data-project-link={name}
            onClick={(event) => follow(event, name)}
          >
            {label}
            <Icon name="external" />
          </a>
        ))}
      </div>
    </div>
  );
}

export function SettingsNavigation({ tab, compact = false, onSelect }) {
  const navigation = useRef();
  const navigate = (event, index) => {
    const rtl = getComputedStyle(document.documentElement).direction === "rtl";
    let next;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (event.key === "ArrowDown") next = index + 1;
    if (event.key === "ArrowUp") next = index - 1;
    if (event.key === "ArrowRight") next = index + (rtl ? -1 : 1);
    if (event.key === "ArrowLeft") next = index + (rtl ? 1 : -1);
    if (next === undefined) return;
    event.preventDefault();
    const name = tabs[(next + tabs.length) % tabs.length][0];
    navigation.current.querySelector(`[data-tab="${name}"]`).focus();
    onSelect(name);
  };
  return (
    <nav
      ref={navigation}
      role="tablist"
      aria-label="DPE Settings"
      aria-orientation={compact ? "horizontal" : "vertical"}
    >
      {tabs.map(([name, label, icon], index) => (
        <button
          key={name}
          type="button"
          id={`t_${name}`}
          data-tab={name}
          role="tab"
          aria-selected={String(tab === name)}
          aria-controls={["settings", "theming"].includes(name) ? "container" : `${name}-panel`}
          tabIndex={tab === name ? 0 : -1}
          class={`dpe-nav-item${tab === name ? " active" : ""}`}
          onClick={() => onSelect(name)}
          onKeyDown={(event) => navigate(event, index)}
        >
          <Icon name={icon} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

export function SettingsShell({ model, error, onError }) {
  const [tab, setTab] = useState("settings");
  const [compact, setCompact] = useState(matchMedia("(max-width: 440px)").matches);
  const [config] = useState(() => window.Native.get());
  const [onboarding] = useState(() => Boolean(window.Native.isOnboardingSettings?.()));
  const select = (name) => {
    if (tabs.some(([key]) => key === name)) setTab(name);
  };
  useLayoutEffect(() => {
    window.Native.onTab(select);
    window.openConfigLink = async (name) => {
      onError("");
      try {
        await window.Native.openLink(name);
      } catch (failure) {
        onError(errorMessage(failure));
      }
    };
    window.Native.onSetupReminder?.(onError);
    window.Native.onSetupError?.(onError);
  }, []);
  useLayoutEffect(() => {
    document.body.dataset.settingsTab = tab;
    document.querySelector("main").scrollTop = 0;
    document
      .querySelectorAll("select")
      .forEach((input) => window.DPEControls.upgradeSelect(input).close());
    if (tab === "mods") void model.load();
  }, [tab]);
  useEffect(() => {
    const media = matchMedia("(max-width: 440px)");
    const changed = () => setCompact(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  return (
    <div class="settings-layout">
      <aside class="settings-sidebar" aria-label="Settings navigation">
        <ProjectMenu onError={onError} />
        <SettingsNavigation tab={tab} compact={compact} onSelect={select} />
        <div class="sidebar-bottom">
          {onboarding && (
            <p id="onboarding-close-note" class="hint">
              Closing settings will launch Discord.
            </p>
          )}
          <button id="b_restart" type="button" class="dpe-nav-item">
            Restart Discord
            <Icon name="refresh" />
          </button>
          <Versions modState={model.state} />
        </div>
      </aside>
      <div class="settings-content">
        <header class="content-header">
          <h2 id="section-title">{tabs.find(([name]) => name === tab)[1]}</h2>
        </header>
        <main tabIndex="-1">
          <p id="external-link-error" role="alert" hidden={!error}>
            {error}
          </p>
          <div
            id="container"
            role="tabpanel"
            aria-labelledby={`t_${tab}`}
            hidden={!["settings", "theming"].includes(tab)}
          >
            {tab === "settings" && <General config={config} />}
            {tab === "theming" && <Appearance config={config} />}
          </div>
          <Flags active={tab === "flags"} />
          <Mods model={model} active={tab === "mods"} />
          <Diagnostics select={select} active={tab === "troubleshooting"} />
        </main>
        <p id="settings-save-note" class="sr-only" role="status">
          {tab === "flags"
            ? "Save flag changes before restarting Discord."
            : tab === "mods"
              ? "Restart Discord after changing your client mod."
              : "Changes save automatically. Restart Discord to apply."}
        </p>
      </div>
    </div>
  );
}
