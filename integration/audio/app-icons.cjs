"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");

const validName = (name) =>
  typeof name === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(name);
const within = (file, root) => file.startsWith(root + path.sep);

// Resolve branded app icons locally. Never execute desktop entries, search the
// network, or treat an icon/desktop ID as audio-capture authority.
function createResolver({ dataDirs, legacyIcons, extraRoots } = {}) {
  const home = os.homedir();
  dataDirs ??= [
    process.env.XDG_DATA_HOME || path.join(home, ".local/share"),
    ...(process.env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":"),
    path.join(home, ".local/share/flatpak/exports/share"),
    "/var/lib/flatpak/exports/share",
  ];
  dataDirs = [...new Set(dataDirs.filter((dir) => path.isAbsolute(dir)))].slice(0, 16);
  legacyIcons ??= path.join(home, ".icons");
  extraRoots ??= [path.join(home, ".local/share/flatpak/app"), "/var/lib/flatpak/app"];
  const roots = [...dataDirs, legacyIcons, ...extraRoots]
    .filter(Boolean)
    .map((dir) => path.resolve(dir));
  const cache = new Map();
  const themeDirs = [
    "64x64/apps",
    "48x48/apps",
    "128x128/apps",
    "scalable/apps",
    "256x256/apps",
    "32x32/apps",
  ];
  async function read(file, maxBytes) {
    try {
      const real = await fs.realpath(file);
      if (!roots.some((root) => within(real, root))) return null;
      const handle = await fs.open(real, "r");
      try {
        const stat = await handle.stat();
        if (!stat.isFile() || stat.size > maxBytes) return null;
        // A bounded read even if an icon changes after stat().
        const buffer = Buffer.alloc(maxBytes + 1);
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
        return bytesRead > maxBytes ? null : buffer.subarray(0, bytesRead);
      } finally {
        await handle.close();
      }
    } catch {
      return null;
    }
  }
  async function image(file) {
    const bytes = await read(file, 256 * 1024);
    if (!bytes) return null;
    let type;
    if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) type = "png";
    else if (path.extname(file) === ".svg" && /<svg[\s>]/.test(bytes.toString("utf8")))
      type = "svg+xml";
    else return null;
    // SVG is loaded only as an image, never inserted into the document markup.
    return `data:image/${type};base64,${bytes.toString("base64")}`;
  }
  async function named(name) {
    if (!validName(name)) return null;
    const stem = name.replace(/\.(png|svg)$/, "");
    for (const dir of [legacyIcons, ...dataDirs.map((dir) => path.join(dir, "icons"))].filter(
      Boolean,
    )) {
      for (const size of themeDirs) {
        for (const extension of ["png", "svg"]) {
          const result = await image(path.join(dir, "hicolor", size, `${stem}.${extension}`));
          if (result) return result;
        }
      }
    }
    for (const dir of dataDirs) {
      for (const extension of ["png", "svg"]) {
        const result = await image(path.join(dir, "pixmaps", `${stem}.${extension}`));
        if (result) return result;
      }
    }
    return null;
  }
  async function lookup(names) {
    for (const name of names) {
      const direct = await named(name);
      if (direct) return direct;
      for (const dir of dataDirs) {
        const entry = await read(
          path.join(dir, "applications", name.replace(/\.desktop$/, "") + ".desktop"),
          64 * 1024,
        );
        if (!entry) continue;
        let inEntry = false;
        let icon;
        for (const line of entry.toString("utf8").split(/\r?\n/)) {
          if (line.startsWith("[")) inEntry = line === "[Desktop Entry]";
          else if (inEntry && line.startsWith("Icon=")) {
            icon = line.slice(5).trim();
            break;
          }
        }
        const result = icon && (path.isAbsolute(icon) ? await image(icon) : await named(icon));
        if (result) return result;
      }
    }
    return null;
  }
  return (candidates) => {
    const names = Array.isArray(candidates)
      ? [...new Set(candidates.filter(validName))].slice(0, 4)
      : [];
    const key = JSON.stringify(names);
    if (!cache.has(key))
      cache.set(
        key,
        lookup(names).catch(() => null),
      );
    return cache.get(key);
  };
}
module.exports = { createResolver };
