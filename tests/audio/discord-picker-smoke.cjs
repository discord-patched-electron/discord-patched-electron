// Production custom picker against current Discord CSS, on a signed-out profile.
// Only this BrowserWindow is captured. No account, media, or desktop interaction.
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const output = process.env.DPE_PICKER_OUTPUT;
if (!output) throw Error("Set DPE_PICKER_OUTPUT to a fresh output directory");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.disableHardwareAcceleration();
const timeout = setTimeout(() => app.exit(2), 90000);
app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: Number(process.env.DPE_PICKER_WIDTH) || 1269,
    height: Number(process.env.DPE_PICKER_HEIGHT) || 772,
    useContentSize: true,
    webPreferences: {
      preload: path.join(__dirname, "picker-smoke-preload.cjs"),
      contextIsolation: true,
      sandbox: false,
    },
  });
  const run = (code) => window.webContents.executeJavaScriptInIsolatedWorld(999, [{ code }]);
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  try {
    await window.loadURL("https://canary.discord.com/login");
    window.webContents.setZoomFactor(Number(process.env.DPE_PICKER_ZOOM) || 1);
    await run(`new Promise((resolve, reject) => {
      if (document.querySelector('input[name=email]')) return resolve();
      const deadline = setTimeout(() => { observer.disconnect(); reject(Error('Login form missing')); }, 20000);
      const observer = new MutationObserver(() => {
        if (document.querySelector('input[name=email]')) {
          clearTimeout(deadline); observer.disconnect(); resolve();
        }
      });
      observer.observe(document.body, {childList:true,subtree:true});
    })`);
    await run(`
      window.replies=[];
      window.dismiss=picker.show([
        {id:'system', label:'System audio', detail:'All applications except Discord'},
        {id:'200', label:'Zen Browser', detail:'Music', iconNames:['zen-bin']},
        {id:'300', label:'mpv', detail:'Video playback', iconNames:['mpv']}
      ], value => replies.push(value));
      document.fonts.ready;
    `);
    await run("new Promise(resolve => setTimeout(resolve, 240))");
    const themes = [];
    for (const theme of ["theme-dark", "theme-darker", "theme-midnight", "theme-light"]) {
      const result = await run(`(async () => {
        const themeNodes = [...document.querySelectorAll('.theme-dark,.theme-darker,.theme-midnight,.theme-light')];
        for (const node of themeNodes) {
          node.classList.remove('theme-dark','theme-darker','theme-midnight','theme-light');
          node.classList.add(${JSON.stringify(theme)});
          if (${JSON.stringify(theme)} !== 'theme-light') node.classList.add('theme-dark');
        }
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const query = s => pickerRoot.querySelector(s);
        const style = getComputedStyle(query('dialog'));
        const hostStyle = getComputedStyle(document.querySelector('#app-mount'));
        const rect = query('dialog').getBoundingClientRect();
        return {
          theme: ${JSON.stringify(theme)},
          modal: query('dialog').matches(':modal'),
          customDialog: query('dialog').localName === 'dialog',
          officialIcons: [...pickerRoot.querySelectorAll('[data-icon-origin="discord"]')].map(node=>node.dataset.dpeIcon),
          reducedMotion: query('dialog').dataset.reducedMotion === 'true',
          nativeGeometryTokens:Object.fromEntries([...query('dialog').style].filter(key=>key.startsWith('--dpe-discord-')).map(key=>[key,query('dialog').style.getPropertyValue(key)])),
          font: style.fontFamily,
          tokens: Object.fromEntries(['--background-base-lowest','--background-base-lower','--interactive-background-active','--text-default','--brand-500'].map(key => [key,hostStyle.getPropertyValue(key).trim()])),
          background: style.backgroundColor,
          card: getComputedStyle(query('.source:has(:checked)')).backgroundColor,
          text: style.color,
          link: getComputedStyle(query('.hint a')).color,
          linkUnderline: getComputedStyle(query('.hint a')).textDecorationLine,
          noOverflow: query('dialog').scrollWidth <= query('dialog').clientWidth,
          cssRect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
          controls:{tabs:query('.tabs').getBoundingClientRect().height,tab:query('.tab').getBoundingClientRect().height,primary:query('.primary').getBoundingClientRect().width,cardWidth:query('.source:not([hidden])').getBoundingClientRect().width,cardHeight:query('.source:not([hidden])').getBoundingClientRect().height},
          rect:{x:Math.round(rect.x*${window.webContents.getZoomFactor()}),y:Math.round(rect.y*${window.webContents.getZoomFactor()}),width:Math.round(rect.width*${window.webContents.getZoomFactor()}),height:Math.round(rect.height*${window.webContents.getZoomFactor()})}
        };
      })()`);
      assert.equal(result.modal, true);
      assert.equal(result.customDialog, true);
      assert.equal(result.noOverflow, true);
      assert.equal(result.linkUnderline, "none");
      assert.ok(result.tokens["--background-base-lowest"]);
      // capturePage may otherwise return the compositor frame preceding the
      // class change even though layout/computed styles already reflect it.
      await new Promise((resolve) => setTimeout(resolve, 150));
      fs.writeFileSync(
        path.join(output, `${theme}.png`),
        (await window.webContents.capturePage(result.rect)).toPNG(),
      );
      themes.push(result);
    }
    assert.notEqual(themes[1].background, themes[3].background);
    assert.notEqual(themes[1].text, themes[3].text);
    assert.equal(await run(`replies.length`), 0);
    await run(
      `pickerRoot.querySelector('[value="300"]').click(); pickerRoot.querySelector('.primary').click()`,
    );
    await run("new Promise(resolve => setTimeout(resolve,240))");
    assert.equal(await run(`replies.at(-1)`), "300");
    fs.writeFileSync(path.join(output, "themes.json"), JSON.stringify(themes, null, 2));
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify({ themes, confirmed: true }, null, 2),
    );
    console.log(JSON.stringify({ themes, confirmed: true }));
  } catch (error) {
    console.error(error);
    app.exitCode = 1;
  } finally {
    clearTimeout(timeout);
    window.destroy();
    app.exit(app.exitCode || 0);
  }
});
