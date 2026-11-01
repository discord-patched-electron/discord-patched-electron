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

test("primary-paste control preserves unrelated Blink settings and follows per-setting precedence", () => {
  const item = require("../../integration/launcher/presets.json").experiments
    .disableMiddleClickPaste;
  const original =
    "# keep\n--blink-settings=middleClickPasteAllowed=true,preferredColorScheme=2\n--blink-settings=imagesEnabled=false";
  const disabled = editor.replace(original, [], item.flags);
  assert.equal(editor.selected(disabled, item), true);
  assert.match(disabled, /preferredColorScheme=2/);
  assert.match(disabled, /imagesEnabled=false/);
  assert.match(disabled, /# keep/);
  assert.equal(
    editor.selected(disabled + "\n--blink-settings=middleClickPasteAllowed=true", item),
    false,
  );
  const restored = editor.replace(disabled, item.flags, []);
  assert.doesNotMatch(restored, /middleClickPasteAllowed/);
  assert.match(restored, /preferredColorScheme=2/);
  assert.match(restored, /imagesEnabled=false/);
});

test("native scrolling controls remain independent and remove opposing overrides", () => {
  const { middleClickAutoscroll, disableSmoothScrolling } =
    require("../../integration/launcher/presets.json").experiments;
  let text = "--disable-features=MiddleClickAutoscroll,Other\n--enable-smooth-scrolling";
  text = editor.replace(text, [], middleClickAutoscroll.flags);
  text = editor.replace(text, [], disableSmoothScrolling.flags);
  assert.equal(editor.selected(text, middleClickAutoscroll), true);
  assert.equal(editor.selected(text, disableSmoothScrolling), true);
  assert.match(text, /--disable-features=Other/);
  assert.doesNotMatch(text, /--enable-smooth-scrolling/);
  assert.equal(
    editor.selected(text + "\n--enable-smooth-scrolling", disableSmoothScrolling),
    false,
    "Chromium's explicit enable takes precedence over disable when both are present",
  );
  text = editor.replace(text, disableSmoothScrolling.flags, []);
  assert.equal(editor.selected(text, middleClickAutoscroll), true);
  assert.equal(editor.selected(text, disableSmoothScrolling), false);
});

test("autoscroll forces the paste control on and preserves its independent saved preference", () => {
  const { middleClickAutoscroll, disableMiddleClickPaste } =
    require("../../integration/launcher/presets.json").experiments;
  for (const paste of ["", "true", "false"]) {
    const original = paste ? "--blink-settings=middleClickPasteAllowed=" + paste : "";
    const enabled = editor.replace(original, [], middleClickAutoscroll.flags);
    assert.equal(editor.selected(enabled, middleClickAutoscroll), true);
    assert.equal(editor.selected(enabled, disableMiddleClickPaste), true);
    assert.equal(editor.has(enabled, disableMiddleClickPaste.flags), paste === "false");
    const restored = editor.replace(enabled, middleClickAutoscroll.flags, []);
    assert.equal(restored, original);
    assert.equal(editor.selected(restored, disableMiddleClickPaste), paste === "false");
  }
});

test("autoscroll control follows native feature overrides including explicit Blink switches", () => {
  const { middleClickAutoscroll, disableMiddleClickPaste } =
    require("../../integration/launcher/presets.json").experiments;
  const cases = [
    [["--enable-features= Other, MiddleClickAutoscroll"], true],
    [["--enable-blink-features=Other, MiddleClickAutoscroll"], true],
    [["--enable-features=*MiddleClickAutoscroll"], false],
    [["--enable-blink-test-features", "--disable-features=*MiddleClickAutoscroll"], true],
    [["--enable-features=MiddleClickAutoscroll:Mode/test"], true],
    [
      ["--enable-features=MiddleClickAutoscroll", "--disable-features=MiddleClickAutoscroll"],
      false,
    ],
    [
      ["--disable-features=MiddleClickAutoscroll", "--enable-blink-features=MiddleClickAutoscroll"],
      true,
    ],
    [
      ["--enable-features=MiddleClickAutoscroll", "--disable-blink-features=MiddleClickAutoscroll"],
      false,
    ],
    [
      [
        "--disable-blink-features=MiddleClickAutoscroll",
        "--enable-blink-features=MiddleClickAutoscroll",
      ],
      false,
    ],
    [["--enable-blink-features=MiddleClickAutoscroll", "--enable-blink-features=Other"], false],
    [["--enable-blink-test-features"], true],
    [["--enable-blink-test-features", "--disable-features=MiddleClickAutoscroll"], false],
  ];
  for (const [args, enabled] of cases) {
    const text = ["--blink-settings=middleClickPasteAllowed=true", ...args].join("\n");
    assert.equal(editor.middleClickAutoscrollEnabled(text), enabled, text);
    assert.equal(editor.selected(text, middleClickAutoscroll), enabled, text);
    assert.equal(editor.selected(text, disableMiddleClickPaste), enabled, text);
  }
});

test("autoscroll switch edits explicit Blink aliases without changing unrelated features", () => {
  const { middleClickAutoscroll } = require("../../integration/launcher/presets.json").experiments;
  const text =
    "--enable-blink-features=MiddleClickAutoscroll,Other\n--disable-blink-features=Keep\n--enable-blink-test-features";
  const off = editor.replace(text, middleClickAutoscroll.flags, []);
  assert.equal(editor.selected(off, middleClickAutoscroll), false);
  assert.match(off, /--enable-blink-features=Other/);
  assert.match(off, /--disable-blink-features=Keep/);
  assert.match(off, /--enable-blink-test-features/);
  const on = editor.replace(
    off + "\n--disable-blink-features=MiddleClickAutoscroll,Keep",
    [],
    middleClickAutoscroll.flags,
  );
  assert.equal(editor.selected(on, middleClickAutoscroll), true);
  assert.match(on, /--disable-blink-features=Keep/);
  assert.doesNotMatch(on, /--disable-features=MiddleClickAutoscroll/);
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
