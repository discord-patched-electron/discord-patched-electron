// Called by the native audio smoke so both adapters run in the same page world.
const assert = require("node:assert/strict");
const source = require("../../integration/discord-ui/source.cjs");
const settings = require("../../integration/discord-ui/settings.cjs");
module.exports = async function checkSettings(run) {
  await run(source.call(settings, {}));
  const result = await run(`(async () => {
    const ui = globalThis[Symbol.for("dpe.discordUI")];
    const req = ui.runtimes("dpe-settings-check").find(runtime => ui.inspect(runtime).React);
    if (!req) throw Error("Discord runtime unavailable");
    const entryPoints = Object.values(req.m).map(String).filter(source =>
      source.includes('"USER_SETTINGS_MODAL_OPEN"') && source.includes("openModalLazy") && source.includes(".e("));
    if (entryPoints.length !== 1) throw Error("Settings entry point changed: " + entryPoints.length);
    const ids = [...entryPoints[0].matchAll(/\\.e\\("(\\d+)"\\)/g)].map(match => match[1]);
    // Register presentation chunks; never invoke account settings actions.
    await Promise.all(ids.map(id => req.e(id)));
    const roots = Object.entries(req.m).filter(([,factory]) =>
      String(factory).includes("LOGOUT_SIDEBAR_ITEM") && String(factory).includes('"user_settings"'));
    if (roots.length !== 1) throw Error("Settings root changed: " + roots.length);
    const exports = req(roots[0][0]);
    const root = ui.inspect({c:{root:{exports}}}).objects.find(value => value.key === "$Root");
    const utility = root.buildLayout().find(node => node.key === "utility_section");
    const compiler = Object.entries(req.m).find(([,factory]) => String(factory).includes(".buildLayout().map"));
    const compile = Object.values(req(compiler[0])).find(value => typeof value === "function" && String(value).includes(".buildLayout().map"));
    const compiled = compile(utility);
    const index = compiled.layout.findIndex(node => node.key === "dpe_openasar");
    const entry = compiled.layout[index];
    if (!entry) throw Error("DPE native settings entry was not installed");
    const icon = ui.inspect(req).choose("SettingsIcon", (s,names) => names.has("SettingsIcon") && ui.has(s,"svg"));
    return {
      label: entry.useTitle(), next: compiled.layout[index+1]?.key,
      nativeIcon: entry.icon === icon, type: entry.type,
      nativeType: compiled.layout[index+1]?.type,
      variant: entry.variant || "default", parent: entry.parent === compiled,
      count: compiled.layout.filter(node => node.key === "dpe_openasar").length,
      emptyPanel: entry.layout.length === 0, chunks: ids.length,
    };
  })()`);
  assert.equal(result.label, "DPE OpenAsar");
  assert.equal(result.next, "logout_sidebar_item");
  assert.equal(result.nativeIcon, true);
  assert.equal(result.type, result.nativeType);
  assert.equal(result.variant, "default");
  assert.equal(result.parent, true);
  assert.equal(result.count, 1);
  assert.equal(result.emptyPanel, true);
  return result;
};
