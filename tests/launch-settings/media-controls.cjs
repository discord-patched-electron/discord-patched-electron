const assert = require("node:assert/strict");
module.exports = async function checkMediaControls({
  window,
  values,
  click,
  get,
  until,
  evaluate,
}) {
  assert.equal(await get('$("desktop-mediaControls").checked'), false);
  await evaluate("document.getElementById('desktop-mediaControls').focus()");
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Space" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Space" });
  await until(() => values.openasar.desktop?.mediaControls === true);
  await until(() => get('!$("desktop-mediaControls").disabled'));
  assert.equal(await get("document.activeElement.id"), "desktop-mediaControls");
  assert.equal(await get('$("desktop-restart-note").hidden'), false);
  await click("t_theming");
  await click("t_settings");
  await until(() => get('$("desktop-mediaControls") && !$("desktop-mediaControls").disabled'));
  assert.equal(await get('$("desktop-mediaControls").checked'), true);
  await click("desktop-mediaControls");
  await until(() => values.openasar.desktop.mediaControls === false);
  await until(() => get('!$("desktop-mediaControls").disabled'));
  assert.equal(await get('$("desktop-restart-note").hidden'), true);
  const originalSize = window.getSize();
  window.setSize(420, 560);
  window.webContents.setZoomFactor(1.25);
  await evaluate(
    `document.getElementById('desktop-mediaControls-label').textContent = 'Desktop media controls with a longer translated setting label'; document.getElementById('desktop-mediaControls').scrollIntoView({block:'center'})`,
  );
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  window.webContents.setZoomFactor(1);
  window.setSize(...originalSize);
};
