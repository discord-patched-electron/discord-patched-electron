// Run twice with a fresh DPE_SPELLCHECK_OUTPUT: the second pass checks persistence.
const { app, BrowserWindow, session } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_SPELLCHECK_OUTPUT;
if (!output) throw Error("DPE_SPELLCHECK_OUTPUT must be a disposable directory");
fs.mkdirSync(path.join(output, "profile"), { recursive: true });
app.setPath("userData", path.join(output, "profile"));
const adapter = require("../../integration/desktop/spellcheck.cjs");
const englishOnly = process.env.DPE_SPELLCHECK_ENGLISH === "1";
const config = {
  spellcheckLanguages: englishOnly ? ["en-GB", "en"] : ["en-US", "fr"],
  spellcheckEnabled: true,
};
global.settings = { get: () => config };
adapter.install(require("electron"));
app.on("window-all-closed", () => {});
const timeout = setTimeout(() => {
  console.error("Spell check fixture timed out");
  app.exit(1);
}, 45000);
app
  .whenReady()
  .then(async () => {
    const s = session.defaultSession;
    const repeat = process.env.DPE_SPELLCHECK_REPEAT === "1";
    let downloadAttempts = 0;
    s.on("spellcheck-dictionary-download-begin", () => downloadAttempts++);
    if (repeat)
      s.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (_details, callback) =>
        callback({ cancel: true }),
      );
    const ready = new Set();
    const initialized = new Promise((resolve) => {
      s.on("spellcheck-dictionary-initialized", (_, language) => {
        ready.add(language);
        if (config.spellcheckLanguages.every((language) => ready.has(language))) resolve();
      });
    });
    adapter.apply(config);
    const w = new BrowserWindow({
      width: 600,
      height: 400,
      webPreferences: {
        spellcheck: true,
        preload: path.join(__dirname, "preload.cjs"),
        sandbox: false,
        contextIsolation: true,
      },
    });
    await w.loadURL(
      'data:text/html,<textarea style="position:absolute;left:10px;top:10px;width:500px;height:200px;font:24px monospace" spellcheck="true"></textarea>',
    );
    await initialized;
    assert.equal(
      await w.webContents.executeJavaScript('window.spellcheckProbe.isWordMisspelled("hello")'),
      false,
    );
    if (!englishOnly)
      assert.equal(
        await w.webContents.executeJavaScript('window.spellcheckProbe.isWordMisspelled("bonjour")'),
        false,
      );
    assert.equal(
      await w.webContents.executeJavaScript('window.spellcheckProbe.isWordMisspelled("helllo")'),
      true,
    );
    assert.ok(
      (
        await w.webContents.executeJavaScript('window.spellcheckProbe.getWordSuggestions("helllo")')
      ).includes("hello"),
    );
    adapter.setLanguages(s, ["en-US"]);
    assert.deepEqual(s.getSpellCheckerLanguages(), config.spellcheckLanguages);
    const personal = "dpebilingualtestword";
    const previous = await s.listWordsInSpellCheckerDictionary();
    if (repeat) assert.ok(previous.includes(personal));
    else {
      assert.equal(s.addWordToSpellCheckerDictionary(personal), true);
      assert.ok((await s.listWordsInSpellCheckerDictionary()).includes(personal));
    }
    await w.webContents.executeJavaScript('document.querySelector("textarea").focus()');
    w.webContents.insertText("helllo ");
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const context = new Promise((resolve) =>
      w.webContents.once("context-menu", (_, params) => resolve(params)),
    );
    w.webContents.sendInputEvent({
      type: "mouseDown",
      button: "right",
      x: 35,
      y: 25,
      clickCount: 1,
    });
    w.webContents.sendInputEvent({ type: "mouseUp", button: "right", x: 35, y: 25, clickCount: 1 });
    const params = await context;
    assert.equal(params.misspelledWord, "helllo");
    assert.ok(params.dictionarySuggestions.includes("hello"));
    w.webContents.replaceMisspelling("hello");
    assert.equal(
      await w.webContents.executeJavaScript('document.querySelector("textarea").value'),
      "hello ",
    );
    // Match the reported typo in both native textareas and Discord-style rich
    // text. Chromium deliberately defers the unfinished word at the caret.
    for (const richText of [false, true]) {
      await w.webContents.executeJavaScript(`(() => {
        document.body.innerHTML = ${JSON.stringify('<textarea spellcheck="true"></textarea>')};
        if (${richText}) document.body.innerHTML = '<div contenteditable="true" spellcheck="true"></div>';
        const input = document.body.firstElementChild;
        input.style.cssText = 'position:absolute;left:10px;top:10px;width:500px;height:200px;font:24px monospace';
        input.focus();
      })()`);
      w.webContents.insertText("convienient");
      await new Promise((resolve) => setTimeout(resolve, 1000));
      assert.equal(
        await w.webContents.executeJavaScript(
          'window.spellcheckProbe.isWordMisspelled("convienient")',
        ),
        true,
      );
      const contextAtWord = () =>
        new Promise((resolve) => {
          w.webContents.once("context-menu", (_, params) => resolve(params));
          w.webContents.sendInputEvent({
            type: "mouseDown",
            button: "right",
            x: 35,
            y: 25,
            clickCount: 1,
          });
          w.webContents.sendInputEvent({
            type: "mouseUp",
            button: "right",
            x: 35,
            y: 25,
            clickCount: 1,
          });
        });
      assert.equal(
        (await contextAtWord()).misspelledWord,
        "",
        "unfinished word at the caret is deferred",
      );
      // Use a fresh editor so the first context click cannot change the next
      // typing sequence's selection or focus.
      await w.webContents.executeJavaScript(`(() => {
        const previous = document.body.firstElementChild;
        const input = previous.cloneNode(false);
        if (input.tagName === "TEXTAREA") input.value = "";
        previous.replaceWith(input);
        input.focus();
      })()`);
      w.webContents.insertText("convienient ");
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const result = await contextAtWord();
      assert.equal(result.misspelledWord, "convienient", richText ? "rich text" : "textarea");
      assert.ok(result.dictionarySuggestions.includes("convenient"));
      w.webContents.replaceMisspelling("convenient");
      assert.equal(
        await w.webContents.executeJavaScript(
          "document.body.firstElementChild.value ?? document.body.firstElementChild.textContent",
        ),
        richText ? "convenient\u00a0" : "convenient ",
      );
    }
    adapter.apply({ ...config, spellcheckEnabled: false });
    assert.equal(s.spellCheckerEnabled, false);
    adapter.apply(config);
    assert.equal(s.spellCheckerEnabled, true);
    await adapter.refresh(true);
    if (repeat)
      assert.equal(downloadAttempts, 0, "cached dictionaries work offline without a download");
    const report = {
      languages: s.getSpellCheckerLanguages(),
      englishAndFrenchAccepted: !englishOnly,
      reportedTypoInTextareaAndRichText: true,
      unfinishedWordDeferred: true,
      misspellingDetected: true,
      contextMenuSuggestions: true,
      replacement: true,
      personalWordPersisted: repeat,
      offlineCacheVerified: repeat,
      downloadAttempts,
      liveToggle: true,
      status: adapter.status(),
    };
    fs.writeFileSync(
      path.join(output, repeat ? "repeat.json" : "result.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(JSON.stringify(report));
    clearTimeout(timeout);
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
