const assert = require("node:assert/strict");
module.exports = async function check({ window, values, click, get, until, evaluate, capture }) {
  await click("t_theming");
  const ready = () =>
    until(() => get('$("desktop-popoutFloating") && !$("desktop-popoutFloating").disabled'));
  await ready();
  for (const name of [
    "popoutFloating",
    "popoutAutoPosition",
    "popoutAlwaysOnTop",
    "popoutHideTitlebar",
    "popoutHideMembers",
  ])
    assert.equal(await get(`$("desktop-${name}").checked`), true);
  const toggle = async (name) => {
    await click(`desktop-${name}`);
    await ready();
  };
  await toggle("popoutAlwaysOnTop");
  assert.equal(values.openasar.desktop.popoutAlwaysOnTop, false);
  await toggle("popoutAlwaysOnTop");
  assert.equal(values.openasar.desktop.popoutAlwaysOnTop, true);
  assert.equal(await get('$("desktop-popoutCorner").value'), "bottom-right");
  await evaluate(
    'document.getElementById("desktop-popoutCorner-trigger").scrollIntoView({block:"center"})',
  );
  await click("desktop-popoutCorner-trigger");
  await evaluate(
    'Promise.all(document.getElementById("desktop-popoutCorner-listbox").getAnimations().map(animation=>animation.finished.catch(()=>{})))',
  );
  await capture("pip-corner-menu");
  await click("desktop-popoutCorner-trigger");
  for (const [index, corner] of [
    [0, "top-left"],
    [1, "top-right"],
    [2, "bottom-left"],
  ]) {
    await click("desktop-popoutCorner-trigger");
    await until(() => get('$("desktop-popoutCorner-listbox").matches(":popover-open")'));
    await click(`desktop-popoutCorner-listbox-${index}`);
    await until(() => values.openasar.desktop?.popoutCorner === corner);
    await ready();
    assert.equal(await get('$("desktop-popoutCorner").value'), corner);
  }
  assert.match(
    await evaluate(
      'window.Native.desktopSave({popoutCorner:"center"}).then(()=>"accepted",error=>error.message)',
    ),
    /Choose supported desktop settings/,
  );
  assert.equal(values.openasar.desktop.popoutCorner, "bottom-left");
  await toggle("popoutAutoPosition");
  assert.equal(await get('$("desktop-popoutCorner-trigger").disabled'), true);
  assert.equal(values.openasar.desktop.popoutAutoPosition, false);
  await toggle("popoutFloating");
  assert.equal(values.openasar.desktop.popoutFloating, false);
  assert.equal(await get('$("desktop-popoutAlwaysOnTop").disabled'), true);
  assert.equal(await get('$("desktop-popoutAlwaysOnTop").checked'), false);
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), true);
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), false);
  await toggle("popoutFloating");
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), true);
  await toggle("popoutHideTitlebar");
  await toggle("windowControls");
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), false);
  await toggle("windowControls");
  assert.equal(await get('$("desktop-popoutHideTitlebar").checked'), false);
  await toggle("nativeTitlebar");
  assert.equal(await get('$("desktop-popoutHideTitlebar").disabled'), false);
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
  assert.equal(await get('$("desktop-popoutAutoPosition").checked'), false);
  assert.equal(await get('$("desktop-popoutCorner").value'), "bottom-left");
  await toggle("popoutAutoPosition");
  assert.equal(await get('$("desktop-popoutCorner-trigger").disabled'), false);
  await evaluate(
    'document.getElementById("desktop-popout-heading").scrollIntoView({block:"start"})',
  );
  await capture("pip-preferences");
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
  const picker = require(
    require("node:path").join(process.env.DPE_APP_ROOT, "integration/discord-ui/pip-corners.cjs"),
  );
  await evaluate(`window.DPEPiP={state:async()=>({available:true,corner:"bottom-right",large:false}),choose:async(corner)=>({available:true,corner,large:false})};
    const video=document.createElement('video');video.id='pip-test-video';video.style.cssText='position:fixed;width:200px;height:120px';document.body.append(video);
    (${picker.toString()})();`);
  await until(() =>
    get('document.querySelectorAll("#dpe-pip-corners button[aria-pressed=true]").length===1'),
  );
  await evaluate('document.querySelector("#dpe-pip-corners button").click()');
  await until(() =>
    get('document.querySelector("#dpe-pip-corners button").getAttribute("aria-pressed")==="true"'),
  );
  await evaluate(
    'window.DPEPiP.state=async()=>({available:true,corner:null,large:false});window.dispatchEvent(new Event("resize"))',
  );
  await until(() =>
    get('document.querySelectorAll("#dpe-pip-corners button[aria-pressed=true]").length===0'),
  );
  await evaluate(
    'window.DPEPiP.state=async()=>({available:true,corner:null,large:true});window.dispatchEvent(new Event("resize"))',
  );
  await until(() => get('document.getElementById("dpe-pip-corners").hidden'));
  await evaluate(
    'document.getElementById("pip-test-video").remove();document.getElementById("dpe-pip-corners").remove()',
  );
};
