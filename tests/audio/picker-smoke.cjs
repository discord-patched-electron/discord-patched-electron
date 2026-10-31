// Run with a retained Electron runtime under xvfb-run. No account or host media.
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const output = process.env.DPE_PICKER_OUTPUT;
if (!output) throw new Error("Set DPE_PICKER_OUTPUT to a disposable output directory");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1269,
    height: 772,
    useContentSize: true,
    webPreferences: {
      preload: path.join(__dirname, "picker-smoke-preload.cjs"),
      sandbox: false,
      contextIsolation: true,
    },
  });
  let openedLink;
  window.webContents.setWindowOpenHandler(({ url }) => {
    openedLink = url;
    return { action: "deny" };
  });
  const run = (code) => window.webContents.executeJavaScriptInIsolatedWorld(999, [{ code }]);
  window.webContents.on("console-message", (event) => console.log(event.message));
  try {
    await window.loadURL(
      "data:text/html," +
        encodeURIComponent(
          '<html><body style="background:#100e15"><main id="app-mount"><button id="previous">Screen share</button></main></body></html>',
        ),
    );
    await run(`
      window.options = [
        { id: 'system', label: 'System audio', detail: 'All applications except Discord' },
        { id: '200', label: 'Firefox', detail: 'Music <b>not markup</b>' },
        { id: '300', label: 'VLC media player', detail: 'A very long media title '.repeat(12) },
      ];
      window.replies = [];
      window.openPicker = () => { document.querySelector('#previous').focus(); window.dismiss = picker.show(options, value => replies.push(value)); };
      openPicker();
    `);
    const result = await run(`(() => {
      const query = s => pickerRoot.querySelector(s);
      const result = {};
      result.modal = query('dialog').matches(':modal');
      result.dialogFocused = pickerRoot.activeElement === query('dialog');
      result.cornerLabel = query('.summary strong').textContent === 'Choose audio source';
      result.appCopy = query('.hint span').textContent === 'Includes windows from the same app';
      result.searchAppearance = query('[type=search]').placeholder === 'Search' && !!query('.search-field svg');
      result.docsLink = query('.hint a').hash === '#automatic-detection';
      const positions = () => JSON.stringify(['h2','.search-field','footer'].map(s=>{const r=query(s).getBoundingClientRect();return [r.x,r.y,r.width,r.height]}));
      const beforeSearch = positions();
      result.disabledInitially = query('.primary').disabled;
      result.literalMetadata = query('small').nextElementSibling === null && pickerRoot.textContent.includes('<b>not markup</b>') && !query('b');
      query('[value="200"]').click();
      result.enabledOnSelection = !query('.primary').disabled;
      query('[type=search]').value = 'vlc';
      query('[type=search]').dispatchEvent(new Event('input'));
      result.hiddenSelectionCleared = query('.primary').disabled && !query('[value="200"]').checked;
      query('.clear-search').click();
      result.searchCleared = query('[type=search]').value === '' && query('.clear-search').hidden && pickerRoot.activeElement === query('[type=search]');
      query('[type=search]').value = '  MUSIC  ';
      query('[type=search]').dispatchEvent(new Event('input'));
      result.mediaSearch = !query('[value="200"]').closest('label').hidden && query('[value="300"]').closest('label').hidden;
      query('[type=search]').value = 'no-results';
      query('[type=search]').dispatchEvent(new Event('input'));
      result.stableEmptySearch = positions() === beforeSearch;
      result.noMatches = !query('.empty').hidden && !query('.clear-search').hidden;
      query('.clear-search').click();
      query('[value="300"]').click();
      query('.primary').click();
      result.confirmed = replies.at(-1) === '300';
      result.focusRestored = document.activeElement.id === 'previous';
      result.removed = !document.querySelector('#app-mount > div');
      openPicker();
      query('dialog').dispatchEvent(new Event('cancel', {cancelable: true}));
      result.cancelled = replies.at(-1) === 'none';
      openPicker();
      query('.refresh').click();
      result.refresh = replies.at(-1) === 'refresh';
      openPicker();
      query('[value="200"]').click();
      query('#audio-tab-1').click();
      result.tabClearsSelection = !query('[value="200"]').checked;
      result.systemCopy = query('.hint span').textContent === 'Includes all applications except Discord';
      result.systemCard = getComputedStyle(query('.system-source')).borderTopWidth === '1px' && getComputedStyle(query('.system-source'),'::after').content.includes('✓') && getComputedStyle(query('.system-source')).backgroundColor !== 'rgba(0, 0, 0, 0)';
      result.systemAutomatic = !query('[value="system"]') && !query('.primary').disabled && query('[type=search]').hidden;
      query('.system-source').click();
      query('.system-source').click();
      result.systemCannotDeselect = !query('.primary').disabled && query('.primary').textContent === 'Share Audio';
      query('#audio-tab-0').click();
      result.appRequiresSelection = query('.primary').disabled;
      query('#audio-tab-1').click();
      query('.primary').click();
      result.systemConfirmed = replies.at(-1) === 'system';
      openPicker();
      query('#audio-tab-0').dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true}));
      result.keyboardTabs = query('#audio-tab-1').getAttribute('aria-selected') === 'true' && pickerRoot.activeElement === query('#audio-tab-1');
      const count = replies.length;
      dismiss(); dismiss();
      result.teardownSilent = replies.length === count;
      openPicker();
      return result;
    })()`);
    for (const [name, value] of Object.entries(result)) assert.equal(value, true, name);
    await run(`pickerRoot.querySelector('.hint a').click()`);
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(
      openedLink,
      "https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/wayland-audio.md#automatic-detection",
    );
    await run(
      `dismiss(); options[1] = {id:'200', label:'Zen Browser', detail:'Music', iconNames:['zen-bin']}; options[2] = {id:'300', label:'mpv', detail:'Video playback', iconNames:['mpv']}; openPicker(); document.fonts.ready`,
    );
    await run(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    await run("new Promise(resolve => setTimeout(resolve, 600))");
    fs.writeFileSync(
      path.join(output, "dark.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    const searchBounds = await run(
      `(() => { const r = pickerRoot.querySelector('.search-field').getBoundingClientRect(); return {x:Math.floor(r.x)-4,y:Math.floor(r.y)-4,width:Math.ceil(r.width)+8,height:Math.ceil(r.height)+8}; })()`,
    );
    fs.writeFileSync(
      path.join(output, "search-idle.png"),
      (await window.webContents.capturePage(searchBounds)).toPNG(),
    );
    await run(
      "pickerRoot.querySelector('[type=search]').focus(); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    fs.writeFileSync(
      path.join(output, "search-focused.png"),
      (await window.webContents.capturePage(searchBounds)).toPNG(),
    );
    await run(
      "pickerRoot.querySelector('#audio-tab-1').click(); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    fs.writeFileSync(
      path.join(output, "system.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    await run(
      "pickerRoot.querySelector('#audio-tab-0').click(); pickerRoot.querySelector('dialog').focus();",
    );
    window.focus();
    window.webContents.focus();
    let keyboardContained = true;
    for (let key = 0; key < 16; key++) {
      window.webContents.sendInputEvent({
        type: "keyDown",
        keyCode: "Tab",
        modifiers: key < 8 ? [] : ["shift"],
      });
      window.webContents.sendInputEvent({
        type: "keyUp",
        keyCode: "Tab",
        modifiers: key < 8 ? [] : ["shift"],
      });
      await run("new Promise(requestAnimationFrame)");
      keyboardContained &&= await run("Boolean(pickerRoot.activeElement?.closest('dialog'))");
    }
    assert.equal(keyboardContained, true, "Tab/Shift+Tab stay in the dialog");
    // Escape must work as a real key event, not only a synthetic cancel event.
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
    await run("new Promise(requestAnimationFrame)");
    const escapeKey = await run(
      "replies.at(-1) === 'none' && !document.querySelector('#app-mount > div')",
    );
    assert.equal(escapeKey, true, "Escape closes without audio");
    await run("openPicker();");
    await run(
      `document.querySelector('#app-mount').style.cssText = '--background-surface-high:#fff;--background-base-low:#f2f3f5;--background-base-lowest:#fafafa;--background-surface-highest:#fff;--background-mod-subtle:#ebedef;--border-subtle:#ddd;--input-background:#fff;--text-default:#313338;--text-muted:#5c5e66;--interactive-normal:#80848e;--background-modifier-hover:#e3e5e8;--background-modifier-selected:#e3e5e8'`,
    );
    await run(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    fs.writeFileSync(
      path.join(output, "light.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    window.setContentSize(360, 640);
    await run(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    const narrow = await run(
      `(() => { const d = pickerRoot.querySelector('dialog'); return d.scrollWidth <= d.clientWidth && d.getBoundingClientRect().width <= innerWidth; })()`,
    );
    assert.equal(narrow, true, "no horizontal overflow at 360px");
    fs.writeFileSync(
      path.join(output, "narrow.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    const overflow = [];
    const baseline = await run(`options`);
    await run(
      `dismiss(); options = [options[0], ...Array.from({length:80}, (_,i) => ({id:String(1000+i), label:'LongApplicationName'.repeat(15), detail:'Long media title '.repeat(30)}))]; openPicker();`,
    );
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
      await run(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
      );
      const check = await run(`(() => {
        const d = pickerRoot.querySelector('dialog'), c = pickerRoot.querySelector('.content');
        const footer = pickerRoot.querySelector('footer');
        const footerBefore = footer.getBoundingClientRect().top;
        c.scrollTop = c.scrollHeight;
        d.scrollTop = d.scrollHeight;
        const last = pickerRoot.querySelector('fieldset').lastElementChild.getBoundingClientRect();
        const box = c.getBoundingClientRect();
        const button = pickerRoot.querySelector('.primary').getBoundingClientRect();
        return { noHorizontalOverflow: d.scrollWidth <= d.clientWidth && c.scrollWidth <= c.clientWidth,
          lastCardReachable: getComputedStyle(c).overflowY === 'visible' || last.bottom <= box.bottom + 1,
          actionsReachable: button.bottom <= innerHeight && button.top >= 0,
          footerStable: getComputedStyle(c).overflowY === 'visible' || Math.abs(footerBefore - footer.getBoundingClientRect().top) < 1,
          contained: getComputedStyle(d).overscrollBehaviorY === 'contain' && getComputedStyle(c).overscrollBehaviorY === 'contain',
          backgroundUnscrolled: document.scrollingElement.scrollTop === 0 };
      })()`);
      for (const [name, passed] of Object.entries(check))
        assert.equal(passed, true, `${width}x${height} @${zoom}: ${name}`);
      overflow.push({ width, height, zoom, ...check });
    }
    window.setContentSize(1269, 772);
    const scaling = [];
    for (const [zoom, fontScale] of [
      [0.8, 100],
      [1.25, 100],
      [1.5, 100],
      [2, 100],
      [1, 75],
      [1, 125],
      [1, 150],
      [1, 200],
      [1.25, 150],
    ]) {
      window.webContents.setZoomFactor(zoom);
      await run(
        `document.documentElement.style.fontSize = "${fontScale}%"; new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`,
      );
      const result = await run(`(() => {
        const [modal, content, card, label, footer, visual] = ["dialog", ".content", ".source:not([hidden])", ".source:not([hidden]) strong", "footer", ".source:not([hidden]) .source-visual"].map(s => pickerRoot.querySelector(s));
        content.scrollTop = 0; modal.scrollTop = 0;
        const box = card.getBoundingClientRect();
        footer.scrollIntoView({block:'end'});
        const f = footer.getBoundingClientRect(), m = modal.getBoundingClientRect();
        return {root:parseFloat(getComputedStyle(document.documentElement).fontSize), cardWidth:box.width, cardHeight:box.height, text:parseFloat(getComputedStyle(label).fontSize), icon:visual.getBoundingClientRect().width, searchHeight:pickerRoot.querySelector('.search-field').getBoundingClientRect().height, buttonHeight:pickerRoot.querySelector('.primary').getBoundingClientRect().height, noOverflow:modal.scrollWidth<=modal.clientWidth+1&&content.scrollWidth<=content.clientWidth+1, footerReachable:f.top>=m.top-1&&f.bottom<=m.bottom+1};
      })()`);
      const ratio = fontScale / 100;
      for (const [key, base] of Object.entries({
        root: 16,
        cardWidth: 200,
        cardHeight: 160,
        text: 16,
        icon: 48,
        searchHeight: 40,
        buttonHeight: 40,
      }))
        assert.ok(
          Math.abs(result[key] - base * ratio) < 0.1,
          `${zoom} zoom, ${fontScale}% font: ${key}=${result[key]}`,
        );
      assert.equal(result.noOverflow, true, `${zoom} zoom, ${fontScale}% font: overflow`);
      assert.equal(result.footerReachable, true, `${zoom} zoom, ${fontScale}% font: footer`);
      if (zoom === 1 && fontScale === 150) {
        await run(
          `pickerRoot.querySelector('dialog').scrollTop=0; pickerRoot.querySelector('.content').scrollTop=0; new Promise(requestAnimationFrame)`,
        );
        fs.writeFileSync(
          path.join(output, "scaled.png"),
          (await window.webContents.capturePage()).toPNG(),
        );
      }
      scaling.push({ zoom, fontScale, ...result });
    }
    fs.writeFileSync(path.join(output, "scaling.json"), JSON.stringify(scaling, null, 2));
    await run(`document.documentElement.style.removeProperty('font-size')`);
    window.webContents.setZoomFactor(1);
    await run(`dismiss(); options = ${JSON.stringify(baseline)}; openPicker();`);
    window.webContents.setZoomFactor(1);
    window.setContentSize(1269, 772);
    // Image failure and a late image reply must not break selection or revive a closed modal.
    const artwork = await run(`(async () => {
      dismiss();
      const sample = 'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><rect width="48" height="48" fill="blue"/></svg>');
      dismiss = picker.show(options, value => replies.push(value), document, async () => sample);
      await new Promise(resolve => setTimeout(resolve, 100));
      const loaded = pickerRoot.querySelectorAll('.source-visual img').length === 2;
      dismiss();
      dismiss = picker.show(options, value => replies.push(value), document, async () => 'data:image/png;base64,YmFk');
      await new Promise(resolve => setTimeout(resolve, 100));
      const fallback = pickerRoot.querySelectorAll('.source-visual svg').length === 3;
      dismiss();
      let complete;
      dismiss = picker.show(options, value => replies.push(value), document, () => new Promise(resolve => { complete = resolve; }));
      dismiss(); complete(sample);
      await new Promise(resolve => setTimeout(resolve, 20));
      return {loaded, fallback, lateReplyIgnored: !document.querySelector('#app-mount > div')};
    })()`);
    for (const [name, value] of Object.entries(artwork)) assert.equal(value, true, name);
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify(
        { ...result, narrow, overflow, artwork, keyboardContained, escapeKey },
        null,
        2,
      ),
    );
    console.log(
      JSON.stringify({ ...result, narrow, overflow, artwork, keyboardContained, escapeKey }),
    );
  } catch (error) {
    console.error(error);
    app.exit(1);
  } finally {
    window.destroy();
    app.quit();
  }
});
