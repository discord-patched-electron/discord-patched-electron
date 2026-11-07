// Native prefilled-editor regression. All HTTPS pages below are local fixtures;
// no account, Discord API request, message or call is involved.
const { app, BrowserWindow, session } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_SPELLCHECK_OUTPUT;
if (!output) throw Error("DPE_SPELLCHECK_OUTPUT must be a disposable directory");
fs.mkdirSync(path.join(output, "profile"), { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.on("window-all-closed", () => {});
const timeout = setTimeout(() => app.exit(1), 60000);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check) {
  for (let attempt = 0; attempt < 40; attempt++) {
    if (await check()) return;
    await delay(25);
  }
  throw Error("Native editing operation did not complete");
}
app
  .whenReady()
  .then(async () => {
    const s = session.defaultSession;
    s.setSpellCheckerLanguages(["en-GB"]);
    const ready = new Promise((resolve) => s.once("spellcheck-dictionary-initialized", resolve));
    const window = new BrowserWindow({
      width: 600,
      height: 400,
      webPreferences: { spellcheck: true },
    });
    await window.loadURL("data:text/html,<body></body>");
    await ready;
    s.protocol.handle(
      "https",
      () =>
        new Response("<html><body></body></html>", { headers: { "content-type": "text/html" } }),
    );
    let frame;
    const run = (code) => (frame || window.webContents).executeJavaScript(code);
    const click = (button, x, y) => {
      window.webContents.sendInputEvent({ type: "mouseDown", button, x, y, clickCount: 1 });
      window.webContents.sendInputEvent({ type: "mouseUp", button, x, y, clickCount: 1 });
    };
    const cases = [
      { name: "prefilled", host: "canary.discord.com" },
      { name: "late-content", host: "discord.com" },
      { name: "late-spellcheck", host: "ptb.discord.com" },
      { name: "keyboard", host: "canary.discord.com" },
      { name: "disabled-element", host: "canary.discord.com", misspelled: false },
      { name: "disabled-session", host: "canary.discord.com", misspelled: false },
      { name: "no-gesture", host: "canary.discord.com", misspelled: false },
      { name: "subframe", host: "canary.discord.com", misspelled: false },
      { name: "other-origin", host: "example.com", misspelled: false },
      { name: "lookalike-origin", host: "canary.discord.com.example.com", misspelled: false },
    ];
    const checked = [];
    for (const entry of cases) {
      const { name, host, misspelled = true } = entry;
      s.spellCheckerEnabled = name !== "disabled-session";
      frame = undefined;
      await window.loadURL(`https://${host}/spelling-fixture`);
      if (name === "subframe") {
        await run(
          `document.body.innerHTML = '<iframe style="position:absolute;left:0;top:0;width:600px;height:400px;border:0" srcdoc="<body></body>"></iframe>'; void 0;`,
        );
        await until(() => window.webContents.mainFrame.frames.length === 1);
        frame = window.webContents.mainFrame.frames[0];
        await until(() => run("Boolean(document.body)"));
      }
      await run(`(() => {
      const mode = ${JSON.stringify(name)};
      document.body.innerHTML = '<button style="position:absolute;left:10px;top:10px" id="edit">Edit message</button><div id="host"></div>';
      globalThis.events = [];
      globalThis.edit = () => {
        const el = document.createElement('div');
        el.id = 'editor'; el.contentEditable = 'true'; el.spellcheck = mode !== 'disabled-element';
        el.setAttribute('data-slate-editor', 'true');
        el.style.cssText = 'position:absolute;left:10px;top:60px;font:24px monospace;width:500px;height:200px';
        const html = '<div data-slate-node="element"><span data-slate-node="text"><span data-slate-leaf="true"><span data-slate-string="true">convienient</span></span></span></div>';
        if (mode !== 'late-content') el.innerHTML = html;
        document.getElementById('host').append(el); el.focus();
        if (mode === 'late-content') el.innerHTML = html;
        const text = el.querySelector('[data-slate-string]').firstChild;
        getSelection().setBaseAndExtent(text, text.length, text, text.length);
        for (const event of ['beforeinput', 'input', 'change', 'compositionstart'])
          el.addEventListener(event, () => events.push(event));
        globalThis.originalHTML = el.innerHTML;
        if (mode === 'late-spellcheck') el.setAttribute('spellcheck', 'true');
      };
      document.getElementById('edit').onclick = edit;
      document.getElementById('edit').onkeydown = (event) => { if (event.key === 'ArrowUp') { event.preventDefault(); edit(); } };
    })()`);
      if (name === "no-gesture") await run("edit()");
      else if (name === "keyboard") {
        await run("document.getElementById('edit').focus()");
        window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Up" });
        window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Up" });
      } else click("left", 30, 20);
      await delay(2200);
      assert.deepEqual(
        await run(`({
      unchanged: editor.innerHTML === originalHTML,
      text: editor.textContent, focused: document.activeElement === editor,
      anchor: getSelection().anchorOffset, focus: getSelection().focusOffset,
      events
    })`),
        { unchanged: true, text: "convienient", focused: true, anchor: 11, focus: 11, events: [] },
        name,
      );
      const context = new Promise((resolve) =>
        window.webContents.once("context-menu", (_, params) => resolve(params)),
      );
      click("right", 35, 75);
      const result = await context;
      assert.equal(result.misspelledWord, misspelled ? "convienient" : "", name);
      if (misspelled) {
        assert.ok(result.dictionarySuggestions.includes("convenient"), name);
        window.webContents.replaceMisspelling("convenient");
        await until(async () => (await run("editor.textContent")) === "convenient");
        window.webContents.undo();
        await until(async () => (await run("editor.textContent")) === "convienient");
      }
      checked.push(name);
    }
    const report = {
      passed: true,
      checked,
      contentSelectionAndFocusPreserved: true,
      replacementUndo: true,
      versions: process.versions,
    };
    fs.writeFileSync(path.join(output, "editing.json"), JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report));
    clearTimeout(timeout);
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
