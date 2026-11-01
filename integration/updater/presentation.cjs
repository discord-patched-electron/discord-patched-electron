"use strict";
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const preferences = require("./profile.cjs");
const source = require("../discord-ui/source.cjs");
const capture = require("../discord-ui/presentation.cjs");
const native = require("./native-presentation.cjs");
const hosts = new Set(["discord.com", "canary.discord.com", "ptb.discord.com"]);
const tokenName =
  /^--(?:(?:dpe-discord-|switch-|scrollbar-|background|bg-|text-|header-|interactive-|button-|input-|control-|border-|brand-|primary-|white|black|red-|green-|yellow-|status-|focus-|font-|radius-|spacing-|space-|size-|custom-theme|neutral-|opacity-)[a-zA-Z0-9_-]*|custom-app-top-bar-height|custom-zoom)$/;
const iconNames = new Set([
  "settings",
  "appearance",
  "mods",
  "flag",
  "troubleshooting",
  "github",
  "external",
  "download",
  "refresh",
  "applications",
  "screen",
  "check",
  "search",
  "clear",
  "trash",
  "close",
  "minimize",
  "maximize",
  "restore",
  "experimental",
  "copy",
  "chevron",
]);
const svgTags = new Set([
  "svg",
  "g",
  "path",
  "circle",
  "ellipse",
  "rect",
  "polygon",
  "polyline",
  "line",
]);
const svgAttributes = new Set([
  "viewBox",
  "d",
  "fill",
  "stroke",
  "stroke-width",
  "stroke-linecap",
  "stroke-linejoin",
  "fill-rule",
  "clip-rule",
  "opacity",
  "fill-opacity",
  "stroke-opacity",
  "cx",
  "cy",
  "r",
  "rx",
  "ry",
  "x",
  "y",
  "x1",
  "x2",
  "y1",
  "y2",
  "width",
  "height",
  "points",
  "transform",
]);

