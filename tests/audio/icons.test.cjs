const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { createResolver } = require("../../integration/audio/app-icons.cjs");
const svg =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="blue"/></svg>';
async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "dpe-icons-"));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const root = path.join(directory, "share");
  async function write(name, value) {
    const filename = path.join(root, name);
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, value);
    return filename;
  }
  return {
    directory,
    root,
    write,
    resolve: createResolver({ dataDirs: [root], legacyIcons: "", extraRoots: [] }),
  };
}
test("icons resolve from hicolor and desktop entries without executing Exec", async (t) => {
  const f = await fixture(t);
  await f.write("icons/hicolor/scalable/apps/player.svg", svg);
  await f.write(
    "applications/org.example.Player.desktop",
    "[Desktop Entry]\nName=Player\nExec=must-not-run\nIcon=player\n[Desktop Action Quit]\nIcon=wrong\n",
  );
  const result = await f.resolve(["org.example.Player"]);
  assert.equal(Buffer.from(result.split(",")[1], "base64").toString(), svg);
  assert.equal(await f.resolve(["player"]), result);
});
test("pixmaps and absolute desktop icons are allowed only within data roots", async (t) => {
  const f = await fixture(t);
  const file = await f.write("pixmaps/branded.svg", svg);
  await f.write("applications/player.desktop", `[Desktop Entry]\nIcon=${file}\n`);
  assert.match(await f.resolve(["player"]), /^data:image\/svg\+xml;base64,/);
  const outside = path.join(f.directory, "outside.svg");
  await fs.writeFile(outside, svg);
  await f.write("applications/outside.desktop", `[Desktop Entry]\nIcon=${outside}\n`);
  await fs.symlink(outside, path.join(f.root, "pixmaps/escape.svg"));
  assert.equal(await f.resolve(["outside"]), null);
  assert.equal(await f.resolve(["escape"]), null);
});
test("invalid, remote, absent, oversized and non-image icons keep the generic fallback", async (t) => {
  const f = await fixture(t);
  for (const names of [
    null,
    [],
    ["../../outside"],
    ["https://example.com/icon.png"],
    ["missing"],
    ["/etc/passwd"],
  ]) {
    assert.equal(await f.resolve(names), null);
  }
  await f.write("pixmaps/large.svg", svg + " ".repeat(256 * 1024));
  await f.write("pixmaps/text.png", "not an image");
  assert.equal(await f.resolve(["large"]), null);
  assert.equal(await f.resolve(["text"]), null);
});
