// Local splash adapter and pinned OpenAsar settings. Proprietary media is
// fetched separately into the user cache, never included in the package.
const fs = require("node:fs/promises");
const path = require("node:path");
const { createHash } = require("node:crypto");
const source = require("./openasar/source.json");
const media = require("./splash-media.json");
const checksum = (data) => createHash("sha256").update(data).digest("hex");
const policy =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src dpe-splash:; font-src dpe-splash:";

module.exports = function assetHandler(
  cache,
  fetch,
  offline = false,
  editorRoot = path.join(process.env.DPE_APP_ROOT || "", "bootstrap/cdn"),
) {
  const pending = new Map();
  async function asset(name, metadata) {
    const target = path.join(cache, metadata.sha256 + path.extname(name));
    try {
      const data = await fs.readFile(target);
      if (checksum(data) === metadata.sha256) return data;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (offline) throw new Error("OpenAsar splash media is not cached");
    const response = await fetch(
      metadata.url ||
        `https://raw.githubusercontent.com/OpenAsar/cdn/${source.commit}/splash/${name}`,
      { credentials: "omit", signal: AbortSignal.timeout(15000) },
    );
    if (!response.ok) throw new Error(`OpenAsar splash download failed: ${response.status}`);
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > metadata.size) throw new Error("OpenAsar splash media exceeds pinned size");
      chunks.push(chunk);
    }
    const data = Buffer.concat(chunks);
    if (checksum(data) !== metadata.sha256) throw new Error("OpenAsar splash checksum mismatch");
    await fs.mkdir(cache, { recursive: true, mode: 0o700 });
    const temporary = `${target}.${process.pid}.tmp`;
    try {
      await fs.writeFile(temporary, data, { mode: 0o600 });
      await fs.rename(temporary, target);
    } finally {
      await fs.rm(temporary, { force: true });
    }
    return data;
  }
  return async ({ url }) => {
    const parsed = new URL(url);
    if (parsed.host !== "app") return new Response(null, { status: 404 });
    const name = parsed.pathname.slice(1);
    try {
      if (["launch-settings.html", "launch-settings.js", "launch-settings.css"].includes(name)) {
        const contents = await fs.readFile(path.join(__dirname, name));
        return new Response(contents, {
          headers: {
            "content-type": {
              ".html": "text/html; charset=utf-8",
              ".js": "text/javascript",
              ".css": "text/css",
            }[path.extname(name)],
            "content-security-policy":
              "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'none'",
          },
        });
      }
      if (
        name === "config.html" ||
        (/^monaco-editor\/min\/[A-Za-z0-9_./-]+$/.test(name) && !name.split("/").includes(".."))
      ) {
        const file =
          name === "config.html"
            ? path.join(__dirname, "openasar/config.html")
            : path.join(editorRoot, name);
        const mime =
          {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".ttf": "font/ttf",
          }[path.extname(file)] || "application/octet-stream";
        let contents = await fs.readFile(file);
        if (name === "config.html" && checksum(contents) !== source.config_sha256)
          throw new Error("OpenAsar settings checksum mismatch");
        if (name === "config.html") contents = require("./config-html.cjs")(contents.toString());
        return new Response(contents, {
          headers: {
            "content-type": mime,
            "content-security-policy":
              "default-src 'none'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src dpe-splash: https://fonts.gstatic.com; connect-src dpe-splash:; worker-src blob: dpe-splash:; img-src dpe-splash: data:",
          },
        });
      }
      if (name === "index.html") {
        const html = await fs.readFile(path.join(__dirname, "modern-splash.html"));
        return new Response(html, {
          headers: {
            "content-type": "text/html; charset=utf-8",
            "content-security-policy": policy,
          },
        });
      }
      const metadata =
        (Object.hasOwn(media.assets, name) && media.assets[name]) ||
        (Object.hasOwn(source.assets, name) && source.assets[name]);
      if (!metadata) return new Response(null, { status: 404 });
      if (!pending.has(name))
        pending.set(
          name,
          asset(name, metadata).catch((error) => {
            pending.delete(name);
            throw error;
          }),
        );
      return new Response(await pending.get(name), { headers: { "content-type": metadata.type } });
    } catch (error) {
      console.error(error.message);
      return new Response(null, { status: 503 });
    }
  };
};
