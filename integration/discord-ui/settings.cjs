"use strict";
// Discord owns rendering and navigation for the sidebar layout node.
module.exports = function settingsEntry(request, ui) {
  const open = () => DiscordNative.ipc.send("DISCORD_UPDATED_QUOTES", "o");
  // The native footer is private to the settings component. Reuse only its
  // version span's presentation, outside the copy-build-info click target.
  // Observe mounts instead of polling or cloning account/profile content.
  if (globalThis.document?.documentElement && typeof MutationObserver === "function") {
    const key = Symbol.for("dpe.discordSettingsFooter");
    let footer = globalThis[key];
    if (!footer) {
      const entries = new WeakMap();
      const focusStyle = document.createElement("style");
      focusStyle.textContent =
        "[data-dpe-settings-version]:focus-visible { outline: 2px solid var(--border-focus, var(--focus-primary, #00b0f4)); outline-offset: 2px; }";
      footer = {
        version: "Development",
        refresh() {
          for (const info of document.querySelectorAll('[class*="compactInfo"]')) {
            const sidebar = info.closest('[class*="sidebar"]');
            const template = info.firstElementChild;
            if (
              !sidebar?.querySelector('[data-list-item-id^="settings-sidebar___"]') ||
              template?.tagName !== "SPAN" ||
              !template.querySelector('[class*="versionHash"]')
            )
              continue;
            const anchor = info.closest('[role="button"], button') || info;
            if (!sidebar.contains(anchor.parentElement)) continue;
            // Tooltip measures the sibling immediately before its hidden marker.
            // Keep the copy target, marker and description together so its hover
            // and Copied feedback remain anchored to Discord's version text.
            let insertion = anchor;
            const descriptions = new Set(
              (anchor.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean),
            );
            for (
              let sibling = anchor.nextElementSibling;
              sibling;
              sibling = sibling.nextElementSibling
            ) {
              if (sibling.hasAttribute("data-dpe-settings-version")) continue;
              if (
                descriptions.has(sibling.id) ||
                (sibling.tagName === "SPAN" && sibling.style.display === "none")
              )
                insertion = sibling;
              else break;
            }
            if (!focusStyle.isConnected)
              (document.head || document.documentElement).append(focusStyle);
            let button = entries.get(anchor);
            if (!button) {
              button = document.createElement("button");
              button.type = "button";
              button.setAttribute("data-dpe-settings-version", "");
              button.onclick = open;
              entries.set(anchor, button);
            }
            const label = `Discord Patched Electron ${footer.version}`;
            if (button.textContent !== label) button.textContent = label;
            button.setAttribute("aria-label", `${label}, open DPE Settings`);
            button.className = template.className;
            button.setAttribute("style", template.getAttribute("style") || "");
            Object.assign(button.style, {
              display: "block",
              background: "none",
              border: "0",
              padding: "0",
              margin: "0",
              marginBlockStart: "var(--space-4, 4px)",
              width: "fit-content",
              textAlign: "start",
              cursor: "default",
            });
            for (const duplicate of anchor.parentElement.querySelectorAll(
              ":scope > [data-dpe-settings-version]",
            ))
              if (duplicate !== button) duplicate.remove();
            if (insertion.nextElementSibling !== button) insertion.after(button);
          }
        },
      };
      globalThis[key] = footer;
      footer.observer = new MutationObserver(() => footer.refresh());
      footer.observer.observe(document.documentElement, { childList: true, subtree: true });
    }
    if (typeof request?.version === "string" && request.version.trim())
      footer.version = request.version.trim().slice(0, 128);
    footer.refresh();
  }
  const installed = new WeakSet();
  ui.watch(
    "dpe-openasar-settings",
    (source) => source.includes("LOGOUT_SIDEBAR_ITEM") && /["']user_settings["']/.test(source),
    (exports, runtime) => {
      const scan = ui.inspect({ c: { settings: { exports } } });
      const root = scan.objects.find(
        (value) =>
          ui.safe(value, "key") === "$Root" &&
          ui.safe(value, "analyticsKey") === "user_settings" &&
          typeof ui.safe(value, "buildLayout") === "function",
      );
      if (!root || installed.has(root)) return;
      const icon = ui
        .inspect(runtime)
        .choose(
          "SettingsIcon",
          (signature, names) => names.has("SettingsIcon") && ui.has(signature, "svg"),
        );
      if (!icon) return;
      const build = root.buildLayout;
      root.buildLayout = function (...args) {
        const layout = Reflect.apply(build, this, args);
        if (!Array.isArray(layout)) return layout;
        return layout.map((section) => {
          if (section?.key !== "utility_section" || typeof section.buildLayout !== "function")
            return section;
          return {
            ...section,
            buildLayout(...args) {
              const items = Reflect.apply(section.buildLayout, section, args);
              if (!Array.isArray(items) || items.some((item) => item?.key === "dpe_openasar"))
                return items;
              const index = items.findIndex((item) => item?.key === "logout_sidebar_item");
              if (index < 0) return items;
              const entry = {
                key: "dpe_openasar",
                type: items[index].type,
                useTitle: () => "DPE Settings",
                icon,
                onClick: open,
                buildLayout: () => [],
              };
              return [...items.slice(0, index), entry, ...items.slice(index)];
            },
          };
        });
      };
      installed.add(root);
    },
  );
};
