const test = require("node:test");
const assert = require("node:assert/strict");
const editor = require("../../integration/updater/flag-editor.js");

test("checkbox edits preserve unrelated features, parameters, comments and literal values", () => {
  const input =
    "# keep\n--enable-features=Other:Mode/a=b,Decode\n--disable-features=Encode\n--custom=$(not-executed) a=b";
  const result = editor.replace(input, [], ["--enable-features=Encode"]);
  assert.match(result, /Other:Mode\/a=b,Decode/);
  assert.match(result, /# keep/);
  assert.ok(result.includes("--custom=$(not-executed) a=b"));
  assert.ok(editor.has(result, ["--enable-features=Encode,Decode"]));
  assert.ok(!result.includes("--disable-features=Encode"));
  assert.ok(
    !editor.has(editor.replace(result, ["--enable-features=Encode"], []), [
      "--enable-features=Encode",
    ]),
  );
});

test("changing renderer clears its old feature bundle and opposite switches", () => {
  const input = "--use-angle=vulkan\n--enable-features=Vulkan,Other\n--disable-gpu-rasterization";
  const result = editor.replace(
    input,
    ["--use-angle=vulkan", "--enable-features=Vulkan"],
    ["--use-angle=gl", "--enable-gpu-rasterization"],
  );
  assert.equal(result, "--enable-features=Other\n--use-angle=gl\n--enable-gpu-rasterization");
});

test("the editor shows final feature states after manual overrides", () => {
  assert.equal(
    editor.has("--enable-features=Video\n--disable-features=Video", ["--enable-features=Video"]),
    false,
  );
  assert.equal(
    editor.has("--disable-features=Video\n--enable-features=Video", ["--enable-features=Video"]),
    true,
  );
});

test("software fallbacks override forced acceleration without removing unrelated flags", () => {
  const catalog = require("../../integration/launcher/presets.json");
  const forced = catalog.experiments.forceAcceleration;
  const software = catalog.experiments.softwareDecode;
  const input = [...forced.flags, "--custom=kept"].join("\n");
  const raw = input + "\n--disable-accelerated-video-decode";
  assert.equal(editor.selected(raw, forced), false);
  assert.equal(editor.selected(raw, catalog.video.decode), false);
  assert.equal(editor.selected(raw, software), true);
  const fallback = editor.replace(input, software.removes, software.flags);
  assert.equal(editor.selected(fallback, software), true);
  assert.equal(editor.selected(fallback, catalog.video.decode), false);
  assert.match(fallback, /--custom=kept/);
  assert.match(fallback, /VaapiOnNvidiaGPUs/);
  const restored = editor.replace(fallback, forced.removes, forced.flags);
  assert.equal(editor.selected(restored, forced), true);
  assert.equal(editor.selected(restored, software), false);
  const cpu = editor.replace(restored, [], catalog.experiments.softwareRaster.flags);
  assert.equal(editor.selected(cpu, forced), false);
  assert.equal(editor.selected(cpu, catalog.experiments.softwareRaster), true);
  assert.equal(editor.selected(restored + "\n--disable-gpu", forced), false);
});

test("editing user overrides preserves external flags in inactive presets only once", () => {
  const config = {
    active: "one",
    extra: "# global\n--outside=old",
    profiles: { one: { flags: "--one" }, two: { flags: "--two" } },
  };
  editor.edit(config, "--outside=new");
  assert.equal(config.extra, "");
  assert.equal(config.profiles.one.flags, "--outside=new");
  assert.equal(config.profiles.one.replaceDefaults, undefined);
  assert.equal(config.profiles.two.flags, "--two\n# global\n--outside=old");
  editor.edit(config, "--outside=last");
  assert.equal(config.profiles.two.flags, "--two\n# global\n--outside=old");
});

test("convenience controls preserve dynamic app defaults and unrelated overrides", () => {
  const catalog = require("../../integration/launcher/presets.json");
  const profile = {
    flags: "--custom=kept",
    backend: "auto",
    decode: false,
    encode: false,
    experiments: [],
  };
  const config = {
    active: "one",
    extra: "--global=kept",
    profiles: { one: profile, two: { ...profile } },
  };
  editor.configure(config, catalog, [], catalog.video.decode.flags);
  assert.equal(profile.replaceDefaults, undefined);
  assert.match(profile.flags, /AcceleratedVideoDecoder/);
  assert.match(profile.flags, /--custom=kept/);
  assert.doesNotMatch(profile.flags, /--ozone-platform|--openh264-library-path/);
  editor.configure(config, catalog, catalog.video.decode.flags, []);
  assert.doesNotMatch(profile.flags, /AcceleratedVideoDecoder/);
  assert.equal(profile.flags.split("--global=kept").length, 2);
});