function trusted(contents) {
  try {
    const url = new URL(contents.getURL());
    return (
      !contents.isDestroyed() &&
      !native.isHelper(contents) &&
      url.protocol === "https:" &&
      hosts.has(url.hostname) &&
      !url.port
    );
  } catch {
    return false;
  }
}
function svg(value) {
  if (
    typeof value !== "string" ||
    value.length > 16384 ||
    !value.startsWith("<svg") ||
    !value.endsWith("</svg>")
  )
    return;
  let cursor = 0;
  const stack = [];
  for (const match of value.matchAll(/<(\/?)([a-z]+)([^<>]*)>/g)) {
    if (match.index !== cursor || !svgTags.has(match[2])) return;
    cursor = match.index + match[0].length;
    if (match[1]) {
      if (match[3] || stack.pop() !== match[2]) return;
      continue;
    }
    stack.push(match[2]);
    let tail = match[3];
    while (tail) {
      const attr = /^ ([a-zA-Z-]+)="([^"<>]*)"/.exec(tail);
      if (!attr || !svgAttributes.has(attr[1])) return;
      const data = attr[2];
      if (data.length > 12000 || /[&\\;{}]|url\(|javascript:/i.test(data)) return;
      if (
        ["fill", "stroke"].includes(attr[1]) &&
        !["none", "transparent", "currentColor"].includes(data)
      )
        return;
      if (!/^[a-zA-Z0-9 .,+()-]*$/.test(data)) return;
      tail = tail.slice(attr[0].length);
    }
  }
  if (cursor !== value.length || stack.length) return;
  return value;
}
function sanitize(value) {
  const result = { tokens: {}, theme: "dark", classes: [], fontFamily: "", fonts: [], icons: {} };
  if (!value || typeof value !== "object") return result;
  let tokenBytes = 0;
  for (const [name, raw] of Object.entries(value.tokens || {}).slice(0, 4096)) {
    if (
      !tokenName.test(name) ||
      typeof raw !== "string" ||
      raw.length > 2048 ||
      /[;{}<>\r\n\\]|url\s*\(|@import/i.test(raw)
    )
      continue;
    tokenBytes += name.length + raw.length;
    if (tokenBytes > 512 * 1024) break;
    result.tokens[name] = raw;
  }
  result.classes = Array.isArray(value.classes)
    ? value.classes
        .filter((name) => /^theme-(?:light|dark|darker|midnight)$/.test(name))
        .slice(0, 4)
    : [];
  result.theme = value.theme === "light" ? "light" : "dark";
  result.reducedMotion = value.reducedMotion === true;
  if (
    typeof value.fontFamily === "string" &&
    value.fontFamily.length < 200 &&
    !/[;{}<>\r\n\\]|url\s*\(/i.test(value.fontFamily)
  )
    result.fontFamily = value.fontFamily;
  for (const [name, raw] of Object.entries(value.icons || {})) {
    const safe = iconNames.has(name) && svg(raw);
    if (safe) result.icons[name] = safe;
  }
  for (const font of Array.isArray(value.fonts) ? value.fonts.slice(0, 20) : []) {
    try {
      const url = new URL(font.url);
      if (
        url.protocol !== "https:" ||
        !hosts.has(url.hostname) ||
        url.port ||
        !/^\/assets\/[a-zA-Z0-9._-]+\.woff2$/.test(url.pathname) ||
        url.search ||
        url.hash
      )
        continue;
      if (
        !/^(gg sans|Noto Sans|ABC Ginto Normal|ABC Ginto Nord)$/.test(font.family) ||
        !/^(?:[1-9]00)(?: [1-9]00)?$/.test(font.weight) ||
        !/^(normal|italic)$/.test(font.style)
      )
        continue;
      result.fonts.push({ ...font, url: url.href });
    } catch {
      /* Untrusted font address. */
    }
  }
  return result;
}
function linuxVersion(release, kernel) {
  const fields = {};
  for (const line of release.split(/\r?\n/)) {
    const match = /^(PRETTY_NAME|NAME|VERSION_ID|BUILD_ID)=(.*)$/.exec(line);
    if (!match) continue;
    let value = match[2].trim();
    if (/^(["']).*\1$/.test(value)) value = value.slice(1, -1);
    if (
      value.length < 200 &&
      ![...value].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
    )
      fields[match[1]] = value;
  }
  let name = fields.PRETTY_NAME || fields.NAME || "Linux";
  if (fields.VERSION_ID && !name.includes(fields.VERSION_ID)) name += ` ${fields.VERSION_ID}`;
  else if (fields.BUILD_ID === "rolling" && !name.toLowerCase().includes("rolling"))
    name += " (rolling)";
  return `${name} · ${kernel}`;
}
let linux;
function platformVersion() {
  if (linux) return linux;
  let release = "";
  for (const filename of ["/etc/os-release", "/usr/lib/os-release"]) {
    try {
      release = fs.readFileSync(filename, "utf8");
      break;
    } catch {
      /* Minimal installations may not provide distribution metadata. */
    }
  }
  linux = linuxVersion(release.slice(0, 16384), os.release());
  return linux;
}
function metadata(
  root = process.env.DPE_APP_ROOT || "",
  versions = process.versions,
  clientHost = process.env.DPE_CLIENT_HOST,
) {
  const policy = preferences.readObject(path.join(root, "runtime-update.json"));
  const build = preferences.readObject(path.join(root, "local-build.json"));
  const packagedDiscord = preferences.readObject(path.join(root, "resources/build_info.json"));
  const activeDiscord =
    typeof clientHost === "string" && path.isAbsolute(clientHost)
      ? preferences.readObject(path.join(clientHost, "resources/build_info.json"))
      : {};
  const channels = { canary: "Canary", ptb: "PTB", stable: "Stable" };
  const discord = {
    ...packagedDiscord,
    ...(typeof activeDiscord.version === "string" && /^\d+\.\d+\.\d+$/.test(activeDiscord.version)
      ? { version: activeDiscord.version }
      : {}),
    ...(typeof activeDiscord.releaseChannel === "string" &&
    Object.hasOwn(channels, activeDiscord.releaseChannel)
      ? { releaseChannel: activeDiscord.releaseChannel }
      : {}),
  };
  const revision =
    typeof build.source_commit === "string" && /^[0-9a-f]{40}$/.test(build.source_commit)
      ? build.source_commit.slice(0, 7)
      : "";
  const openasar = typeof global.oaVersion === "string" ? global.oaVersion : "Unknown";
  const oaRevision =
    typeof build.openasar_commit === "string" && /^[0-9a-f]{40}$/.test(build.openasar_commit)
      ? build.openasar_commit.slice(0, 7)
      : "";
  const channel = discord.releaseChannel || build.channel;
  const channelName = Object.hasOwn(channels, channel) ? channels[channel] : "";
  return {
    dpe:
      policy.release_version ||
      (revision ? `${revision}${build.source_dirty ? " + changes" : ""}` : "Development"),
    discord: [channelName, discord.version || build.client_version || "Unknown"]
      .filter(Boolean)
      .join(" "),
    electron: versions.electron || "Unknown",
    openasar: oaRevision ? `${openasar} (${oaRevision})` : openasar,
    ...(process.platform === "linux" ? { linux: platformVersion() } : {}),
  };
}
function recoveryCommand() {
  const launcher = process.env.DPE_LAUNCHER;
  if (launcher && path.isAbsolute(launcher) && !/[\r\n\0]/.test(launcher))
    return `'${launcher.replace(/'/g, "'\\''")}' --ignore-flags`;
  const name = process.env.DPE_PACKAGE_NAME;
  return `${typeof name === "string" && /^[a-z0-9_.-]+$/.test(name) ? name : "discord-canary-prerelease-patched-electron-bin"} --ignore-flags`;
}
function versionText(versions, mod) {
  const labels = { dpe: "DPE", discord: "Discord", electron: "Electron", openasar: "OpenAsar" };
  const lines = Object.entries(versions).map(([key, value]) =>
    key === "linux" ? value : `${labels[key] || key} ${value}`.trim(),
  );
  if (mod && ["vencord", "equicord"].includes(mod.installed))
    lines.push(
      `${mod.installed === "equicord" ? "Equicord" : "Vencord"} ${mod.installedVersion || ""}`.trim(),
    );
  return lines.join("\n");
}
function attach(window, preferredSource, options = {}) {
  const { BrowserWindow, clipboard, net } = require("electron");
  const desktop = require("../desktop/preferences.cjs");
  let stopped = false,
    running = false,
    styleKey,
    fontKey,
    latest = sanitize({ icons: require("../discord-ui/icons.cjs")() }),
    previous = "",
    ready = false,
    initialReady = false,
    rendererReady = options.waitForReady !== true,
    shown = false;
  let initialLoaded;
  const initialLoad = new Promise((resolve) => {
    initialLoaded = resolve;
  });
  const fontCache = new Map();
  let sourceContents = preferredSource;
  const decorate = (value) => ({
    ...value,
    mode: options.mode || "settings",
    versions: metadata(),
    maximized: window.isMaximized(),
    windowControls: desktop.effectiveControls(window),
    focused: window.isFocused?.() !== false,
    recoveryCommand: recoveryCommand(),
  });
  function handle(name, callback) {
    window.webContents.ipc.handle(name, (event, ...args) => {
      if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame)
        throw Error("Presentation is available only in the local settings window.");
      return callback(...args);
    });
  }
  handle("dpe-presentation", () => decorate(latest));
  function show() {
    if (!shown && !stopped && initialReady && rendererReady) {
      shown = true;
      window.show();
    }
  }
  handle("dpe-config-ready", async () => {
    await initialLoad;
    if (stopped) return false;
    try {
      await window.webContents.executeJavaScript("document.fonts.ready.then(() => true)");
    } catch (error) {
      if (stopped || window.isDestroyed()) return false;
      throw error;
    }
    rendererReady = true;
    show();
    return true;
  });
  handle("dpe-window-action", (action) => {
    if (action === "minimize") window.minimize();
    else if (action === "maximize") {
      if (window.isMaximized()) window.unmaximize();
      else window.maximize();
    } else if (action === "close") window.close();
    else throw Error("Unknown window action");
    return { maximized: !window.isDestroyed() && window.isMaximized() };
  });
  handle("dpe-copy-recovery", async () => {
    await clipboard.writeText(recoveryCommand());
    return true;
  });
  handle("dpe-copy-versions", async () => {
    const text = versionText(metadata(), require("./mod-settings.cjs").installedInfo());
    await clipboard.writeText(text);
    return text;
  });
  const publish = () => {
    if (ready && !stopped) window.webContents.send("dpe-presentation", decorate(latest));
  };
  const unsubscribeControls = desktop.subscribeControls(window, publish);
  window.on("focus", publish);
  window.on("blur", publish);
  window.on("maximize", publish);
  window.on("unmaximize", publish);
  async function loadFonts(fonts) {
    const rules = await Promise.all(
      fonts.map(async (font) => {
        if (!fontCache.has(font.url))
          fontCache.set(
            font.url,
            native.font(font.url, {
              profile: process.env.DPE_PROFILE_DIR,
              offline: process.env.DPE_CLIENT_OFFLINE === "1",
              fetch: net.fetch,
            }),
          );
        const bytes = await fontCache.get(font.url);
        return bytes
          ? `@font-face{font-family:"${font.family}";font-style:${font.style};font-weight:${font.weight};src:url(data:font/woff2;base64,${bytes}) format("woff2");font-display:swap}`
          : "";
      }),
    );
    if (stopped || !ready) return;
    const css = rules.join("");
    if (css) {
      const old = fontKey;
      fontKey = await window.webContents.insertCSS(css);
      if (old) await window.webContents.removeInsertedCSS(old);
    }
  }
  async function sync() {
    if (stopped || running || !ready) return;
    running = true;
    try {
      if (!trusted(sourceContents))
        sourceContents = BrowserWindow.getAllWindows()
          .map((item) => item.webContents)
          .find(trusted);
      let value, zoom, origin;
      const active = sourceContents;
      if (!active) {
        // Retain a live capture during client reloads. Only the initial empty
        // window needs a public, isolated asset acquisition.
        if (previous) return;
        const build = preferences.readObject(
          path.join(process.env.DPE_APP_ROOT || "", "resources/build_info.json"),
        );
        const channel =
          build.releaseChannel ||
          preferences.readObject(path.join(process.env.DPE_APP_ROOT || "", "local-build.json"))
            .channel;
        origin =
          channel === "stable"
            ? "https://discord.com"
            : channel === "ptb"
              ? "https://ptb.discord.com"
              : "https://canary.discord.com";
        const acquired = await native.acquire({
          profile: process.env.DPE_PROFILE_DIR,
          origin,
          sanitize,
          offline: process.env.DPE_CLIENT_OFFLINE === "1",
        });
        if (!acquired || stopped) return;
        value = acquired.snapshot;
        zoom = acquired.zoom;
      } else {
        origin = new URL(active.getURL()).origin;
        zoom = active.getZoomFactor?.();
        let timeout;
        value = await Promise.race([
          active.executeJavaScript(
            source.call(capture, {
              windowMaximized: BrowserWindow.fromWebContents(active)?.isMaximized() === true,
            }),
          ),
          new Promise((_, reject) => {
            timeout = setTimeout(() => reject(Error("Presentation capture timed out")), 2000);
          }),
        ]).finally(() => clearTimeout(timeout));
        if (stopped || !trusted(active)) return;
      }
      if (Number.isFinite(zoom) && zoom >= 0.5 && zoom <= 3)
        window.webContents.setZoomFactor?.(options.mode === "welcome" ? 1 : zoom);
      const next = sanitize(value),
        identity = JSON.stringify(next) + ":" + zoom;
      if (identity === previous) return;
      previous = identity;
      if (active)
        await native
          .remember({
            profile: process.env.DPE_PROFILE_DIR,
            origin,
            snapshot: next,
            sanitize,
            zoom,
          })
          .catch(() => {});
      next.icons = options.lockIcons && shown ? latest.icons : { ...latest.icons, ...next.icons };
      latest = next;
      // Transition suppression lasts through the browser's next committed frame.
      await window.webContents.executeJavaScript(
        `(() => { const old=document.getElementById('dpe-theme-swap');if(old)old.remove();const s=document.createElement('style');s.id='dpe-theme-swap';s.textContent='*,*::before,*::after{transition:none!important}';document.head.append(s); })()`,
      );
      const css = `:root,body.dpe-ui{${Object.entries(next.tokens)
        .map(([name, raw]) => `${name}:${raw};`)
        .join("")}${next.fontFamily ? `--font-primary:${next.fontFamily};` : ""}}`;
      const old = styleKey;
      styleKey = await window.webContents.insertCSS(css);
      if (old) await window.webContents.removeInsertedCSS(old);
      await window.webContents.executeJavaScript(
        `(() => {document.body.dataset.dpeTheme=${JSON.stringify(next.theme)};document.body.dataset.reducedMotion=${JSON.stringify(String(next.reducedMotion))};document.documentElement.classList.remove('theme-light','theme-dark','theme-darker','theme-midnight');document.documentElement.classList.add(...${JSON.stringify(next.classes)});void document.body.offsetHeight;requestAnimationFrame(()=>requestAnimationFrame(()=>document.getElementById('dpe-theme-swap')?.remove()));})()`,
      );
      publish();
      const fonts = loadFonts(next.fonts).catch(() => {
        /* Window closed while fonts loaded. */
      });
      if (!shown) await fonts;
    } catch {
      /* A reloading client or missing source retains the last valid presentation. */
    } finally {
      running = false;
    }
  }
  window.webContents.on("did-finish-load", () => {
    ready = true;
    void sync().finally(() => {
      publish();
      initialReady = true;
      initialLoaded();
      show();
    });
  });
  window.webContents.on("did-start-navigation", (_event, _url, _inPlace, isMainFrame) => {
    if (isMainFrame) {
      ready = false;
      previous = "";
      styleKey = undefined;
      fontKey = undefined;
    }
  });
  const timer = setInterval(() => void sync(), 750);
  timer.unref();
  window.once("closed", () => {
    stopped = true;
    unsubscribeControls();
    initialLoaded();
    clearInterval(timer);
    fontCache.clear();
  });
  return { sync };
}
module.exports = {
  attach,
  sanitize,
  svg,
  trusted,
  metadata,
  linuxVersion,
  recoveryCommand,
  versionText,
};
