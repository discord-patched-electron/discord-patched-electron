const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const source = require("../../integration/discord-ui/source.cjs");
const settings = require("../../integration/discord-ui/settings.cjs");

function fixture({ cached = false, icon = true } = {}) {
  const calls = [],
    executed = [];
  const context = vm.createContext({ calls, console });
  vm.runInContext(
    `
    globalThis.DiscordNative = { ipc: { send: (...args) => calls.push(args) } };
    globalThis.SettingsIcon = function SettingsIcon() { return { type: "svg" }; };
    globalThis.logout = Object.freeze({ key: "logout_sidebar_item", type: 42,
      variant: "destructive", icon: () => "exit", useTitle: () => "Déconnexion",
      onClick() { throw Error("must never log out"); }, buildLayout: () => [] });
    globalThis.items = Object.freeze([{key:"developer_sidebar_item"}, logout]);
    globalThis.sections = Object.freeze([{key:"vencord_section"},
      {key:"utility_section", buildLayout() { return items; }}]);
  `,
    context,
  );
  const factory = vm.runInContext(
    `(function(module) {
    const LOGOUT_SIDEBAR_ITEM = "logout_sidebar_item";
    module.exports = { renamed: { key: "$Root", analyticsKey: "user_settings",
      buildLayout() { return sections; } } };
    return LOGOUT_SIDEBAR_ITEM;
  })`,
    context,
  );
  const runtime = (id) => {
    if (!runtime.c[id]) {
      const module = { exports: {} };
      runtime.c[id] = module;
      executed.push(id);
      runtime.m[id](module, module.exports, runtime);
    }
    return runtime.c[id].exports;
  };
  runtime.m = cached ? { root: factory } : {};
  runtime.c = icon ? { icons: { exports: { SettingsIcon: context.SettingsIcon } } } : {};
  const chunks = [];
  chunks.push = function (chunk) {
    Array.prototype.push.call(this, chunk);
    Object.assign(runtime.m, chunk[1]);
    return chunk[2]?.(runtime);
  };
  context.webpackChunkdiscord_app = chunks;
  if (cached) runtime("root");
  const install = () => vm.runInContext(source.call(settings, {}), context);
  const load = () => chunks.push([["settings"], { root: factory }, (req) => req("root")]);
  const getItems = () =>
    runtime("root")
      .renamed.buildLayout()
      .find((x) => x.key === "utility_section")
      .buildLayout();
  return { context, runtime, chunks, calls, executed, install, load, getItems };
}

test("lazy native layout gets Discord's cog and ordinary sidebar action above translated Log Out", () => {
  const f = fixture();
  f.install();
  assert.deepEqual(f.executed, [], "discovery must not initialize settings or capture controllers");
  const result = f.load();
  assert.ok(result.renamed, "chunk callback result is preserved");
  const items = f.getItems(),
    entry = items[1];
  assert.deepEqual(
    Array.from(items, (x) => x.key),
    ["developer_sidebar_item", "dpe_openasar", "logout_sidebar_item"],
  );
  assert.equal(entry.type, f.context.logout.type);
  assert.equal(entry.icon, f.context.SettingsIcon);
  assert.equal(entry.useTitle(), "DPE OpenAsar");
  assert.equal(entry.variant, undefined, "do not inherit the destructive logout style");
  assert.equal(entry.buildLayout().length, 0);
  entry.onClick();
  assert.deepEqual(Array.from(f.calls[0]), ["DISCORD_UPDATED_QUOTES", "o"]);
  assert.equal(items[2], f.context.logout);
  assert.equal(f.context.items.length, 2, "native arrays are not mutated");
  assert.equal(f.context.sections[0].key, "vencord_section");
  f.install();
  assert.equal(f.getItems().filter((x) => x.key === "dpe_openasar").length, 1);
});

test("cached settings builders, repeated requests and sidebar rebuilds use one shared runtime", () => {
  const f = fixture({ cached: true });
  f.install();
  const original = f.runtime("root").renamed.buildLayout;
  const ui = vm.runInContext('globalThis[Symbol.for("dpe.discordUI")]', f.context);
  f.install();
  assert.equal(f.runtime("root").renamed.buildLayout, original);
  assert.equal(
    vm.runInContext(
      source.call((_request, shared) => shared, {}),
      f.context,
    ),
    ui,
  );
  for (let i = 0; i < 3; i++)
    assert.equal(f.getItems().filter((x) => x.key === "dpe_openasar").length, 1);
  assert.equal(f.chunks.length, 0, "runtime probes do not accumulate");
});

test("missing or ambiguous native icons do not substitute custom markup", () => {
  for (const ambiguous of [false, true]) {
    const f = fixture({ icon: ambiguous });
    if (ambiguous)
      f.runtime.c.other = {
        exports: {
          SettingsIcon: function () {
            return { type: "svg", other: true };
          },
        },
      };
    f.install();
    f.load();
    assert.equal(f.getItems().length, 2);
  }
});

test("another mod's factory wrapper and layout entries survive the shared module hook", () => {
  const f = fixture();
  const originalPush = f.chunks.push;
  f.chunks.push = function (chunk) {
    for (const [id, original] of Object.entries(chunk[1])) {
      const wrapper = function (...args) {
        const result = Reflect.apply(original, this, args);
        args[0].exports.fromMod = true;
        return result;
      };
      wrapper.toString = () => original.toString();
      chunk[1][id] = wrapper;
    }
    return originalPush.call(this, chunk);
  };
  f.install();
  assert.equal(f.load().fromMod, true);
  assert.equal(f.getItems()[1].key, "dpe_openasar");
});

test("changed layout without Log Out leaves Discord's list intact", () => {
  const f = fixture();
  f.install();
  f.load();
  vm.runInContext('items = Object.freeze([{key:"new_upstream_item"}])', f.context);
  assert.equal(f.getItems(), f.context.items);
});

test("settings registration waits for a late webpack loader without a DOM polling loop", () => {
  const f = fixture();
  const chunks = f.context.webpackChunkdiscord_app;
  const timers = [];
  f.context.setTimeout = (callback) => timers.push(callback);
  delete f.context.webpackChunkdiscord_app;
  f.install();
  assert.equal(timers.length, 1);
  f.context.webpackChunkdiscord_app = chunks;
  timers.shift()();
  f.load();
  assert.equal(f.getItems()[1].key, "dpe_openasar");
  assert.equal(timers.length, 0);
});
