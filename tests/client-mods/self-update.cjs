// Run the installed official bundle's updater with local release responses.
// Its real filesystem writes must survive later package/client publication.
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const { isBuiltin } = require("node:module");
const path = require("node:path");
const vm = require("node:vm");
const asar = require("@electron/asar");

async function main() {
  const [name, target, output] = process.argv.slice(2);
  assert.ok(["Vencord", "Equicord"].includes(name));
  // Only operate on the disposable profile created by check_client_mods.py.
  const profile = path.join(path.dirname(path.resolve(output)), "profile");
  assert.ok(fs.realpathSync(target).startsWith(profile + path.sep));
  fs.mkdirSync(output);
  const marker = "dpe-self-update-v2";
  const additions = {
    "patcher.js": "\n// Self-update fixture v2.\n",
    "preload.js": "\n// Self-update fixture v2.\n",
    "renderer.js": `\n;globalThis.__DPE_MOD_SELF_UPDATE__ = ${JSON.stringify(marker)};\n`,
    "renderer.css": "\n/* Self-update fixture v2. */\n",
  };
  const bundled = name === "Equicord";
  const directory = bundled ? path.join(output, "source") : path.dirname(target);
  if (bundled) asar.extractAll(target, directory);
  const source = fs.readFileSync(path.join(directory, "patcher.js"), "utf8");
  const assets = new Map();
  if (bundled) {
    for (const [filename, extra] of Object.entries(additions))
      fs.appendFileSync(path.join(directory, filename), extra);
    const archive = path.join(output, "desktop.asar");
    await asar.createPackage(directory, archive);
    assets.set("desktop.asar", fs.readFileSync(archive));
  } else {
    for (const filename of fs.readdirSync(directory)) {
      if (!Object.keys(additions).some((prefix) => filename.startsWith(prefix))) continue;
      assets.set(
        filename,
        Buffer.concat([
          fs.readFileSync(path.join(directory, filename)),
          Buffer.from(additions[filename] || ""),
        ]),
      );
    }
  }
  const destinations = [...assets.keys()].map((filename) =>
    bundled ? target : path.join(directory, filename),
  );
  const hashes = () =>
    Object.fromEntries(
      destinations.map((filename) => [
        filename,
        createHash("sha256").update(fs.readFileSync(filename)).digest("hex"),
      ]),
    );
  const before = hashes();
  const fetchEvent = bundled ? "VencordFetchUpdate" : "VencordUpdate";
  const applyEvent = bundled ? "VencordApplyUpdate" : "VencordBuild";
  const handlers = new Map();
  const registered = new Error("Updater handlers registered");
  const electron = {
    ipcMain: {
      handle(event, callback) {
        handlers.set(event, callback);
        if (event === applyEvent) throw registered;
      },
      on() {},
    },
    app: { getPath: () => profile, getVersion: () => "0.0.0", on() {} },
  };
  let failDownload = false;
  const requests = [];
  async function fetchFixture(url) {
    requests.push(String(url));
    if (/^https:\/\/api.github.com\/repos\/[^/]+\/[^/]+\/releases\/latest$/.test(url))
      return Response.json({
        name: `Fixture ${marker}`,
        assets: [...assets.keys()].map((filename) => ({
          name: filename,
          browser_download_url: `https://updates.invalid/${filename}`,
        })),
      });
    assert.ok(String(url).startsWith("https://updates.invalid/"));
    if (failDownload) return new Response("Fixture download failed", { status: 503 });
    const data = assets.get(new URL(url).pathname.slice(1));
    assert.ok(data, `Unexpected update URL: ${url}`);
    return new Response(data);
  }
  function fixtureRequire(id) {
    if (id === "electron") return electron;
    if (id === "original-fs") return fs;
    assert.ok(isBuiltin(id), `Unexpected bundle import: ${id}`);
    return require(id);
  }
  const run = vm.runInThisContext(
    `(function(require,module,exports,__dirname,__filename,process,fetch){${source}\n})`,
    { filename: target },
  );
  try {
    // Stop after registration, before the bundle can start Discord. These are
    // the actual HTTP updater handlers; only Electron IPC and fetch are fixtures.
    run(
      fixtureRequire,
      { exports: {} },
      {},
      bundled ? target : directory,
      target,
      {
        env: { DISABLE_UPDATER_AUTO_PATCHING: "1" },
        platform: "linux",
        argv: [],
        versions: process.versions,
      },
      fetchFixture,
    );
    throw new Error("Official updater handlers were not registered");
  } catch (error) {
    if (error !== registered) throw error;
  }
  const available = await handlers.get(fetchEvent)();
  assert.ok(available.ok && available.value, JSON.stringify(available));
  failDownload = true;
  assert.equal((await handlers.get(applyEvent)()).ok, false);
  assert.deepEqual(hashes(), before);
  failDownload = false;
  const applied = await handlers.get(applyEvent)();
  assert.ok(applied.ok && applied.value, JSON.stringify(applied));
  for (const [filename, data] of assets)
    assert.deepEqual(fs.readFileSync(bundled ? target : path.join(directory, filename)), data);
  const after = hashes();
  assert.notDeepEqual(after, before);
  fs.writeFileSync(
    path.join(output, "report.json"),
    JSON.stringify({ name, marker, before, after, requests, fetchEvent, applyEvent }, null, 2),
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
