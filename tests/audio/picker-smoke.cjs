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
      window.openPicker = () => { window.dismiss?.(); document.querySelector('#previous').focus(); window.dismiss = picker.show(options, value => replies.push(value), document, undefined, async()=>({reducedMotion:false})); };
      openPicker();
    `);
    await run(`(async () => {
      const dialog = pickerRoot.querySelector('dialog'), deadline = performance.now() + 2000;
      while (dialog.dataset.state !== 'open') {
        if (performance.now() > deadline) throw Error('Audio dialog did not open');
        await new Promise(requestAnimationFrame);
      }
      await new Promise(requestAnimationFrame);
      await Promise.all(dialog.getAnimations({subtree:true}).map(animation => animation.finished));
    })()`);
    const result = await run(`(async () => {
      const query = s => pickerRoot.querySelector(s);
      const result = {};
      const blank = picker.createControls(document).sourceCard({id:'400',label:'  ',detail:''}).row;
      result.missingMetadata = blank.querySelector('strong').textContent === 'Application' && blank.querySelector('small').textContent === 'Application audio' && blank.title === 'Application — Application audio';
      result.modal = query('dialog').matches(':modal');
      result.dialogFocused = pickerRoot.activeElement === query('dialog');
      result.nativeDimensions = query('dialog').getBoundingClientRect().width === 960 && query('dialog').getBoundingClientRect().height === 596 && query('.tabs').getBoundingClientRect().height === 40 && query('.tab').getBoundingClientRect().height === 32;
      result.nativeActions = query('.primary').textContent === 'Make Selection' && query('.primary').getBoundingClientRect().width >= 100 && query('.primary').scrollWidth <= query('.primary').clientWidth && getComputedStyle(query('.secondary')).fontSize === '16px' && getComputedStyle(query('.secondary')).fontWeight === '400' && getComputedStyle(query('.secondary')).borderColor === 'rgba(0, 0, 0, 0)' && getComputedStyle(query('.secondary')).backgroundColor === 'rgba(0, 0, 0, 0)';
      result.bulletSeparator = query('.detection-link [aria-hidden=true]').textContent === '•';
      result.cornerLabel = query('.summary strong').textContent === 'Choose audio source';
      result.appCopy = query('.scope [aria-hidden=false] .scope-copy').textContent === 'Includes windows from the same app';
      result.searchAppearance = query('[type=search]').placeholder === 'Search' && !!query('.search-field svg');
      result.docsLink = query('.hint a').hash === '#automatic-detection';
      const positions = () => JSON.stringify(['h2','.search-field','footer'].map(s=>{const r=query(s).getBoundingClientRect();return [r.x,r.y,r.width,r.height]}));
      const beforeSearch = positions();
      const dialogRect = query('dialog').getBoundingClientRect();
      const centerX = dialogRect.x + dialogRect.width/2;
      const contentCenterX = centerX - 8;
      const cards = [...pickerRoot.querySelectorAll('.source:not([hidden])')].map(n=>n.getBoundingClientRect());
      // Scrollbars consume content width on short displays. Children center in
      // that available area, while the dialog itself centers in the viewport.
      const contentRect = query('.content').getBoundingClientRect();
      const contentCenter = contentRect.left + query('.content').clientLeft + query('.content').clientWidth / 2;
      result.centered = Math.abs(centerX-innerWidth/2)<1 && Math.abs(dialogRect.y+dialogRect.height/2-innerHeight/2)<1 && Math.abs((cards[0].left+cards.at(-1).right)/2-contentCenter)<1 && ['h2','.search-field'].every(s=>{const r=query(s).getBoundingClientRect();return Math.abs(r.x+r.width/2-contentCenter)<1});
      result.defaultSelected = !query('.primary').disabled && query('[value="200"]').checked && replies.length===0;
      result.literalMetadata = query('small').nextElementSibling === null && pickerRoot.textContent.includes('<b>not markup</b>') && !query('b');
      query('[value="200"]').click();
      result.enabledOnSelection = !query('.primary').disabled;
      query('[type=search]').value = 'vlc';
      query('[type=search]').dispatchEvent(new Event('input'));
      result.filteredSelection = !query('.primary').disabled && query('[value="300"]').checked;
      result.searchInputSpace = query('.clear-search').getBoundingClientRect().width < 40 && query('[type=search]').getBoundingClientRect().width > 100;
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
      await new Promise(resolve => setTimeout(resolve, 240));
      result.confirmed = replies.at(-1) === '300';
      result.focusRestored = document.activeElement.id === 'previous';
      result.removed = !document.querySelector('#app-mount > div');
      openPicker();
      query('dialog').dispatchEvent(new Event('cancel', {cancelable: true}));
      await new Promise(resolve => setTimeout(resolve, 240));
      result.cancelled = replies.at(-1) === 'none';
      openPicker();
      query('.refresh').click();
      result.refresh = replies.at(-1) === 'refresh' && query('dialog').matches(':modal') && query('.refresh').disabled;
      openPicker();
      query('[value="200"]').click();
      const actionPositions = () => JSON.stringify(['.primary','.refresh','.secondary'].map(s=>{const r=query(s).getBoundingClientRect();return [r.x,r.y,r.width,r.height]}));
      const appActions = actionPositions();
      query('#audio-tab-1').click();
      result.tabPreservesApp = query('[value="200"]').checked;
      result.stableActions = actionPositions() === appActions;
      result.refreshDisabled = query('.refresh').disabled && !query('.refresh').hidden;
      result.noSystemDetectionLink = !query('.scope [aria-hidden=false]').querySelector('a');
      result.systemCopy = query('.scope [aria-hidden=false] .scope-copy').textContent === 'Includes all applications except Discord';
      result.systemCard = getComputedStyle(query('.system-source')).borderTopWidth === '1px' && !getComputedStyle(query('.system-source'),'::after').content.includes('✓') && getComputedStyle(query('.system-source')).backgroundColor !== 'rgba(0, 0, 0, 0)';
      result.systemAutomatic = !query('[value="system"]') && !query('.primary').disabled && query('[type=search]').disabled;
      query('.system-source').click();
      query('.system-source').click();
      result.systemCannotDeselect = !query('.primary').disabled && query('.primary').textContent === 'Make Selection';
      query('#audio-tab-0').click();
      result.appRestored = !query('.primary').disabled && query('[value="200"]').checked && !query('.refresh').disabled && actionPositions()===appActions;
      query('#audio-tab-1').click();
      query('.primary').click();
      await new Promise(resolve => setTimeout(resolve, 240));
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
    const componentSearch = await run(`(() => {
      const field=pickerRoot.querySelector('[type=search]');
      field.value='sdfsdf';field.dispatchEvent(new Event('input'));
      const glyph=pickerRoot.querySelector('.clear-search svg');
      const sameInput=field===pickerRoot.querySelector('[type=search]')&&pickerRoot.activeElement===field;
      const circleClear=glyph.dataset.dpeIcon==='clear'&&glyph.getBoundingClientRect().width===16;
      const selectable=getComputedStyle(field).userSelect==='text'&&getComputedStyle(pickerRoot.querySelector('h2')).userSelect==='none';
      return {sameInput,circleClear,selectable};
    })()`);
    for (const [name, value] of Object.entries(componentSearch)) assert.equal(value, true, name);
    fs.writeFileSync(
      path.join(output, "search-value.png"),
      (await window.webContents.capturePage(searchBounds)).toPNG(),
    );
    await run("pickerRoot.querySelector('.clear-search').click()");
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
    await run("new Promise(resolve => setTimeout(resolve, 240))");
    const escapeKey = await run(
      "replies.at(-1) === 'none' && !document.querySelector('#app-mount > div')",
    );
    assert.equal(escapeKey, true, "Escape closes without audio");
    await run("openPicker(); new Promise(resolve => setTimeout(resolve, 240));");
    const inside = await run(
      "(() => { const r=pickerRoot.querySelector('dialog').getBoundingClientRect(); return {x:Math.floor(r.left+10),y:Math.floor(r.top+10)}; })()",
    );
    window.webContents.sendInputEvent({
      type: "mouseDown",
      button: "left",
      clickCount: 1,
      ...inside,
    });
    window.webContents.sendInputEvent({ type: "mouseMove", x: 10, y: 10 });
    window.webContents.sendInputEvent({
      type: "mouseUp",
      button: "left",
      clickCount: 1,
      x: 10,
      y: 10,
    });
    await run("new Promise(requestAnimationFrame)");
    const dragOutKeptOpen = await run(
      "pickerRoot.querySelector('dialog').dataset.state === 'open'",
    );
    assert.equal(dragOutKeptOpen, true, "Dragging out of the modal does not cancel");
    window.webContents.sendInputEvent({
      type: "mouseDown",
      button: "left",
      clickCount: 1,
      x: 10,
      y: 10,
    });
    window.webContents.sendInputEvent({
      type: "mouseUp",
      button: "left",
      clickCount: 1,
      x: 10,
      y: 10,
    });
    await run("new Promise(resolve => setTimeout(resolve,240))");
    const backdropDismiss = await run(
      "replies.at(-1) === 'none' && !document.querySelector('#app-mount > div')",
    );
    assert.equal(backdropDismiss, true, "Backdrop click closes without audio");
    await run("openPicker();");
    await run(
      `document.querySelector('#app-mount').style.cssText = '--background-surface-high:#fff;--background-base-low:#f2f3f5;--background-base-lowest:#fafafa;--background-surface-highest:#fff;--background-mod-subtle:#ebedef;--border-subtle:#ddd;--input-background:#fff;--text-default:#313338;--text-muted:#5c5e66;--interactive-normal:#80848e;--background-modifier-hover:#e3e5e8;--background-modifier-selected:#e3e5e8;--text-link:#0067b8'`,
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
        cardWidth: 204,
        cardHeight: 166,
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
    const lifecycle = await run(`(async () => {
      const before = replies.length;
      openPicker();
      document.querySelector('#app-mount > div').remove();
      await new Promise(resolve => setTimeout(resolve, 20));
      const unmountCancelled = replies.length === before + 1 && replies.at(-1) === 'none';
      dismiss();
      const noDuplicateReply = replies.length === before + 1;
      const sample = 'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><circle cx="24" cy="24" r="20" fill="blue"/></svg>');
      let calls = 0;
      const many = [options[0], ...Array.from({length:8}, (_, i) => ({id:String(i), label:'Application '+i, detail:''}))];
      dismiss = picker.show(many, value => replies.push(value), document, async () => {
        if (++calls <= 4) throw new Error('unreadable icon');
        return sample;
      });
      await new Promise(resolve => setTimeout(resolve, 100));
      const iconFailureContinues = calls === 8 && pickerRoot.querySelectorAll('img').length === 4;
      dismiss();
      return {unmountCancelled, noDuplicateReply, iconFailureContinues};
    })()`);
    for (const [name, value] of Object.entries(lifecycle)) assert.equal(value, true, name);
    const refreshState = await run(`(async () => {
      dismiss();
      options = [{id:'system',label:'System audio',detail:'All applications'}, ...Array.from({length:12},(_,i)=>({id:String(i+200),label:'Player '+i,detail:'Playing music'}))];
      openPicker();
      await new Promise(resolve => setTimeout(resolve,240));
      const query=s=>pickerRoot.querySelector(s);
      const host=document.querySelector('#app-mount > div');
      const modal=query('dialog');
      const search=query('[type=search]');
      search.value='Player'; search.dispatchEvent(new Event('input'));
      const chosen=query('[value="205"]'); chosen.click();
      chosen.focus({preventScroll:true});
      query('.content').scrollTop=100;
      const scroll=query('.content').scrollTop;
      query('.refresh').click();
      const busy=query('.refresh').disabled&&query('.primary').disabled&&query('.refresh').getAttribute('aria-busy')==='true';
      const count=replies.length;
      query('.refresh').click();
      const duplicateBlocked=replies.length===count;
      dismiss.update([...options].reverse(),value=>replies.push(value));
      const retained=document.querySelector('#app-mount > div')===host&&query('dialog')===modal&&query('[type=search]')===search&&query('[value="205"]')===chosen;
      const unchanged=search.value==='Player'&&chosen.checked&&query('.content').scrollTop===scroll&&pickerRoot.activeElement===chosen;
      const ready=!query('.refresh').disabled&&!query('.primary').disabled&&!query('.refresh').hasAttribute('aria-busy');
      const noAnimationRestart=modal.dataset.state==='open'&&Number(getComputedStyle(modal).opacity)===1;
      query('.refresh').focus(); query('.refresh').click();
      dismiss.update(options, value=>replies.push(value));
      const repeated=pickerRoot.activeElement===query('.refresh')&&query('dialog')===modal;
      dismiss.update(options.map(o=>o.id==='204'?{...o,label:' ',detail:''}:o),value=>replies.push(value));
      const blankRow=query('[value="204"]').closest('.source');
      const missingRefreshedMetadata=blankRow.querySelector('strong').textContent==='Application'&&blankRow.querySelector('small').textContent==='Application audio'&&blankRow.title==='Application — Application audio';
      chosen.focus(); query('.refresh').click();
      dismiss.update(options.filter(o=>o.id!=='205'),value=>replies.push(value));
      const removedSelection=query('[value="200"]').checked&&pickerRoot.activeElement===query('[value="200"]');
      query('.refresh').click();
      query('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));
      dismiss.update(options,value=>replies.push(value));
      await new Promise(resolve=>setTimeout(resolve,240));
      const cancelDuringRefresh=!host.isConnected&&replies.at(-1)==='none';
      const lateIgnored=dismiss.update(options)===false&&!host.isConnected;
      return {busy,duplicateBlocked,retained,unchanged,ready,noAnimationRestart,repeated,missingRefreshedMetadata,removedSelection,cancelDuringRefresh,lateIgnored};
    })()`);
    for (const [name, value] of Object.entries(refreshState)) assert.equal(value, true, name);
    const motion = await run(`(async () => {
      openPicker();
      await new Promise(resolve=>setTimeout(resolve,70));
      const d=pickerRoot.querySelector('dialog');
      const opening=getComputedStyle(d);
      const opacity=Number(opening.opacity);
      const scale=new DOMMatrixReadOnly(opening.transform).a;
      const entering=opacity>0&&opacity<1&&scale>.7&&scale<1;
      const backdropOpacity=Number(getComputedStyle(d,'::backdrop').opacity);
      const backdropFades=backdropOpacity>0&&backdropOpacity<1;
      pickerRoot.querySelector('.secondary').click();
      await new Promise(resolve=>setTimeout(resolve,50));
      const retainedOnExit=d.isConnected&&d.dataset.state==='closing'&&Number(getComputedStyle(d).opacity)<=opacity+.1;
      await new Promise(resolve=>setTimeout(resolve,240));
      const exited=!d.isConnected&&replies.at(-1)==='none';
      openPicker();
      pickerRoot.querySelector('.secondary').click();
      const before=replies.length; dismiss();
      await new Promise(resolve=>setTimeout(resolve,240));
      return {entering,backdropFades,retainedOnExit,exited,externalCancelStopsReply:replies.length===before};
    })()`);
    for (const [name, value] of Object.entries(motion)) assert.equal(value, true, name);
    window.webContents.debugger.attach("1.3");
    await window.webContents.debugger.sendCommand("Emulation.setEmulatedMedia", {
      features: [{ name: "prefers-reduced-motion", value: "reduce" }],
    });
    const reducedMotion = await run(`(async () => {
      openPicker(); await new Promise(resolve=>setTimeout(resolve,70));
      const d=pickerRoot.querySelector('dialog'), style=getComputedStyle(d);
      const stationary=style.transform==='none'||new DOMMatrixReadOnly(style.transform).a===1;
      const crossFade=Number(style.opacity)>0&&Number(style.opacity)<1;
      pickerRoot.querySelector('.secondary').click();
      const reducedExitStationary=d.isConnected&&d.dataset.state==='closing'&&(getComputedStyle(d).transform==='none'||new DOMMatrixReadOnly(getComputedStyle(d).transform).a===1);
      await new Promise(resolve=>setTimeout(resolve,240));
      const exited=!d.isConnected&&replies.at(-1)==='none';
      return {stationary,crossFade,reducedExitStationary,exited};
    })()`);
    window.webContents.debugger.detach();
    for (const [name, value] of Object.entries(reducedMotion)) assert.equal(value, true, name);
    const glyphs = await run(`(async () => {
      const clean=picker.officialIcon(document,'<svg viewBox="0 0 24 24" onload="alert(1)"><path fill="red" d="M1 1L2 2" /></svg>');
      const transparentIcon=picker.officialIcon(document,'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="transparent"/><path d="M1 1L2 2" fill="currentColor"/></svg>');
      const transparent=transparentIcon.querySelector('circle').getAttribute('fill')==='transparent';
      const fallback=picker.createControls(document).icon('refresh');
      const sharedFallback=fallback.dataset.iconOrigin==='fallback'&&fallback.getAttribute('viewBox')==='0 0 16 16'&&fallback.querySelector('path');
      const blocked=picker.officialIcon(document,'<svg><image href="https://example.invalid/image" /></svg>')===null;
      const sanitized=clean&&!clean.hasAttribute('onload')&&clean.querySelector('path').getAttribute('fill')==='currentColor';
      dismiss=picker.show(options,value=>replies.push(value),document,async()=>null,async()=>({reducedMotion:true,icons:{refresh:'<svg viewBox="0 0 24 24"><path d="M1 1L2 2"/></svg>'}}));
      await new Promise(resolve=>setTimeout(resolve,40));
      const official=pickerRoot.querySelector('.refresh svg').dataset.iconOrigin==='discord';
      const discordReducedMotion=pickerRoot.querySelector('dialog').dataset.reducedMotion==='true'&&getComputedStyle(pickerRoot.querySelector('dialog')).transform==='none';
      dismiss();
      let releasePreference;
      dismiss=picker.show(options,value=>replies.push(value),document,async()=>null,()=>new Promise(resolve=>{releasePreference=resolve;}));
      await new Promise(resolve=>setTimeout(resolve,40));
      const waiting=pickerRoot.querySelector('dialog').dataset.state==='entering'&&Number(getComputedStyle(pickerRoot.querySelector('dialog')).opacity)===0;
      releasePreference({reducedMotion:true});
      await new Promise(resolve=>setTimeout(resolve,40));
      const delayedPreferenceStationary=getComputedStyle(pickerRoot.querySelector('dialog')).transform==='none';
      dismiss();
      dismiss=picker.show(options,value=>replies.push(value),document,async()=>null,()=>new Promise(()=>{}));
      await new Promise(resolve=>setTimeout(resolve,140));
      const slowFallback=pickerRoot.querySelector('dialog').dataset.state==='open'&&getComputedStyle(pickerRoot.querySelector('dialog')).transform==='none'&&Number(getComputedStyle(pickerRoot.querySelector('dialog')).opacity)>0;
      dismiss();return {blocked,sanitized,transparent,sharedFallback:Boolean(sharedFallback),official,discordReducedMotion,waiting,delayedPreferenceStationary,slowFallback};
    })()`);
    for (const [name, value] of Object.entries(glyphs)) assert.equal(value, true, name);
    const nativeTokens = await run(`(async () => {
      dismiss=picker.show(options,value=>replies.push(value),document,async()=>null,async()=>({reducedMotion:false,tokens:{'--dpe-discord-modal-width':'54rem','--dpe-discord-source-card-width':'13rem','--dpe-discord-control-height':'44px','--dpe-discord-scrollbar-width':'16px','--dpe-discord-source-picker-height':'url(https://invalid.test)','--font-primary':'invalid'}}));
      await new Promise(resolve=>setTimeout(resolve,240));
      const d=pickerRoot.querySelector('dialog');
      const liveGeometry=d.getBoundingClientRect().width===864&&pickerRoot.querySelector('.source:not([hidden])').getBoundingClientRect().width===208&&pickerRoot.querySelector('.search-field').getBoundingClientRect().height===44;
      const bounded=d.style.getPropertyValue('--dpe-discord-source-picker-height')===''&&d.style.getPropertyValue('--font-primary')==='';
      document.querySelector('#app-mount').style.setProperty('--control-input-height-md','48px');
      document.querySelector('#app-mount').style.setProperty('--space-24','32px');
      document.querySelector('#app-mount').style.setProperty('--radius-md','18px');
      const inherited=pickerRoot.querySelector('.search-field').getBoundingClientRect().height===48;
      const inheritedSpacing=getComputedStyle(d).paddingLeft==='32px'&&getComputedStyle(d).paddingTop==='32px';
      const inheritedRadius=getComputedStyle(d).borderTopLeftRadius==='18px'&&getComputedStyle(pickerRoot.querySelector('.tabs')).borderTopLeftRadius==='18px';
      for(const key of ['--control-input-height-md','--space-24','--radius-md'])document.querySelector('#app-mount').style.removeProperty(key);
      dismiss();return {liveGeometry,bounded,inherited,inheritedSpacing,inheritedRadius};
    })()`);
    for (const [name, value] of Object.entries(nativeTokens)) assert.equal(value, true, name);
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify(
        {
          ...result,
          narrow,
          overflow,
          artwork,
          lifecycle,
          refreshState,
          motion,
          reducedMotion,
          glyphs,
          nativeTokens,
          keyboardContained,
          escapeKey,
          dragOutKeptOpen,
          backdropDismiss,
        },
        null,
        2,
      ),
    );
    console.log(
      JSON.stringify({
        ...result,
        narrow,
        overflow,
        artwork,
        lifecycle,
        refreshState,
        motion,
        reducedMotion,
        glyphs,
        nativeTokens,
        keyboardContained,
        escapeKey,
        dragOutKeptOpen,
        backdropDismiss,
      }),
    );
  } catch (error) {
    console.error(error);
    app.exit(1);
  } finally {
    window.destroy();
    app.quit();
  }
});
