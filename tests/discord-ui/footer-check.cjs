// Real DOM, MutationObserver and keyboard activation. No Discord/account/network.
const assert = require("node:assert/strict");
const settings = require("../../integration/discord-ui/settings.cjs");
module.exports = async function checkFooter(window) {
  const run = (code) => window.webContents.executeJavaScript(code);
  const install = (request = {}) =>
    run(`(${settings.toString()})(${JSON.stringify(request)}, {watch(){}})`);
  const settle = () =>
    run("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  const count = () => run('document.querySelectorAll("[data-dpe-settings-version]").length');
  await window.loadURL(
    "data:text/html;charset=utf-8," +
      encodeURIComponent(`<!doctype html>
      <style>:root {--space-4:4px;--text-muted:rgb(125,130,140);--border-focus:rgb(0,176,244)} button {outline:0} .native-xxs {font:10px/14px sans-serif} .compact__native {display:flex;flex-direction:column;gap:var(--space-4);width:fit-content} .hiddenVisually_native {position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}</style>
      <div id="unrelated"><div class="compactInfo__embed"><span><span class="versionHash__embed">unrelated</span></span></div></div>
      <main></main>`),
  );
  await run(`
      globalThis.calls = [];
      globalThis.DiscordNative = {ipc:{send:(...args)=>calls.push(args)}};
      globalThis.copyCalls = 0;
      globalThis.observerCount = 0;
      const NativeObserver = MutationObserver;
      globalThis.MutationObserver = class extends NativeObserver {constructor(fn){super(fn);observerCount++}};
      globalThis.mount = () => {
        document.querySelector('main').innerHTML = '<aside class="sidebar__native"><div class="private-profile">Fixture profile</div><nav><div data-list-item-id="settings-sidebar___logout_sidebar_item">Déconnexion</div><footer><div role="button" aria-describedby="copy-description" class="clickable__native compact__native"><div class="compactInfo__native"><span class="native-xxs" style="color:var(--text-muted)">canary 659642 <span class="versionHash__native">(ed4f240)</span></span><span class="native-xxs">0.0.123</span></div></div><span id="tooltip-marker" style="display:none"></span><span id="copy-description" class="hiddenVisually_native">Copier les informations</span><div class="links_native">Confidentialité · Conditions</div></footer></nav></aside>';
        document.querySelector('.clickable__native').onclick=()=>copyCalls++;
      };
      void 0;
    `);
  await install({ version: "2026.10.31" });
  assert.equal(await count(), 0, "unrelated compact content is not a settings footer");
  await run("mount()");
  await settle();
  assert.equal(await count(), 1);
  assert.deepEqual(
    await run(`(() => {
      const button=document.querySelector('[data-dpe-settings-version]');
      const text=document.querySelector('.compactInfo__native').firstElementChild;
      return {tag:button.tagName, text:button.textContent, previous:button.previousElementSibling.className,
        next:button.nextElementSibling.className, className:button.className,
        font:getComputedStyle(button).font, nativeFont:getComputedStyle(text).font,
        color:getComputedStyle(button).color, nativeColor:getComputedStyle(text).color,
        descendants:button.childElementCount, profiles:document.querySelectorAll('.private-profile').length};
    })()`),
    {
      tag: "BUTTON",
      text: "Discord Patched Electron 2026.10.31",
      previous: "hiddenVisually_native",
      next: "links_native",
      className: "native-xxs",
      font: "10px / 14px sans-serif",
      nativeFont: "10px / 14px sans-serif",
      color: "rgb(125, 130, 140)",
      nativeColor: "rgb(125, 130, 140)",
      descendants: 0,
      profiles: 1,
    },
  );
  assert.equal(
    await run("getComputedStyle(document.querySelector('[data-dpe-settings-version]')).cursor"),
    "default",
  );
  assert.equal(
    await run("document.getElementById('tooltip-marker').previousElementSibling.className"),
    "clickable__native compact__native",
    "Discord's tooltip marker keeps its native target",
  );
  await run("document.documentElement.style.setProperty('--space-4','6px')");
  assert.equal(
    await run(
      `(()=>{const button=document.querySelector('[data-dpe-settings-version]'),anchor=document.querySelector('.clickable__native');return button.getBoundingClientRect().top-anchor.getBoundingClientRect().bottom})()`,
    ),
    6,
    "row spacing follows the theme token",
  );
  await run("document.querySelector('[data-dpe-settings-version]').click()");
  assert.equal(
    await run("copyCalls"),
    0,
    "the separate action does not bubble into copy-build-info",
  );
  assert.deepEqual(await run("calls"), [["DISCORD_UPDATED_QUOTES", "o"]]);
  window.focus();
  window.webContents.focus();
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
  await settle();
  await run("document.querySelector('[data-dpe-settings-version]').focus()");
  assert.deepEqual(
    await run(`(() => {
      const style = getComputedStyle(document.querySelector('[data-dpe-settings-version]'));
      return [style.outlineColor, style.outlineStyle, style.outlineWidth];
    })()`),
    ["rgb(0, 176, 244)", "solid", "2px"],
    "keyboard focus remains visible despite Discord's global outline reset",
  );
  for (const keyCode of ["Return", " "]) {
    window.webContents.sendInputEvent({ type: "keyDown", keyCode });
    window.webContents.sendInputEvent({ type: "char", keyCode });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode });
    await settle();
  }
  assert.equal(await run("calls.length"), 3, "Enter and Space activate the settings button");
  await install();
  await install({ version: "2026.11.1" });
  await settle();
  assert.equal(await count(), 1);
  assert.equal(await run("observerCount"), 1, "reinstallation keeps a single observer");
  assert.equal(
    await run("document.querySelector('[data-dpe-settings-version]').textContent"),
    "Discord Patched Electron 2026.11.1",
  );
  await run("document.documentElement.style.setProperty('--text-muted','rgb(50,60,70)')");
  assert.equal(
    await run("getComputedStyle(document.querySelector('[data-dpe-settings-version]')).color"),
    "rgb(50, 60, 70)",
  );
  await run(
    `const original=document.querySelector('[data-dpe-settings-version]'); original.after(original.cloneNode(true))`,
  );
  await settle();
  assert.equal(await count(), 1, "native rerenders or mod duplicates leave one action");
  await run("document.querySelector('[data-dpe-settings-version]').remove()");
  await settle();
  assert.equal(await count(), 1, "removing the injected line alone is repaired");
  await run("document.querySelector('main').replaceChildren()");
  await settle();
  assert.equal(await count(), 0);
  await run("mount()");
  await settle();
  assert.equal(await count(), 1, "closing and reopening settings restores one line");
  await run("document.querySelector('[data-dpe-settings-version]').click()");
  assert.equal(await run("calls.length"), 4);
  assert.equal(await run("copyCalls"), 0);
  await run("document.querySelector('.clickable__native').click()");
  assert.equal(await run("copyCalls"), 1, "native copy-build-info remains unchanged");
  await run("mount(); document.querySelector('[data-list-item-id]').remove()");
  await settle();
  assert.equal(await count(), 0, "non-settings sidebars do not receive the footer");

  await run(
    "mount(); document.querySelector('.clickable__native').removeAttribute('aria-describedby'); document.getElementById('copy-description').remove()",
  );
  await settle();
  assert.equal(
    await run("document.getElementById('tooltip-marker').previousElementSibling.className"),
    "clickable__native compact__native",
  );
  assert.equal(
    await run("document.querySelector('[data-dpe-settings-version]').previousElementSibling.id"),
    "tooltip-marker",
  );
  await run(
    "document.querySelector('.clickable__native').after(document.querySelector('[data-dpe-settings-version]'))",
  );
  await settle();
  assert.equal(
    await run("document.getElementById('tooltip-marker').previousElementSibling.className"),
    "clickable__native compact__native",
    "existing misplaced injected rows are repaired",
  );
  await run("document.getElementById('tooltip-marker').remove()");
  await settle();
  assert.equal(
    await run("document.querySelector('[data-dpe-settings-version]').nextElementSibling.className"),
    "links_native",
    "a footer without tooltip nodes retains its link order",
  );
};
