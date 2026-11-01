// Native input regression: no DOM interception is installed by the app.
const { app, BrowserWindow, clipboard } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_INPUT_OUTPUT;
const mode = process.env.DPE_INPUT_MODE || "default";
if (!output || !["default", "blocked", "autoscroll"].includes(mode))
  throw Error("Choose a disposable DPE_INPUT_OUTPUT and a supported DPE_INPUT_MODE");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile-" + mode));
const blocked = mode !== "default";
if (blocked) app.commandLine.appendSwitch("blink-settings", "middleClickPasteAllowed=false");
if (mode === "autoscroll") app.commandLine.appendSwitch("enable-features", "MiddleClickAutoscroll");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const timer = setTimeout(() => app.exit(1), 20000);
app
  .whenReady()
  .then(async () => {
    const window = new BrowserWindow({
      width: 740,
      height: 560,
      webPreferences: { spellcheck: false },
    });
    const events = [];
    window.webContents.setWindowOpenHandler(({ url }) => {
      events.push(url);
      return { action: "deny" };
    });
    const frame =
      '<body style="margin:0"><canvas id="activity" width="150" height="60"></canvas><script>window.events=[];for(const type of ["mousedown","mouseup","auxclick"])activity.addEventListener(type,e=>{events.push([type,e.button]);if(type==="mousedown")e.preventDefault()});</script>';
    const html = `<style>body{margin:0}textarea,#editable,a,canvas,iframe,#scroller{position:absolute}textarea{left:20px;top:20px;width:260px;height:45px}#editable{left:20px;top:90px;width:260px;height:45px}a{left:20px;top:160px}canvas{left:20px;top:200px}iframe{left:20px;top:300px;width:220px;height:90px;border:0}#scroller{left:350px;top:20px;width:260px;height:350px;overflow:auto}#content{height:2500px}</style><textarea id="field"></textarea><div id="editable" contenteditable="true"></div><a href="https://example.test/middle" target="_blank">Middle-click link</a><canvas id="activity" width="200" height="70"></canvas><iframe srcdoc="${frame.replaceAll("&", "&amp;").replaceAll('"', "&quot;")}"></iframe><div id="scroller"><div id="content">Scrollable content</div></div><script>window.events=[];for(const type of ['mousedown','mouseup','auxclick'])document.addEventListener(type,e=>events.push([type,e.button,e.target.id||e.target.tagName]));activity.addEventListener('mousedown',e=>e.preventDefault());</script>`;
    await window.loadURL("data:text/html," + encodeURIComponent(html));
    const evaluate = (code) => window.webContents.executeJavaScript(code);
    await delay(150);
    function click(x, y, button = "middle") {
      for (const type of ["mouseDown", "mouseUp"])
        window.webContents.sendInputEvent({
          type,
          button,
          x,
          y,
          globalX: x,
          globalY: y,
          clickCount: 1,
        });
    }
    async function paste(target, x, y) {
      await evaluate(`${target}.textContent='';${target}.value='';${target}.focus()`);
      clipboard.writeText("PRIMARY", "selection");
      click(x, y);
      await delay(100);
      return evaluate(target === "field" ? "field.value" : "editable.textContent");
    }
    const expected = blocked ? "" : "PRIMARY";
    assert.equal(await paste("field", 60, 40), expected);
    assert.equal(await paste("editable", 60, 110), expected);
    window.webContents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    await delay(100);
    assert.equal(
      await paste("field", 60, 40),
      expected,
      "renderer preference updates must preserve the explicit override",
    );
    clipboard.writeText("CLIPBOARD");
    for (const type of ["keyDown", "keyUp"])
      window.webContents.sendInputEvent({ type, keyCode: "V", modifiers: ["control"] });
    await delay(100);
    assert.equal(await evaluate("field.value"), expected + "CLIPBOARD");
    click(50, 168);
    await delay(100);
    assert.deepEqual(events, ["https://example.test/middle"]);
    click(70, 230);
    click(60, 330);
    await delay(100);
    const mainEvents = await evaluate("events.filter(e=>e[2]==='activity')");
    assert.deepEqual(mainEvents, [
      ["mousedown", 1, "activity"],
      ["mouseup", 1, "activity"],
      ["auxclick", 1, "activity"],
    ]);
    const frameEvents = await evaluate("document.querySelector('iframe').contentWindow.events");
    assert.deepEqual(frameEvents, [
      ["mousedown", 1],
      ["mouseup", 1],
      ["auxclick", 1],
    ]);
    click(410, 70);
    window.webContents.sendInputEvent({
      type: "mouseMove",
      x: 410,
      y: 300,
      globalX: 410,
      globalY: 300,
    });
    await delay(800);
    const scrolled = await evaluate("scroller.scrollTop");
    if (mode === "autoscroll")
      assert.ok(scrolled > 0, "native middle-click autoscroll should move the scroller");
    else assert.equal(scrolled, 0);
    click(410, 300, "left");
    const report = {
      passed: true,
      mode,
      primaryPasteBlocked: blocked,
      rendererPreferenceUpdate: true,
      keyboardPaste: true,
      middleLink: true,
      canvasAuxclick: true,
      frameAuxclick: true,
      autoscroll: scrolled > 0,
      versions: process.versions,
    };
    fs.writeFileSync(path.join(output, mode + ".json"), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report));
    clearTimeout(timer);
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
