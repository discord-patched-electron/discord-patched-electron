const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const presentation = require("../../integration/discord-ui/presentation.cjs");
const style = (values) => ({ getPropertyValue: (key) => values[key] || "" });
const rule = (selectorText, values) => ({ selectorText, style: style(values) });

function fixture(rules = []) {
  const root = { values: { "font-size": "20px" }, classList: { contains: () => false } };
  const nodes = new Map();
  const sheet = { cssRules: rules };
  const context = vm.createContext({
    document: {
      documentElement: root,
      styleSheets: [sheet],
      querySelector: (query) => nodes.get(query) || null,
      querySelectorAll: () => [],
    },
    matchMedia: () => ({ matches: false }),
    getComputedStyle: (node) => style(node.values),
    ui: { runtimes: () => [], safe: (value, key) => value?.[key] },
  });
  const run = () =>
    vm.runInContext(`(${presentation.toString()})({ iconsOnly:true }, ui)`, context);
  const refresh = () =>
    vm.runInContext('globalThis[Symbol.for("dpe.discordPresentation")].geometryAttempt=0', context);
  return { root, nodes, sheet, run, refresh };
}

test("the Flags icon never borrows Discord's unrelated System sidebar drawing", () => {
  const f = fixture();
  f.nodes.set('[data-settings-sidebar-item="system_panel"] svg', {
    localName: "svg",
    attributes: [{ name: "viewBox", value: "0 0 24 24" }],
    children: [],
  });
  assert.equal(f.run().icons.flag, undefined);
  assert.equal(f.run().icons.flags, undefined);
});

test("native geometry follows runtime font, component styles and live settings without screen zoom calculations", () => {
  const f = fixture([
    rule(".size-xl_changedBuild", { "max-width": "72rem" }),
    rule(".auto_changedBuild::-webkit-scrollbar", { width: "20px" }),
  ]);
  f.nodes.set('[class*="text-sm/normal_"]', {
    values: { "font-size": "17.5px", "line-height": "22.5px" },
  });
  const sidebar = {
    values: { width: "280px" },
    getClientRects: () => [{}],
    parentElement: { querySelector: () => null },
  };
  f.nodes.set("aside:has([data-settings-sidebar-item])", sidebar);
  let tokens = f.run().tokens;
  assert.equal(tokens["--dpe-discord-root-font-size"], "20px");
  assert.equal(
    tokens["--dpe-discord-modal-width"],
    "72rem",
    "rem dimensions remain responsive to the actual root font",
  );
  assert.equal(tokens["--dpe-discord-scrollbar-width"], "20px");
  assert.equal(tokens["--dpe-discord-sidebar-width"], "280px");
  assert.equal(tokens["--dpe-discord-text-sm-size"], "17.5px");
  assert.equal(tokens["--dpe-discord-text-sm-line-height"], "22.5px");
  sidebar.values = {
    width: "240px",
    "box-sizing": "content-box",
    "padding-left": "12px",
    "border-right-width": "1px",
  };
  assert.equal(
    f.run().tokens["--dpe-discord-sidebar-width"],
    "253.0000px",
    "native content-box dimensions include their padding and border without screenshot/zoom conversion",
  );
  f.root.values["font-size"] = "18px";
  sidebar.getClientRects = () => [];
  tokens = f.run().tokens;
  assert.equal(tokens["--dpe-discord-root-font-size"], "18px");
  assert.equal(
    tokens["--dpe-discord-sidebar-width"],
    "253.0000px",
    "a hidden lazy view retains its last valid geometry",
  );
});

test("unknown, conflicting, unsafe and unavailable CSS rules keep bounded layout fallbacks", () => {
  for (const rules of [
    [rule(".unrelated", { "max-width": "1100px" })],
    [
      rule(".size-xl_new", { "max-width": "1000px" }),
      rule(".size-xl_other", { "max-width": "1400px" }),
    ],
    [rule(".size-xl_new", { "max-width": "999999px" })],
    [rule(".size-xl_new", { "max-width": "url(https://invalid.example/)" })],
  ])
    assert.equal(fixture(rules).run().tokens["--dpe-discord-modal-width"], undefined);
  const f = fixture([rule(".size-xl_new", { "max-width": "1040px" })]);
  assert.equal(f.run().tokens["--dpe-discord-modal-width"], "1040px");
  Object.defineProperty(f.sheet, "cssRules", {
    get() {
      throw Error("stylesheet unavailable");
    },
  });
  f.refresh();
  assert.equal(f.run().tokens["--dpe-discord-modal-width"], "1040px");
});

test("settings panel widths follow the native centered content-box rule and reject unrelated declarations", () => {
  const centered = { "margin-inline": "auto", position: "relative" };
  const f = fixture([
    rule(".panel_changedBuild", { ...centered, "max-width": "44rem", "min-width": "320px" }),
  ]);
  assert.equal(f.run().tokens["--dpe-discord-settings-panel-max-width"], "44rem");
  assert.equal(f.run().tokens["--dpe-discord-settings-panel-min-width"], "320px");
  for (const rules of [
    [rule(".panel_otherFeature", { "max-width": "900px" })],
    [rule(".panel_changedBuild", { ...centered, "max-width": "url(https://invalid.example/)" })],
    [rule(".panel_changedBuild", { ...centered, "max-width": "99999px" })],
    [
      rule(".panel_first", { ...centered, "max-width": "696px" }),
      rule(".panel_other", { ...centered, "max-width": "900px" }),
    ],
  ])
    assert.equal(fixture(rules).run().tokens["--dpe-discord-settings-panel-max-width"], undefined);
});
