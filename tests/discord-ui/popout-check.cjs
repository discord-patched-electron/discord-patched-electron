const assert = require("node:assert/strict");
const { titlebarCSS } = require("../../integration/desktop/preferences.cjs");
const media = require("../../integration/desktop/popout-media.cjs");
module.exports = async function check(window) {
  const run = (script) => window.webContents.executeJavaScript(script);
  await window.loadURL(
    "data:text/html," +
      encodeURIComponent(`<!doctype html><style>
    body{margin:0}.titleBar_test{height:28px}.wordmark_test{height:12px}video{width:600px;height:400px}
    </style><div id="plain" class="titleBar_test"><div class="wordmark_test">Discord</div><button class="winButton_test">Close</button></div>
    <div id="extra" class="titleBar_test"><button class="winButton_test">Close</button><button>Pin</button></div>
    <div id="modern" class="bar_test"><div class="trailing_test"><button>Help</button><div class="winButtons_test"><button class="winButton_test">Close</button></div></div></div>`),
  );
  await window.webContents.insertCSS(titlebarCSS({ windowControls: false }, true));
  assert.equal(await run("document.getElementById('plain').getBoundingClientRect().height"), 0);
  assert.equal(await run("document.getElementById('extra').getBoundingClientRect().height"), 28);
  assert.equal(
    await run("document.querySelector('#modern button').getBoundingClientRect().height > 0"),
    true,
  );
  await run(`globalThis.result = (${media.toString()})();
    const small=document.createElement('video');small.style.cssText='width:100px;height:70px';
    Object.defineProperties(small,{videoWidth:{value:640},videoHeight:{value:480}});document.body.append(small);
    const large=document.createElement('video');Object.defineProperties(large,{videoWidth:{value:1080},videoHeight:{value:1920}});
    document.body.prepend(large);void 0`);
  const dimensions = await run("result");
  assert.ok(
    dimensions,
    await run(
      `JSON.stringify({innerWidth,innerHeight,videos:[...document.querySelectorAll('video')].map(v=>({width:v.videoWidth,height:v.videoHeight,rect:v.getBoundingClientRect().toJSON()}))})`,
    ),
  );
  assert.equal(dimensions.width, 1080);
  assert.equal(dimensions.height, 1920);
  assert.equal(dimensions.chromeWidth, await run("Math.max(0, innerWidth - 600)"));
  assert.equal(dimensions.chromeHeight, await run("Math.max(0, innerHeight - 400)"));
};
