"use strict";
// Obtain public presentation assets before the desktop client starts. The
// disposable renderer has neither the user's browser session nor a native bridge.
const fs = require("node:fs/promises");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");
const source = require("../discord-ui/source.cjs");
const capture = require("../discord-ui/presentation.cjs");
const origins = new Set([
  "https://discord.com",
  "https://canary.discord.com",
  "https://ptb.discord.com",
]);
const helpers = new WeakSet();
const pending = new Map();
const refreshAfter = 24 * 60 * 60 * 1000;
const requiredIcons = [
  "settings",
  "appearance",
  "mods",
  "external",
  "download",
  "experimental",
  "refresh",
  "copy",
  "chevron",
];
const digest = (value) => createHash("sha256").update(value).digest("hex");

function allowedRequest(details, origin) {
  try {
    const url = new URL(details.url);
    return (
      origins.has(origin) &&
      details.method === "GET" &&
      url.origin === origin &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      ((details.resourceType === "mainFrame" && url.pathname === "/login") ||
        (["script", "stylesheet", "font", "xhr", "other"].includes(details.resourceType) &&
          /^\/assets\/[a-zA-Z0-9._-]+\.(?:js|css|woff2)$/.test(url.pathname)))
    );
  } catch {
    return false;
  }
}
function fontURL(value) {
  try {
    const url = new URL(value);
    return (
      origins.has(url.origin) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      /^\/assets\/[a-zA-Z0-9._-]+\.woff2$/.test(url.pathname)
    );
  } catch {
    return false;
  }
}
function directory(profile, origin) {
  if (!profile || !path.isAbsolute(profile) || !origins.has(origin)) return;
  return path.join(profile, "dpe-updater/native-presentation", new URL(origin).hostname);
}
async function readRecord(filename, limit = 2 * 1024 * 1024) {
  try {
    if ((await fs.stat(filename)).size > limit) return;
    const value = JSON.parse(await fs.readFile(filename, "utf8"));
    if (
      value.schema !== 1 ||
      typeof value.payload !== "string" ||
      value.sha256 !== digest(value.payload)
    )
      return;
    return JSON.parse(value.payload);
  } catch {
    return;
  }
}
async function writeRecord(filename, value) {
  const payload = JSON.stringify(value);
  if (payload.length > 2 * 1024 * 1024) return;
  await fs.mkdir(path.dirname(filename), { recursive: true, mode: 0o700 });
  const temporary = filename + "." + randomUUID() + ".tmp";
  try {
    await fs.writeFile(temporary, JSON.stringify({ schema: 1, sha256: digest(payload), payload }), {
      mode: 0o600,
    });
    await fs.rename(temporary, filename);
  } finally {
    await fs.rm(temporary, { force: true });
  }
}
async function cached(profile, origin, kind, sanitize) {
  const folder = directory(profile, origin);
  if (!folder) return;
  const value = await readRecord(path.join(folder, kind + ".json"));
  if (
    !value ||
    value.origin !== origin ||
    value.source !== kind ||
    !Number.isFinite(value.capturedAt) ||
    value.capturedAt > Date.now() + 300000
  )
    return;
  const snapshot = sanitize(value.snapshot);
  if (Object.keys(snapshot.tokens).length < 100) return;
  return {
    ...value,
    snapshot,
    zoom: Number.isFinite(value.zoom) && value.zoom >= 0.5 && value.zoom <= 3 ? value.zoom : 1,
  };
}
async function remember({ profile, origin, snapshot, sanitize, zoom = 1, kind = "client" }) {
  const folder = directory(profile, origin);
  if (!folder || !["client", "public-assets"].includes(kind)) return;
  snapshot = sanitize(snapshot);
  if (Object.keys(snapshot.tokens).length < 100) return;
  const value = { origin, source: kind, capturedAt: Date.now(), zoom, snapshot };
  await writeRecord(path.join(folder, kind + ".json"), value);
  return value;
}
function merge(publicAssets, client) {
  if (!client) return publicAssets;
  if (!publicAssets) return client;
  // A public page cannot know the user's Discord theme or font/zoom settings.
  return {
    ...client,
    snapshot: {
      ...client.snapshot,
      icons: { ...publicAssets.snapshot.icons, ...client.snapshot.icons },
    },
  };
}
async function font(url, { profile, offline, fetch: fetcher }) {
  if (!fontURL(url)) return "";
  const folder = directory(profile, new URL(url).origin);
  const filename = folder && path.join(folder, "fonts", digest(url) + ".json");
  if (filename) {
    const value = await readRecord(filename);
    if (value?.url === url && typeof value.data === "string") {
      const bytes = Buffer.from(value.data, "base64");
      if (
        bytes.length <= 1024 * 1024 &&
        bytes.subarray(0, 4).toString() === "wOF2" &&
        bytes.toString("base64") === value.data
      )
        return value.data;
    }
  }
  if (offline || !fetcher) return "";
  try {
    const response = await fetcher(url, {
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok || (response.url && response.url !== url)) return "";
    const chunks = [];
    let length = 0;
    for await (const chunk of response.body) {
      length += chunk.length;
      if (length > 1024 * 1024) return "";
      chunks.push(Buffer.from(chunk));
    }
    const bytes = Buffer.concat(chunks);
    if (bytes.subarray(0, 4).toString() !== "wOF2") return "";
    const data = bytes.toString("base64");
    if (filename) await writeRecord(filename, { url, data }).catch(() => {});
    return data;
  } catch {
    return "";
  }
}
async function probe({ origin, sanitize, timeout = 15000, onRequest }) {
  const { BrowserWindow, session } = require("electron");
  const isolated = session.fromPartition("dpe-native-presentation-" + randomUUID(), {
    cache: false,
  });
  isolated.webRequest.onBeforeRequest((details, callback) => {
    const allowed = allowedRequest(details, origin);
    onRequest?.({ url: details.url, resourceType: details.resourceType, allowed });
    callback({ cancel: !allowed });
  });
  isolated.setPermissionCheckHandler(() => false);
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.on("will-download", (event) => event.preventDefault());
  const window = new BrowserWindow({
    show: false,
    focusable: false,
    skipTaskbar: true,
    width: 1080,
    height: 900,
    webPreferences: {
      session: isolated,
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
    },
  });
  helpers.add(window.webContents);
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  let timer;
  const deadline = Date.now() + timeout;
  try {
    return await Promise.race([
      (async () => {
        await window.loadURL(origin + "/login");
        while (!window.isDestroyed() && Date.now() < deadline) {
          const value = await window.webContents.executeJavaScript(source.call(capture, {}));
          const snapshot = sanitize(value);
          if (
            requiredIcons.every((name) => snapshot.icons[name]) &&
            Object.keys(snapshot.tokens).length > 500 &&
            snapshot.fonts.length >= 4
          )
            return { origin, source: "public-assets", capturedAt: Date.now(), zoom: 1, snapshot };
          await new Promise((resolve) => setTimeout(resolve, 150));
        }
        throw Error("Native presentation assets did not finish loading.");
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(Error("Native presentation acquisition timed out.")),
          timeout,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
    if (!window.isDestroyed()) window.destroy();
    // This nonpersistent session never reads the account profile or keeps cookies.
    void isolated.clearStorageData().catch(() => {});
  }
}
async function acquire({ profile, origin, sanitize, offline = false, onRequest }) {
  if (!directory(profile, origin)) return;
  const [publicAssets, client] = await Promise.all([
    cached(profile, origin, "public-assets", sanitize),
    cached(profile, origin, "client", sanitize),
  ]);
  if (offline || (publicAssets && Date.now() - publicAssets.capturedAt < refreshAfter))
    return merge(publicAssets, client);
  const key = profile + "\0" + origin;
  if (!pending.has(key))
    pending.set(
      key,
      (async () => {
        try {
          const next = await probe({ origin, sanitize, onRequest });
          await remember({
            profile,
            origin,
            snapshot: next.snapshot,
            sanitize,
            kind: "public-assets",
          }).catch(() => {});
          return next;
        } catch {
          return;
        }
      })(),
    );
  return merge((await pending.get(key)) || publicAssets, client);
}
module.exports = {
  acquire,
  probe,
  remember,
  cached,
  merge,
  font,
  allowedRequest,
  fontURL,
  readRecord,
  writeRecord,
  isHelper: (contents) => Boolean(contents && helpers.has(contents)),
};
