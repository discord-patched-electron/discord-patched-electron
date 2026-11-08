const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

module.exports = async function ({
  get,
  click,
  until,
  evaluate,
  capture,
  window,
  protocolState,
  protocol,
  values,
  settingsPage,
  profile,
  root,
  setWindow,
}) {
  const setProtocol = (handler, fail = false) =>
    fs.writeFileSync(protocolState, JSON.stringify({ handler, fail }));
  await until(() => get('$("desktop-discord-links") && !$("desktop-discord-links").disabled'));
  assert.match(await get('$("desktop-discord-links-help").textContent'), /Another app/);
  setProtocol("other-discord.desktop", true);
  await click("desktop-discord-links");
  await until(() => get('$("desktop-preferences-error")?.textContent.includes("Could not make")'));
  assert.equal(protocol().handler, "other-discord.desktop");
  setProtocol("other-discord.desktop");
  await evaluate('document.getElementById("desktop-discord-links").focus()');
  await click("desktop-discord-links");
  await until(() =>
    get(
      '$("desktop-discord-links").disabled && $("desktop-discord-links").textContent.trim() === "In use"',
    ),
  );
  assert.equal(protocol().handler, "discord-canary.desktop");
  await until(() => evaluate('document.activeElement.id === "desktop-discord-links-help"'));
  assert.equal(values.openasar.desktop?.discordLinks, undefined);
  await capture("discord-links-selected");
  window.setSize(480, 720);
  window.webContents.setZoomFactor(1.5);
  await evaluate(
    'document.getElementById("desktop-discord-links").scrollIntoView({block:"center"})',
  );
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  await capture("discord-links-narrow");
  window.close();
  await until(() => window.isDestroyed());
  setProtocol("other-discord.desktop");
  window = settingsPage.open("settings");
  setWindow(window);
  await until(() => get('$("desktop-discord-links") && !$("desktop-discord-links").disabled'));
  assert.equal(
    protocol().handler,
    "other-discord.desktop",
    "opening Settings never steals another handler",
  );
  window.close();
  await until(() => window.isDestroyed());
  let launches = 0;
  window = await require(path.join(root, "integration/updater/mod-settings.cjs")).onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  setWindow(window);
  await until(() => get('$("setup-next") && !$("setup-next").disabled'));
  await click("setup-next");
  await until(() => get('$("setup-discord-links") && !$("setup-discord-links").disabled'));
  assert.equal(await get('$("setup-discord-links").checked'), false);
  await click("setup-discord-links");
  setProtocol("other-discord.desktop", true);
  await click("setup-done");
  await until(() => get('!$("setup-error").hidden && !$("setup-done").disabled'));
  assert.equal(launches, 0);
  assert.equal(await get('$("setup-discord-links").checked'), true);
  assert.match(await get('$("setup-error").textContent'), /Could not make/);
  await capture("discord-links-welcome-error");
  await click("setup-discord-links");
  await click("setup-done");
  await until(() => launches === 1);
  assert.equal(protocol().handler, "other-discord.desktop");
  assert.equal(
    JSON.parse(fs.readFileSync(path.join(profile, "settings.json"))).openasar.desktop?.discordLinks,
    undefined,
  );
};
