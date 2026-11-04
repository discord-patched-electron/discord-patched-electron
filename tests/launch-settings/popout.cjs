const assert = require("node:assert/strict");
module.exports = async function check({ window, values, click, get, until, evaluate }) {
  await click("t_theming");
  const ready = () =>
    until(() => get('$("desktop-popoutFloating") && !$("desktop-popoutFloating").disabled'));
  await ready();
  for (const name of ["popoutFloating", "popoutHideTitlebar", "popoutHideMembers"])
    assert.equal(await get(`$("desktop-${name}").checked`), true);
  const toggle = async (name) => {
    await click(`desktop-${name}`);
    await ready();
  };
  await toggle("popoutFloating");
  assert.equal(values.openasar.desktop.popoutFloating, false);
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), true);
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), false);
  await toggle("popoutFloating");
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), true);
  await toggle("popoutHideTitlebar");
  await toggle("windowControls");
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), true);
  await toggle("windowControls");
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), false);
  await toggle("nativeTitlebar");
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), true);
  await toggle("nativeTitlebar");
  window.focus();
  window.webContents.focus();
  await evaluate('document.getElementById("desktop-popoutHideMembers").focus()');
  for (const type of ["keyDown", "char", "keyUp"])
    window.webContents.sendInputEvent({ type, keyCode: " " });
  await until(() => values.openasar.desktop?.popoutHideMembers === false);
  await ready();
  await click("t_settings");
  await click("t_theming");
  await ready();
  assert.equal(await get('$("desktop-popoutHideMembers").checked'), false);
  window.setMinimumSize(1, 1);
  window.setSize(720, 620);
  window.webContents.setZoomFactor(1.5);
  await evaluate(`document.getElementById('desktop-popoutHideMembers-label').textContent =
    'Hide the members bar when expanding the currently selected screenshare with a longer translated label';
    document.getElementById('desktop-popoutHideMembers').scrollIntoView({block:'center'});
    new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), true);
  assert.equal(
    await evaluate(`(()=>{const label=document.getElementById('desktop-popoutHideMembers-label').getBoundingClientRect();
    const input=document.getElementById('desktop-popoutHideMembers').getBoundingClientRect();
    return label.right <= input.left && label.height > 0;})()`),
    true,
  );
};
