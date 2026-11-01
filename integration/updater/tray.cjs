// Reuse Discord's tray and voice/unread state, with bundled presets and custom PNGs.
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const preferences = require("./profile.cjs");
const { presets } = require("./assets/tray-presets/manifest.json");
const states = {
  default: "Default",
  unread: "Unread messages",
  connected: "In a voice channel",
  speaking: "Speaking",
  muted: "Microphone muted",
  deafened: "Deafened",
};
const cache = new Map();
const stockPreviews = new Map();
let defaults = {};
let defaultDirectory;
let active;

function config() {
  return global.settings?.get("openasar", {}) ?? preferences.config();
}
function directory() {
  return path.join(process.env.DPE_PROFILE_DIR, "tray-icons");
}
function asset(state) {
  const name = config().trayIcons?.[state];
  return typeof name === "string" && /^[a-f0-9]{64}\.png$/.test(name) ? name : null;
}
function validPreset(value) {
  return typeof value === "string" && (value === "discord" || Object.hasOwn(presets, value));
}
function preset(state) {
  const settings = config();
  const override = settings.trayIconPresets?.[state];
  if (state && validPreset(override)) return override;
  return validPreset(settings.trayIconPreset) ? settings.trayIconPreset : "discord";
}
function presetImage(state) {
  const selected = preset(state);
  if (selected === "discord") return null;
  const filename = presets[selected].states[state];
  const key = `preset:${selected}/${filename}`;
  if (cache.has(key)) return cache.get(key);
  try {
    const image = require("electron").nativeImage.createFromPath(
      path.join(__dirname, "assets/tray-presets", filename),
    );
    if (image.isEmpty()) return null;
    const resized = image.resize({ width: 32, height: 32, quality: "best" });
    cache.set(key, resized);
    return resized;
  } catch {
    return null;
  }
}
function checkState(state) {
  if (typeof state !== "string" || !Object.hasOwn(states, state))
    throw new Error("Unknown tray icon state.");
}
function stateFor(value = {}) {
  if (!value.connected) return value.unread ? "unread" : "default";
  if (value.deafStates?.selfDeaf || value.deafStates?.serverDeaf) return "deafened";
  if (value.muteStates?.selfMute || value.muteStates?.serverMute || value.muteStates?.suppress)
    return "muted";
  return value.speaking ? "speaking" : "connected";
}
function load(name) {
  if (cache.has(name)) return cache.get(name);
  try {
    const filename = path.join(directory(), name);
    if (fs.statSync(filename).size > 4 * 1024 * 1024) return null;
    const image = require("electron").nativeImage.createFromBuffer(fs.readFileSync(filename));
    if (image.isEmpty()) return null;
    cache.set(name, image);
    return image;
  } catch {
    // Missing or damaged custom assets must never remove Discord's tray.
    return null;
  }
}
function image(original, state) {
  const id = stateFor(state);
  const name = asset(id);
  return (name && load(name)) || presetImage(id) || original;
}
function register(tray, getImage, originals = {}) {
  active = { tray, getImage };
  defaults = originals;
  stockPreviews.clear();
}
function setDefaultDirectory(directory) {
  defaultDirectory = directory;
  stockPreviews.clear();
}
function refresh() {
  if (!active) return;
  if (active.tray.isDestroyed()) {
    active = undefined;
    return;
  }
  active.tray.setImage(active.getImage());
}
function describe() {
  return Object.entries(states).map(([id, label]) => {
    const name = asset(id);
    const custom = name && load(name);
    const bundled = !custom && presetImage(id);
    let preview = (custom || bundled)?.toDataURL();
    if (!preview) {
      if (!stockPreviews.has(id)) {
        // Settings can open from onboarding before Discord creates its tray.
        const original =
          defaults[id.toUpperCase()] ||
          (defaultDirectory &&
            path.join(defaultDirectory, id === "default" ? "tray.png" : `tray-${id}.png`));
        const image =
          typeof original === "string"
            ? require("electron").nativeImage.createFromPath(original)
            : original;
        stockPreviews.set(id, image && !image.isEmpty() ? image.toDataURL() : null);
      }
      preview = stockPreviews.get(id);
    }
    return {
      id,
      label,
      custom: Boolean(custom),
      overridden: Boolean(name) || validPreset(config().trayIconPresets?.[id]),
      preset: custom ? null : bundled ? preset(id) : "discord",
      source: custom ? "Custom image" : bundled ? presets[preset(id)].label : "Discord",
      preview: preview || null,
    };
  });
}
function status() {
  return {
    preset: preset(),
    presets: [
      ["discord", "Discord"],
      ...Object.entries(presets).map(([id, value]) => [id, value.label]),
    ],
    icons: describe(),
  };
}
function save(patch) {
  const settings = global.settings;
  if (!settings) throw new Error("Reopen Settings after Discord has started.");
  const previous = settings.get("openasar", {});
  const next = { ...previous, ...patch };
  settings.set("openasar", next);
  try {
    settings.save();
  } catch (error) {
    settings.set("openasar", previous);
    throw error;
  }
  global.oaConfig = { ...next, autoupdate: false };
  refresh();
}
function importImage(filename) {
  const fd = fs.openSync(filename, "r");
  let data;
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > 4 * 1024 * 1024)
      throw new Error("Choose a PNG image smaller than 4 MB.");
    data = fs.readFileSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  if (
    data.length < 24 ||
    data.length > 4 * 1024 * 1024 ||
    !data.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex")) ||
    data.toString("ascii", 12, 16) !== "IHDR"
  )
    throw new Error("Choose a valid PNG image.");
  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  if (!width || !height || width > 1024 || height > 1024)
    throw new Error("Choose a PNG no larger than 1024 × 1024 pixels.");
  const decoded = require("electron").nativeImage.createFromBuffer(data);
  if (decoded.isEmpty()) throw new Error("The PNG image could not be opened.");
  const png = decoded.resize({ width: 32, height: 32, quality: "best" }).toPNG();
  const name = createHash("sha256").update(png).digest("hex") + ".png";
  fs.mkdirSync(directory(), { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(directory(), name), png, { mode: 0o600 });
  cache.delete(name);
  return name;
}
async function choose(window, state) {
  checkState(state);
  const result = await require("electron").dialog.showOpenDialog(window, {
    title: `Choose ${states[state].toLowerCase()} tray icon`,
    properties: ["openFile"],
    filters: [{ name: "PNG image", extensions: ["png"] }],
  });
  if (result.canceled || !result.filePaths.length) return status();
  const name = importImage(result.filePaths[0]);
  save({ trayIcons: { ...config().trayIcons, [state]: name } });
  return status();
}
function reset(state) {
  checkState(state);
  const icons = { ...config().trayIcons };
  const overrides = { ...config().trayIconPresets };
  delete icons[state];
  delete overrides[state];
  save({ trayIcons: icons, trayIconPresets: overrides });
  return status();
}
function setPreset(selected) {
  if (!validPreset(selected)) throw new Error("Unknown tray icon preset.");
  save({ trayIconPreset: selected, trayIcons: {}, trayIconPresets: {} });
  return status();
}
function setStatePreset(state, selected) {
  checkState(state);
  if (!validPreset(selected)) throw new Error("Unknown tray icon preset.");
  const icons = { ...config().trayIcons };
  delete icons[state];
  save({
    trayIcons: icons,
    trayIconPresets: { ...config().trayIconPresets, [state]: selected },
  });
  return status();
}
module.exports = {
  image,
  register,
  setDefaultDirectory,
  describe,
  status,
  setPreset,
  setStatePreset,
  choose,
  reset,
  stateFor,
  importImage,
};
