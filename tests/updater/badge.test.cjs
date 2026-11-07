const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const { createController } = require("../../integration/desktop/preferences.cjs");
const { patch } = require("../../integration/desktop/preferences-patch.cjs");
const trayPatch = require("../../integration/updater/tray-patch.cjs");

function fixture(settings = {}) {
  const app = { isReady: () => true, setBadgeCount: (count) => (app.count = count) };
  const controller = createController({ app }, () => settings);
  let refreshes = 0;
  const state = { unread: true, connected: false, muteStates: {}, deafStates: {} };
  let displayed;
  controller.registerTray(
    () => {},
    () => {},
    () => {
      refreshes++;
      displayed = controller.trayStates(state);
    },
  );
  return { app, controller, state, displayed: () => displayed, refreshes: () => refreshes };
}

test("mentions-only tray uses numeric notifications, never ordinary guild unread", async () => {
  const f = fixture({ badgeOnlyMentions: true });
  assert.equal(f.controller.trayStates(f.state).unread, false, "unknown count is not a mention");
  for (const count of [0, -1, true, "1", NaN, Infinity]) {
    f.controller.setBadgeCount(f.app, count);
    assert.equal(f.displayed().unread, false);
  }
  for (const count of [1, 4]) {
    f.controller.setBadgeCount(f.app, count);
    assert.equal(f.displayed().unread, true, "DM or mention notification remains visible");
    assert.equal(f.app.count, count);
  }
  f.controller.setBadgeCount(f.app, 0);
  assert.equal(f.displayed().unread, false, "last mention cleared while guild remains unread");
  assert.equal(f.state.unread, true, "raw Discord state is preserved");
  await f.controller.apply({});
  assert.equal(f.displayed().unread, true, "all-unread mode restores ordinary unread live");
  await f.controller.apply({ badgeOnlyMentions: true });
  assert.equal(f.displayed().unread, false);
  f.controller.setBadgeCount(f.app, 2);
  await f.controller.apply({ appBadge: false, badgeOnlyMentions: true });
  assert.equal(f.app.count, 0);
  assert.equal(f.displayed().unread, false);
  await f.controller.apply({ badgeOnlyMentions: true });
  assert.equal(f.app.count, 2);
  assert.equal(f.displayed().unread, true);
  f.state.unread = false;
  assert.equal(
    f.controller.trayStates(f.state).unread,
    false,
    "respect upstream unread suppression",
  );
});

test("dock and overlay count updates refresh tray and clear stale notification state", async () => {
  const f = fixture({ badgeOnlyMentions: true });
  const dock = { setBadge: (text) => (dock.text = text) };
  f.controller.setDockBadge(dock, "12");
  assert.equal(f.displayed().unread, true);
  f.controller.setDockBadge(dock, "•");
  assert.equal(dock.text, "");
  assert.equal(f.displayed().unread, false);
  let overlay;
  const update = (count) => {
    f.controller.rememberOverlay(count, () => update(count));
    overlay = f.controller.badgeCount(count);
  };
  update(3);
  assert.equal(f.displayed().unread, true);
  update(-1);
  assert.equal(overlay, 0);
  assert.equal(f.displayed().unread, false);
  await f.controller.apply({});
  assert.equal(overlay, -1);
  assert.equal(f.displayed().unread, true);
  update(0);
  assert.equal(overlay, 0);
  assert.ok(f.refreshes() > 0);
});

test("native tray transform filters legacy and structured states before stock/custom icon selection", async () => {
  const { controller, app } = fixture({ badgeOnlyMentions: true });
  // Retained native state/icon selectors, with synthetic assets and no account.
  const source = `
    let currentIconKey="UNREAD",currentTrayStates=null;
    const IDLE_STATES={connected:false,unread:false,muteStates:{},deafStates:{}};
    function getTrayStates(){return currentTrayStates??function(iconKey){switch(iconKey){case"UNREAD":return {...IDLE_STATES,unread:true};default:return null}}(currentIconKey)??IDLE_STATES}
    function getTrayIcon(){let{muteStates,deafStates,connected,speaking,unread}=getTrayStates();return connected?deafStates.selfDeaf||deafStates.serverDeaf?trayIcons.DEAFENED:muteStates.selfMute||muteStates.serverMute||muteStates.suppress?trayIcons.MUTED:speaking?trayIcons.SPEAKING:trayIcons.CONNECTED:unread?trayIcons.UNREAD:trayIcons.DEFAULT}
    function create(){(atomTray).setToolTip(currentStrings.appName),setContextMenu()}
  `;
  const context = {
    atomTray: { setToolTip() {} },
    trayIcons: { DEFAULT: "normal", UNREAD: "unread", MUTED: "muted", SPEAKING: "speaking" },
    currentStrings: { appName: "Fixture" },
    setContextMenu() {},
    require: (name) =>
      name === "preferences"
        ? controller
        : {
            image: (stock, state) => (state.unread && !state.connected ? "custom-unread" : stock),
            register() {},
          },
  };
  const transformed = patch(source, "preferences", (text, before, after) =>
    text.replace(before, after),
  );
  vm.createContext(context);
  vm.runInContext(trayPatch.patch(transformed, "tray"), context);
  const icon = () => vm.runInContext("getTrayIcon()", context);
  assert.equal(icon(), "normal", "legacy UNREAD key cannot bypass filtering");
  controller.setBadgeCount(app, 1);
  assert.equal(icon(), "custom-unread");
  vm.runInContext("currentTrayStates={...IDLE_STATES,unread:true}", context);
  controller.setBadgeCount(app, 0);
  assert.equal(icon(), "normal", "structured unread cannot bypass filtering");
  await controller.apply({});
  assert.equal(icon(), "custom-unread");
  await controller.apply({ badgeOnlyMentions: true });
  vm.runInContext(
    "currentTrayStates={...currentTrayStates,connected:true,muteStates:{selfMute:true}}",
    context,
  );
  assert.equal(icon(), "muted");
  vm.runInContext("currentTrayStates={...currentTrayStates,muteStates:{},speaking:true}", context);
  assert.equal(icon(), "speaking", "voice states are preserved");
});

test("native tray registration refreshes existing icons on counts/settings and survives destruction", async () => {
  const { controller, app } = fixture({ badgeOnlyMentions: true });
  let registration;
  patch("", "preferences", (source, before, after) => {
    if (before.startsWith(",(IS_WINDOWS||IS_LINUX)")) registration = after;
    return source;
  });
  const tray = {
    destroyed: false,
    isDestroyed() {
      return this.destroyed;
    },
    destroy() {
      this.destroyed = true;
    },
    setImage(value) {
      this.image = value;
    },
  };
  const context = {
    atomTray: tray,
    require: () => controller,
    createTray() {},
    getTrayIcon: () => (controller.trayStates({ unread: true }).unread ? "unread" : "default"),
  };
  vm.runInNewContext(registration.slice(1, registration.indexOf(",(IS_WINDOWS")), context);
  controller.setBadgeCount(app, 1);
  assert.equal(tray.image, "unread");
  controller.setBadgeCount(app, 0);
  assert.equal(tray.image, "default");
  await controller.apply({});
  assert.equal(tray.image, "unread");
  await controller.apply({ badgeOnlyMentions: true });
  assert.equal(tray.image, "default");
  await controller.apply({ tray: false });
  controller.setBadgeCount(app, 2);
  assert.equal(tray.destroyed, true);
  assert.equal(tray.image, "default", "destroyed tray receives no image updates");
});
