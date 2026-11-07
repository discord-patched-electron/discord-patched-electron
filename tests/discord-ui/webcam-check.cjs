const assert = require("node:assert/strict");
const { webcamCSS } = require("../../integration/desktop/preferences.cjs");

module.exports = async function checkWebcam(window) {
  await window.loadURL(
    "data:text/html," +
      encodeURIComponent(`<!doctype html>
    <style>
      .mirror__fixture,.camera__fixture {transform:scaleX(-1)}
      .video__fixture {width:160px;height:90px}
    </style>
    <div id="self" class="video__fixture mirror__fixture"><canvas></canvas></div>
    <div id="tile" class="media-engine-video video__fixture mirror__fixture"><video></video></div>
    <div id="camera" class="camera__fixture"><div class="media-engine-video"><video></video></div></div>
    <div id="minimap" class="media-engine-video minimapVideo__fixture mirror__fixture"><video></video></div>
    <div id="remote" class="media-engine-video video__fixture"><video></video></div>
    <div id="screen" class="media-engine-video video__fixture"><video></video></div>
    <div id="unrelated" class="mirror__fixture"></div>
    <div id="camera-icon" class="camera__fixture"></div>`),
  );
  const run = (source) => window.webContents.executeJavaScript(source);
  const transform = (id) => run(`getComputedStyle(document.getElementById('${id}')).transform`);
  const previews = ["self", "tile", "camera", "minimap"];
  for (const id of previews)
    assert.equal(await transform(id), "matrix(-1, 0, 0, 1, 0, 0)", `${id} starts mirrored`);
  for (const outgoing of [false, true]) {
    const css = webcamCSS();
    const key = css ? await window.webContents.insertCSS(css) : undefined;
    const matrix = await transform("self");
    for (const id of previews) {
      assert.equal(await transform(id), "none", `${id} has no additional CSS flip`);
      const displayedSign = (outgoing ? -1 : 1) * ((await transform(id)) === "none" ? 1 : -1);
      assert.equal(displayedSign, outgoing ? -1 : 1, `${id} matches the sent frame`);
    }
    assert.equal(await transform("remote"), "none");
    assert.equal(await transform("screen"), "none");
    assert.equal(await transform("unrelated"), "matrix(-1, 0, 0, 1, 0, 0)");
    assert.equal(await transform("camera-icon"), "matrix(-1, 0, 0, 1, 0, 0)");
    await run("document.getElementById('self').className = 'video__new mirror__fixture'");
    assert.equal(await transform("self"), matrix);
    await run("document.getElementById('self').className = 'video__fixture mirror__fixture'");
    if (key) await window.webContents.removeInsertedCSS(key);
    for (const id of previews) assert.equal(await transform(id), "matrix(-1, 0, 0, 1, 0, 0)");
  }
};
