const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const {
  createController,
  patchDesktop,
  cachedLanguages,
} = require("../../integration/desktop/spellcheck.cjs");
function fixture(config = {}, options) {
  const session = new EventEmitter();
  Object.assign(session, {
    availableSpellCheckerLanguages: ["en-US", "en-GB", "fr", "de"],
    spellCheckerEnabled: true,
    languages: ["en-US"],
    getSpellCheckerLanguages() {
      return this.languages;
    },
    setSpellCheckerLanguages(languages) {
      this.languages = languages;
    },
  });
  const electron = {
    session: { defaultSession: session },
    app: { isReady: () => true, getPreferredSystemLanguages: () => ["en-GB", "fr-FR"] },
  };
  return { session, electron, controller: createController(electron, () => config, options) };
}
test("system preferences enable multiple available browser dictionaries", () => {
  const { controller } = fixture();
  assert.deepEqual(controller.apply().languages, ["en-GB", "fr"]);
});
test("explicit bilingual selection survives locale application and disables live", () => {
  const { controller } = fixture({ spellcheckLanguages: ["fr", "de", "fr", "invalid"] });
  assert.deepEqual(controller.apply().languages, ["fr", "de"]);
  assert.deepEqual(controller.apply().languages, ["fr", "de"]);
  assert.equal(controller.apply({ spellcheckEnabled: false }).enabled, false);
  assert.equal(
    controller.apply({ spellcheckEnabled: true, spellcheckLanguages: ["en-US"] }).enabled,
    true,
  );
});
test("dictionary failure is reported without logging typed text and listeners do not multiply", () => {
  const { controller, session } = fixture();
  controller.apply();
  controller.apply();
  assert.equal(session.listenerCount("spellcheck-dictionary-download-failure"), 1);
  session.emit("spellcheck-dictionary-download-failure", {}, "fr");
  assert.deepEqual(controller.status().downloads, [{ language: "fr", state: "failed" }]);
  session.emit("spellcheck-dictionary-initialized", {}, "fr");
  assert.deepEqual(controller.status().downloads, [{ language: "fr", state: "ready" }]);
});
test("dictionary API errors leave a readable settings error", () => {
  const { controller, session } = fixture();
  session.setSpellCheckerLanguages = () => {
    throw Error("unavailable");
  };
  assert.match(controller.apply().error, /unavailable/);
});
test("manual empty selection stays off across controller restarts and legacy empty arrays stay automatic", () => {
  const config = {
    spellcheckEnabled: true,
    spellcheckLanguages: [],
    spellcheckUseSystemLanguages: false,
  };
  for (let i = 0; i < 2; i++) {
    const { controller, session } = fixture(config);
    assert.equal(controller.apply().enabled, false);
    assert.deepEqual(session.languages, []);
  }
  assert.deepEqual(fixture({ spellcheckLanguages: [] }).controller.apply().languages, [
    "en-GB",
    "fr",
  ]);
});
test("minimal desktop patch routes both locale updates without changing result/learned-word IPC", () => {
  const source =
    "electron.session.defaultSession.setSpellCheckerLanguages([locale]);electron.session.defaultSession.setSpellCheckerLanguages([...languages]);SPELLCHECK_RESULT;setLearnedWords";
  const result = patchDesktop(source, "/adapter.cjs", (s, a, b) => {
    assert.equal(s.split(a).length, 2);
    return s.replace(a, b);
  });
  assert.equal(
    result,
    'require("/adapter.cjs").setLanguages(electron.session.defaultSession,[locale]);require("/adapter.cjs").setLanguages(electron.session.defaultSession,[...languages]);SPELLCHECK_RESULT;setLearnedWords',
  );
});

test("download status recognizes cached BDic files across controller restarts without confusing language variants", async (t) => {
  const fs = require("node:fs");
  const path = require("node:path");
  const os = require("node:os");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-spelling-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const valid = Buffer.alloc(64);
  valid.write("BDic");
  fs.writeFileSync(path.join(directory, "en-US-10-2.bdic"), valid);
  fs.writeFileSync(path.join(directory, "fr-FR-3-0.bdic"), valid);
  fs.writeFileSync(path.join(directory, "de-DE-3-0.bdic"), "unfinished");
  fs.writeFileSync(path.join(directory, "es-ES-3-0.bdic.tmp"), valid);
  assert.deepEqual(
    [
      ...(await cachedLanguages(directory, [
        "en",
        "en-US",
        "en-GB",
        "fr",
        "fr-FR",
        "fr-CA",
        "de",
        "es",
      ])),
    ],
    ["en", "en-US", "fr", "fr-FR"],
  );
  assert.deepEqual([...(await cachedLanguages(path.join(directory, "missing"), ["en-US"]))], []);
});

test("dictionary metadata uses stale-while-revalidate, deduplicates scans and preserves cached results on failure", async () => {
  let time = 1000,
    calls = 0;
  let result = Promise.resolve(new Set(["en-US"]));
  const { controller, session } = fixture(
    {},
    {
      now: () => time,
      scan: () => {
        calls++;
        return result;
      },
    },
  );
  assert.deepEqual(controller.status().downloads, []);
  await controller.refresh();
  assert.equal(calls, 1);
  assert.deepEqual(controller.status().downloads, [{ language: "en-US", state: "cached" }]);
  await controller.refresh();
  assert.equal(calls, 1, "fresh metadata does not rescan the filesystem");
  time += 30001;
  let complete;
  result = new Promise((resolve) => {
    complete = resolve;
  });
  assert.deepEqual(controller.status().downloads, [{ language: "en-US", state: "cached" }]);
  const pending = controller.refresh();
  assert.equal(
    controller.refresh(true),
    pending,
    "forced and automatic scans share in-flight work",
  );
  complete(new Set(["fr"]));
  await pending;
  assert.equal(calls, 2);
  assert.deepEqual(controller.status().downloads, [{ language: "fr", state: "cached" }]);
  result = Promise.resolve(new Set(["en-US", "fr"]));
  session.emit("spellcheck-dictionary-download-success", {}, "fr");
  await controller.refresh();
  assert.equal(calls, 3, "dictionary events invalidate the cache immediately");
  result = Promise.reject(Error("temporarily unavailable"));
  await controller.refresh(true);
  assert.match(controller.status().dictionaryError, /temporarily unavailable/);
  assert.equal(
    controller.apply().error,
    undefined,
    "metadata failures do not break spell checking",
  );
  assert.equal(
    controller.status().downloads.length,
    2,
    "failed refresh retains cached dictionaries",
  );
  result = Promise.resolve(new Set());
  await controller.refresh(true);
  assert.equal(controller.status().dictionaryError, undefined);
  assert.deepEqual(controller.status().downloads, [{ language: "fr", state: "ready" }]);
});

test("repeated Discord locale updates do not reinitialize unchanged dictionaries", () => {
  const { controller, session } = fixture({ spellcheckLanguages: ["fr", "en-US"] });
  let calls = 0;
  session.setSpellCheckerLanguages = (languages) => {
    calls++;
    session.languages = languages;
  };
  controller.apply();
  controller.apply();
  controller.apply({ spellcheckLanguages: ["en-US", "fr"] });
  assert.equal(calls, 1);
});
