// Actual Discord components, fresh signed-out profile; no capture or account use.
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const output = process.env.DPE_PICKER_OUTPUT;
if (!output) throw Error("Set DPE_PICKER_OUTPUT to a fresh output directory");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.disableHardwareAcceleration();
const timeout = setTimeout(() => app.exit(2), 60000);
app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1269,
    height: 772,
    useContentSize: true,
    webPreferences: {
      preload: path.join(__dirname, "native-picker-preload.cjs"),
      contextIsolation: true,
      sandbox: false,
    },
  });
  const run = (code) => window.webContents.executeJavaScript(code),
    isolated = (code) => window.webContents.executeJavaScriptInIsolatedWorld(999, [{ code }]);
  const pause = () => new Promise((resolve) => setTimeout(resolve, 400));
  async function until(code) {
    for (let n = 0; n < 80; n++) {
      if (await run(code)) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw Error("Timed out: " + code);
  }
  window.webContents.on("console-message", (event) => {
    if (event.level === "error") console.error(event.message);
  });
  try {
    await window.loadURL("https://canary.discord.com/login");
    await until('!!document.querySelector("input[name=email]")');
    // Register the same lazy UI chunks as the screenshare entry point, without
    // executing its controller, opening the video picker or starting capture.
    await run(
      `(async()=>{const rs=[],c=[['dpe-test-chunks'],{},r=>rs.push(r)];webpackChunkdiscord_app.push(c);webpackChunkdiscord_app.splice(webpackChunkdiscord_app.indexOf(c),1);const req=rs.sort((a,b)=>Object.keys(b.c||{}).length-Object.keys(a.c||{}).length)[0];const source=Object.values(req.m).map(String).find(s=>s.includes('openModalLazy')&&s.includes('getDesktopSource')&&s.includes('sourceName:null')&&s.length<10000);if(!source)throw Error('Screenshare entry unavailable');const ids=[...source.matchAll(/\\.e\\("(\\d+)"\\)/g)].map(m=>m[1]);await Promise.all(ids.map(id=>req.e(id)));})()`,
    );
    console.log(
      await run(
        `(${require("../../integration/audio/native-picker-renderer.cjs").toString()})({operation:'probe',key:'dpe-test-probe'},${require("../../integration/audio/native-picker-discovery.cjs").toString()})`,
      ),
    );
    await isolated("audioTest.open()");
    await until('!!document.querySelector(".dpe-native-audio-layout")');
    await pause();
    fs.writeFileSync(
      path.join(output, "modal.html"),
      await run(`document.querySelector(".dpe-native-audio-layout").outerHTML`),
    );
    const initial = await run(
      `(()=>{const modal=document.querySelector('[data-mana-component=modal]'),r=modal.getBoundingClientRect(),card=document.querySelector('.dpe-native-audio-source').getBoundingClientRect(),search=document.querySelector('.dpe-native-audio-search').getBoundingClientRect();return {width:r.width,height:r.height,cardWidth:card.width,cardHeight:card.height,searchWidth:search.width,tabs:modal.querySelectorAll('[role=tab]').length,buttons:modal.querySelectorAll('[data-mana-component=button]').length,disabled:document.querySelector('.dpe-native-primary button').disabled};})()`,
    );
    console.log(initial);
    assert.equal(initial.width, 1200);
    assert.equal(initial.height, 720);
    assert.equal(initial.cardWidth, 254);
    assert.equal(initial.cardHeight, 206);
    assert.equal(initial.searchWidth, 274);
    assert.equal(initial.tabs, 2);
    assert.equal(initial.buttons, 3);
    assert.equal(initial.disabled, true);
    const rect = await run(
      `(()=>{const r=document.querySelector('[data-mana-component=modal]').getBoundingClientRect();return {x:Math.floor(r.x),y:Math.floor(r.y),width:Math.ceil(r.width),height:Math.ceil(r.height)};})()`,
    );
    fs.writeFileSync(
      path.join(output, "native.png"),
      (await window.webContents.capturePage(rect)).toPNG(),
    );
    await run(`document.documentElement.classList.replace('theme-darker','theme-midnight')`);
    await pause();
    fs.writeFileSync(
      path.join(output, "midnight.png"),
      (await window.webContents.capturePage(rect)).toPNG(),
    );
    async function search(value) {
      await run(
        `(()=>{const input=document.querySelector('[role=searchbox]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));})()`,
      );
      await run("new Promise(requestAnimationFrame)");
    }
    window.focus();
    window.webContents.focus();
    await pause();
    await run(`document.querySelector('[data-source-id="200"]').focus()`);
    assert.equal(await run(`document.activeElement.dataset.sourceId`), "200");
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Right" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Right" });
    await until(
      `document.querySelector('[data-source-id="300"]').getAttribute('aria-checked')==='true'`,
    );
    await run(`document.querySelector('[data-source-id="200"]').click()`);
    await search("  VIDEO  ");
    assert.equal(await run(`document.querySelectorAll('[role=radio]').length`), 1);
    assert.equal(await run(`document.querySelector('.dpe-native-primary button').disabled`), true);
    await search("no match");
    assert.equal(await run(`!!document.querySelector('.dpe-native-empty')`), true);
    await run(
      `document.querySelector('.dpe-native-audio-search [role=button][aria-label]').click()`,
    );
    await until(`document.querySelectorAll('[role=radio]').length===2`);
    await run(`document.querySelector('[data-source-id="200"]').click()`);
    await run(`document.querySelector('.dpe-native-primary button').click()`);
    await until(`!document.querySelector('.dpe-native-audio-layout')`);
    assert.equal(await isolated("audioTest.replies.at(-1)"), "200");
    await isolated("audioTest.open()");
    await until(`!!document.querySelector('[role=tab]')`);
    await run(`document.querySelectorAll('.dpe-native-audio-tabs [role=tab]')[1].click()`);
    await until(`!!document.querySelector('.dpe-native-system')`);
    await run(
      `document.querySelector('.dpe-native-system').click();document.querySelector('.dpe-native-system').click()`,
    );
    assert.equal(
      await run(
        `document.querySelector('.dpe-native-primary button').disabled||!!document.querySelector('[role=radio]')`,
      ),
      false,
    );
    await run(`document.querySelector('.dpe-native-primary button').click()`);
    await until(`!document.querySelector('.dpe-native-audio-layout')`);
    assert.equal(await isolated("audioTest.replies.at(-1)"), "system");
    await isolated("audioTest.open()");
    await until(`!!document.querySelector('.dpe-native-audio-layout')`);
    await pause();
    window.focus();
    window.webContents.focus();
    const tabFocus = [];
    for (let n = 0; n < 16; n++) {
      const modifiers = n < 8 ? [] : ["shift"];
      window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab", modifiers });
      window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab", modifiers });
      await run("new Promise(requestAnimationFrame)");
      tabFocus.push(await run(`document.activeElement.getAttribute('role')`));
      assert.equal(
        await run(`!!document.activeElement.closest('[data-mana-component=modal]')`),
        true,
      );
    }
    assert.ok(tabFocus.includes("tab"), "native pill tabs participate in keyboard navigation");
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
    await until(`!document.querySelector('.dpe-native-audio-layout')`);
    assert.equal(await isolated("audioTest.replies.at(-1)"), "none");
    await isolated(
      `audioTest.open([{id:'system',label:'System audio',detail:'All applications except Discord'},...Array.from({length:80},(_,i)=>({id:String(i+1000),label:'Long application name '.repeat(8)+i,detail:'Media title '.repeat(30)}))])`,
    );
    await until(`document.querySelectorAll('[role=radio]').length===80`);
    const layouts = [];
    for (const [width, height, zoom] of [
      [1269, 772, 1],
      [720, 500, 1],
      [360, 640, 1],
      [320, 480, 1],
      [640, 360, 1],
      [1269, 772, 2],
    ]) {
      window.setContentSize(width, height);
      window.webContents.setZoomFactor(zoom);
      await pause();
      const result = await run(
        `(()=>{const modal=document.querySelector('[data-mana-component=modal]'),last=document.querySelector('[role=radio]:last-child'),footer=document.querySelector('.dpe-native-audio-footer');last.scrollIntoView({block:'end'});const r=modal.getBoundingClientRect(),l=last.getBoundingClientRect();const lastReachable=l.top>=r.top-1&&l.bottom<=r.bottom+1;footer.scrollIntoView({block:'end'});const f=footer.getBoundingClientRect();return {width:innerWidth,height:innerHeight,visible:r.left>=-1&&r.right<=innerWidth+1&&r.top>=-1&&r.bottom<=innerHeight+1,noOverflow:modal.scrollWidth<=modal.clientWidth+1,lastReachable,footerReachable:f.top>=r.top-1&&f.bottom<=r.bottom+1};})()`,
      );
      layouts.push(result);
    }
    console.log(layouts);
    fs.writeFileSync(path.join(output, "layouts.json"), JSON.stringify(layouts, null, 2));
    for (const result of layouts)
      for (const key of ["visible", "noOverflow", "lastReachable", "footerReachable"])
        assert.equal(result[key], true, `${result.width}x${result.height} ${key}`);
    await isolated("audioTest.close()");
    await until(`!document.querySelector('.dpe-native-audio-layout')`);
    assert.equal(await isolated("audioTest.replies.length"), 3);
    window.setContentSize(1269, 772);
    window.webContents.setZoomFactor(1);
    const rendererSource = require("../../integration/audio/native-picker-renderer.cjs").toString();
    const discoverySource =
      require("../../integration/audio/native-picker-discovery.cjs").toString();
    for (const broken of ["Button", "Scroller"]) {
      const failure = await run(
        `(${rendererSource})({operation:'open',key:'dpe-broken-${broken}',options:[{id:'42',label:'Player',detail:'Music'}],css:${JSON.stringify(fs.readFileSync(path.join(__dirname, "../../integration/audio/native-picker.css"), "utf8"))}}, key=>{const d=(${discoverySource})(key);d.components.${broken}=()=>null;return d;})`,
      );
      assert.equal(failure.reason, "render-contract");
      await until(`!document.querySelector('.dpe-native-audio-layout')`);
    }
    // A host update can unmount its contents without an explicit close callback.
    await run(
      `globalThis.nativeHostResult=null;void (${rendererSource})({operation:'open',key:'dpe-unmount-test',options:[{id:'42',label:'Player',detail:'Music'}],css:${JSON.stringify(fs.readFileSync(path.join(__dirname, "../../integration/audio/native-picker.css"), "utf8"))}}, key=>{const d=(${discoverySource})(key), Root=d.components.ModalRoot,React=d.components.React;d.components.ModalRoot=props=>{const [visible,setVisible]=React.useState(true);globalThis.unmountPicker=()=>setVisible(false);return visible?React.createElement(Root,props):null;};return d;}).then(result=>{globalThis.nativeHostResult=result;})`,
    );
    await until(
      `globalThis[Symbol.for('dpe.audioPicker.sessions')].get('dpe-unmount-test')?.mounted`,
    );
    await run(`globalThis.unmountPicker()`);
    await until(`!!globalThis.nativeHostResult`);
    assert.ok(
      ["host-unmounted", "host-removed"].includes(await run("globalThis.nativeHostResult.reason")),
    );
    await until(`!document.querySelector('.dpe-native-audio-layout')`);
    await run(`delete globalThis.unmountPicker;delete globalThis.nativeHostResult`);
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify(
        {
          initial,
          layouts,
          search: true,
          keyboard: true,
          system: true,
          teardown: true,
          rejectedBrokenButton: true,
          rejectedBrokenScroller: true,
          hostUnmount: true,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    console.error(error);
    app.exit(1);
  } finally {
    clearTimeout(timeout);
    window.destroy();
    app.quit();
  }
});
