const assert = require("node:assert/strict");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const append = require("../../integration/updater/flags.cjs");
const { bootstrap } = require("../../integration/updater/patches.cjs");

function fixture(initial = {}) {
  const switches = { ...initial };
  const commandLine = {
    getSwitchValue: (name) => switches[name] || "",
    hasSwitch: (name) => Object.hasOwn(switches, name),
    appendSwitch: (name, value) => (switches[name] = value),
  };
  return { switches, commandLine };
}

for (const feature of ["EnableDrDc", "EnableDrDc:mode/value", "EnableDrDc<Trial"]) {
  test(`OpenAsar cannot force unsupported Linux rendering with ${feature}`, () => {
    const { switches, commandLine } = fixture({
      "enable-features": "AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL",
    });
    append(commandLine, "enable-features", `${feature},CanvasOopRasterization`);
    assert.equal(
      switches["enable-features"],
      "AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL,CanvasOopRasterization",
    );
  });
}

test("filtering the only preset feature leaves no empty switch", () => {
  const { switches, commandLine } = fixture();
  append(commandLine, "enable-features", "EnableDrDc");
  assert.deepEqual(switches, {});
});

test("presets keep the driver blocklist without disabling other performance and battery flags", () => {
  const { switches, commandLine } = fixture();
  append(commandLine, "ignore-gpu-blocklist", "");
  append(commandLine, "enable-zero-copy", "");
  append(commandLine, "force_low_power_gpu", "");
  append(commandLine, "disable-features", "EnableDrDc,Vulkan");
  assert.deepEqual(switches, {
    "enable-zero-copy": "",
    force_low_power_gpu: "",
    "disable-features": "EnableDrDc,Vulkan",
  });
});

test("explicit launcher overrides remain authoritative", () => {
  const explicit = {
    "enable-features": "EnableDrDc:mode/user,Keep:Param/value",
    "disable-features": "CanvasOopRasterization",
    "ignore-gpu-blocklist": "",
    "enable-hardware-overlays": "",
  };
  const { switches, commandLine } = fixture(explicit);
  append(commandLine, "enable-features", "EnableDrDc,Keep,CanvasOopRasterization");
  append(commandLine, "ignore-gpu-blocklist", "");
  append(commandLine, "enable-hardware-overlays", "single-fullscreen,single-on-top,underlay");
  assert.deepEqual(switches, explicit);
});

test("the bootstrap routes OpenAsar's default preset through the GPU compatibility filter", () => {
  const { switches, commandLine } = fixture({
    "enable-features": "AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL",
  });
  const source = `
    const c = {
      '--enable-features': ['EnableDrDc,CanvasOopRasterization,UseSkiaRenderer'],
      '--ignore-gpu-blocklist': [''],
      '--disable-features': ['Vulkan'],
      '--autoplay-policy': ['no-user-gesture-required'],
    };
    for (const k in c) {
      app.commandLine.appendSwitch(k.replace('--', ''), c[k].join(','));
    }
  `;
  vm.runInNewContext(bootstrap(source, "cmdSwitches.js"), {
    app: { commandLine },
    process: { env: { DPE_APP_ROOT: path.resolve(__dirname, "../..") } },
    require,
  });
  assert.deepEqual(switches, {
    "enable-features":
      "AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL,CanvasOopRasterization,UseSkiaRenderer",
    "disable-features": "Vulkan",
    "autoplay-policy": "no-user-gesture-required",
  });
});
