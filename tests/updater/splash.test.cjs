const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { test } = require("node:test");
const assetHandler = require("../../integration/updater/splash-assets.cjs");
const splashState = require("../../integration/updater/splash-state.cjs");
const source = require("../../integration/updater/openasar/source.json");

test("modern splash keeps the OpenAsar contract and cannot serve arbitrary files", async () => {
  const handler = assetHandler("/unused", () => assert.fail("HTML needs no network"));
  const response = await handler({ url: "dpe-splash://app/index.html" });
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /ggsans-500-medium.woff2/);
  assert.match(html, /Splash.onState/);
  assert.equal((html.match(/role="progressbar"/g) || []).length, 1);
  assert.match(response.headers.get("content-security-policy"), /default-src 'none'/);
  for (const url of [
    "dpe-splash://elsewhere/index.html",
    "dpe-splash://app/LICENSE",
    "dpe-splash://app/%2e%2e/source.json",
  ]) {
    assert.equal((await handler({ url })).status, 404);
  }
});

test("splash media is pinned, cached outside the app, and reusable offline", async (t) => {
  const cache = await fs.mkdtemp(path.join(os.tmpdir(), "dpe-splash-"));
  t.after(() => fs.rm(cache, { recursive: true, force: true }));
  const data = Buffer.from("fixture font");
  const original = source.assets["whitney_400.woff"];
  source.assets["whitney_400.woff"] = {
    ...original,
    size: data.length,
    sha256: createHash("sha256").update(data).digest("hex"),
  };
  t.after(() => {
    source.assets["whitney_400.woff"] = original;
  });
  const url = "dpe-splash://app/whitney_400.woff";
  let requests = 0;
  const handler = assetHandler(cache, async (remote) => {
    requests++;
    assert.equal(
      remote,
      `https://raw.githubusercontent.com/OpenAsar/cdn/${source.commit}/splash/whitney_400.woff`,
    );
    return new Response(data);
  });
  const responses = await Promise.all([handler({ url }), handler({ url })]);
  for (const response of responses) assert.equal(await response.text(), data.toString());
  assert.equal(requests, 1);
  const offline = assetHandler(cache, () => assert.fail("offline cache must not fetch"), true);
  assert.equal(await (await offline({ url })).text(), data.toString());
  const cached = path.join(cache, (await fs.readdir(cache))[0]);
  await fs.writeFile(cached, "corrupt");
  const invalid = assetHandler(cache, async () => new Response("bad"));
  assert.equal((await invalid({ url })).status, 503);
  const absent = assetHandler(cache, () => assert.fail("offline must not fetch"), true);
  assert.equal((await absent({ url })).status, 503);
});

test("upstream settings and its local editor work without remote executable code", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "openasar-editor-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "monaco-editor/min/vs"), { recursive: true });
  await fs.writeFile(path.join(root, "monaco-editor/min/vs/loader.js"), "fixture editor");
  const handler = assetHandler("/unused", () => assert.fail("settings must be local"), true, root);
  const config = await handler({ url: "dpe-splash://app/config.html" });
  assert.equal(config.status, 200);
  const adapted = await config.text();
  assert.match(adapted, /id="t_flags" role="tab"/);
  assert.equal((adapted.match(/<textarea/g) || []).length, 2);
  assert.match(adapted, /id="essential-section"/);
  assert.doesNotMatch(adapted, /id="preset"/);
  assert.ok(!adapted.includes("[ 'Focus',"));
  assert.equal(
    createHash("sha256")
      .update(
        await fs.readFile(path.join(__dirname, "../../integration/updater/openasar/config.html")),
      )
      .digest("hex"),
    source.config_sha256,
  );
  for (const name of [
    "launch-settings.js",
    "launch-settings.css",
    "config-tabs.js",
    "flag-editor.js",
  ]) {
    const local = await handler({ url: "dpe-splash://app/" + name });
    assert.equal(local.status, 200);
    assert.match(local.headers.get("content-security-policy"), /connect-src 'none'/);
  }
  const editor = await handler({ url: "dpe-splash://app/monaco-editor/min/vs/loader.js" });
  assert.equal(await editor.text(), "fixture editor");
  assert.match(editor.headers.get("content-type"), /javascript/);
  for (const name of [
    "monaco-editor/LICENSE",
    "monaco-editor/min/%2e%2e/secret.js",
    "monaco-editor/min/vs/%2fetc/passwd",
  ])
    assert.equal((await handler({ url: "dpe-splash://app/" + name })).status, 404);
});

test("one bar combines overlapping tasks, never regresses, and resets on retry", () => {
  const translate = splashState();
  translate({ stage: "batch", total: 2 });
  translate({ stage: "download", label: "discord_voice", current: 10, total: 10 });
  let state = translate({
    stage: "download",
    label: "discord_desktop_core",
    current: 5,
    total: 10,
  });
  assert.equal(state.progress, 37.5);
  state = translate({ stage: "install", label: "discord_voice" });
  assert.equal(state.progress, 37.5);
  state = translate({ stage: "installed", label: "discord_voice" });
  assert.equal(state.progress, 62.5);
  assert.equal(state.current, 2);
  assert.equal(state.status, "downloading");
  state = translate({ stage: "download", label: "discord_voice", current: 1, total: 10 });
  assert.equal(state.progress, 62.5);
  state = translate({ stage: "installed", label: "discord_desktop_core" });
  assert.equal(state.progress, 100);
  assert.equal(state.status, "installing");
  assert.deepEqual(translate({ stage: "error" }), { status: "fail", seconds: 10 });
  state = translate({ stage: "download", label: "Electron", current: -1, total: 0 });
  assert.equal(state.progress, 0);
  state = translate({ stage: "installed", label: "Electron" });
  assert.equal(state.progress, 100);
  assert.equal(state.label, "Electron");
  assert.deepEqual(translate({ stage: "ready" }), { status: "starting" });
});
