const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { test } = require("node:test");
const assetHandler = require("../../integration/updater/splash-assets.cjs");
const splashState = require("../../integration/updater/splash-state.cjs");
const source = require("../../integration/updater/openasar/source.json");

test("the screen matches upstream exactly and cannot serve arbitrary files", async () => {
  const handler = assetHandler("/unused", () => assert.fail("HTML needs no network"));
  const response = await handler({ url: "dpe-splash://app/index.html" });
  assert.equal(response.status, 200);
  assert.equal(
    createHash("sha256")
      .update(await response.text())
      .digest("hex"),
    source.html_sha256,
  );
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

test("parallel download and install progress use OpenAsar states and retry countdown", () => {
  const translate = splashState();
  assert.deepEqual(translate({ stage: "check" }), { status: "checking-for-updates" });
  assert.deepEqual(translate({ stage: "download", label: "Electron", current: 5, total: 10 }), {
    status: "downloading",
    current: 1,
    total: 1,
    progress: 50,
  });
  translate({ stage: "batch", total: 2 });
  translate({ stage: "download", label: "voice", current: 10, total: 10 });
  assert.deepEqual(translate({ stage: "download", label: "core", current: 5, total: 10 }), {
    status: "downloading",
    current: 2,
    total: 2,
    progress: 75,
  });
  assert.deepEqual(translate({ stage: "installed", label: "voice" }), {
    status: "installing",
    current: 2,
    total: 2,
    progress: 50,
  });
  assert.deepEqual(translate({ stage: "error" }), { status: "fail", seconds: 10 });
  assert.deepEqual(translate({ stage: "ready" }), { status: "starting" });
});
