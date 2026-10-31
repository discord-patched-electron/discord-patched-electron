const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const native = require("../../integration/updater/native-presentation.cjs");
const { sanitize } = require("../../integration/updater/presentation.cjs");
const origin = "https://canary.discord.com";
const svg = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M1 2L3 4Z"></path></svg>';
const snapshot = (color = "#111") => ({
  tokens: Object.fromEntries(Array.from({ length: 120 }, (_, i) => ["--primary-" + i, color])),
  icons: { settings: svg },
  classes: ["theme-dark"],
  fonts: [],
  theme: "dark",
});
async function profile(t) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "dpe-native-presentation-"));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  return folder;
}

test("asset renderer allows only unauthenticated public presentation GETs", () => {
  for (const [resourceType, pathname] of [
    ["mainFrame", "/login"],
    ["script", "/assets/app.123.js"],
    ["stylesheet", "/assets/native.css"],
    ["font", "/assets/font.woff2"],
  ])
    assert.equal(
      native.allowedRequest({ method: "GET", url: origin + pathname, resourceType }, origin),
      true,
    );
  for (const details of [
    { url: origin + "/api/v9/users/@me", resourceType: "xhr" },
    { url: origin + "/api/v9/experiments", resourceType: "xhr" },
    { url: "wss://remote-auth-gateway.discord.gg/", resourceType: "webSocket" },
    { url: origin + "/assets/tracker.png", resourceType: "image" },
    { url: origin + "/assets/app.js?credential=private", resourceType: "script" },
    { url: "https://example.invalid/assets/app.js", resourceType: "script" },
    { url: "https://user@canary.discord.com/assets/app.js", resourceType: "script" },
    { url: "file:///etc/passwd", resourceType: "other" },
    { url: origin + "/assets/frame.js", resourceType: "subFrame" },
    { url: origin + "/assets/app.js", resourceType: "script", method: "POST" },
  ])
    assert.equal(native.allowedRequest({ method: "GET", ...details }, origin), false);
});

test("native snapshots are integrity checked, sanitized and replayable without Electron or network", async (t) => {
  const folder = await profile(t);
  await native.remember({
    profile: folder,
    origin,
    snapshot: { ...snapshot(), tokens: { ...snapshot().tokens, "--bad": "url(https://invalid/)" } },
    sanitize,
    kind: "public-assets",
  });
  const result = await native.acquire({ profile: folder, origin, sanitize, offline: true });
  assert.equal(result.snapshot.tokens["--primary-0"], "#111");
  assert.equal(result.snapshot.tokens["--bad"], undefined);
  assert.equal(result.snapshot.icons.settings, svg);
  assert.equal(result.origin, origin);
  const filename = path.join(
    folder,
    "dpe-updater/native-presentation/canary.discord.com/public-assets.json",
  );
  const record = JSON.parse(await fs.readFile(filename, "utf8"));
  record.payload = record.payload.replace("#111", "#fff");
  await fs.writeFile(filename, JSON.stringify(record));
  assert.equal(
    await native.acquire({ profile: folder, origin, sanitize, offline: true }),
    undefined,
  );
});

test("cached user theme, typography and zoom win over a public login shell", async (t) => {
  const folder = await profile(t);
  await native.remember({
    profile: folder,
    origin,
    snapshot: snapshot(),
    sanitize,
    kind: "public-assets",
  });
  const personal = {
    ...snapshot("#000"),
    classes: ["theme-dark", "theme-midnight"],
    fontFamily: '"gg sans"',
    icons: {},
  };
  await native.remember({ profile: folder, origin, snapshot: personal, sanitize, zoom: 1.25 });
  const result = await native.acquire({ profile: folder, origin, sanitize, offline: true });
  assert.equal(result.source, "client");
  assert.equal(result.snapshot.tokens["--primary-0"], "#000");
  assert.equal(result.snapshot.fontFamily, '"gg sans"');
  assert.deepEqual(result.snapshot.classes, ["theme-dark", "theme-midnight"]);
  assert.equal(result.zoom, 1.25);
  assert.equal(result.snapshot.icons.settings, svg);
});

test("official font bytes are bounded, cached with integrity and usable offline", async (t) => {
  const folder = await profile(t);
  const url = origin + "/assets/verified.woff2";
  const bytes = Buffer.from("wOF2fixture-font");
  let fetched = 0;
  const fetcher = async (address, options) => {
    fetched++;
    assert.equal(address, url);
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    return { ok: true, url, body: [bytes] };
  };
  assert.equal(
    await native.font(url, { profile: folder, fetch: fetcher }),
    bytes.toString("base64"),
  );
  assert.equal(
    await native.font(url, {
      profile: folder,
      offline: true,
      fetch: () => assert.fail("Offline font request"),
    }),
    bytes.toString("base64"),
  );
  assert.equal(fetched, 1);
  for (const invalid of [
    "http://canary.discord.com/assets/font.woff2",
    "https://example.invalid/assets/font.woff2",
    origin + "/api/font.woff2",
    url + "?token=private",
  ])
    assert.equal(
      await native.font(invalid, {
        profile: folder,
        fetch: () => assert.fail("Invalid font request"),
      }),
      "",
    );
  assert.equal(
    await native.font(origin + "/assets/invalid.woff2", {
      profile: folder,
      fetch: async () => ({ ok: true, body: [Buffer.from("not a font")] }),
    }),
    "",
  );
  assert.equal(
    await native.font(origin + "/assets/redirect.woff2", {
      profile: folder,
      fetch: async () => ({ ok: true, url: "https://example.invalid/font.woff2", body: [bytes] }),
    }),
    "",
  );
});
