const assert = require("node:assert/strict");
const { test } = require("node:test");
const { createSpellingState } = require("../../integration/updater/ui/spelling-state.cjs");

const initial = {
  enabled: true,
  useSystemLanguages: false,
  configuredLanguages: ["en-US"],
  languages: ["en-US"],
  availableLanguages: ["en-US", "fr"],
  downloads: [],
};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("tab remounts reuse the cached list while a deduplicated refresh is pending", async () => {
  let response = Promise.resolve(initial),
    calls = 0;
  const store = createSpellingState({
    spellcheckLoad: () => {
      calls++;
      return response;
    },
  });
  await store.load();
  const pending = deferred();
  response = pending.promise;
  const refresh = store.load();
  assert.equal(store.load(), refresh);
  let restored;
  const unsubscribe = store.subscribe((snapshot) => {
    restored = snapshot;
  });
  assert.equal(restored.value, initial, "remounted tabs receive data before the read resolves");
  unsubscribe();
  pending.reject(Error("Offline"));
  await refresh;
  assert.equal(calls, 2);
  assert.equal(store.getSnapshot().value, initial);
  assert.equal(store.getSnapshot().error, "Offline");
  response = Promise.resolve({ ...initial, downloads: [{ language: "en-US", state: "cached" }] });
  await store.load(true);
  assert.equal(store.getSnapshot().error, "");
  assert.equal(store.getSnapshot().value.downloads.length, 1);
});

test("late reads cannot overwrite saves, including saves that finish after leaving the tab", async () => {
  let response = Promise.resolve(initial);
  const saved = deferred();
  const store = createSpellingState({
    spellcheckLoad: () => response,
    spellcheckSave: () => saved.promise,
  });
  await store.load();
  const stale = deferred();
  response = stale.promise;
  const reading = store.load();
  const writing = store.save({ enabled: false, languages: [], useSystemLanguages: false });
  assert.equal(store.getSnapshot().value.enabled, false);
  assert.deepEqual(store.getSnapshot().value.languages, []);
  stale.resolve(initial);
  await reading;
  assert.equal(store.getSnapshot().value.enabled, false);
  const changed = { ...initial, enabled: false, configuredLanguages: [], languages: [] };
  saved.resolve(changed);
  await writing;
  assert.equal(store.getSnapshot().value, changed);
  assert.equal(store.getSnapshot().busy, false);
});

test("failed autosave rolls back to the cached settings and reports the error", async () => {
  const store = createSpellingState({
    spellcheckLoad: async () => initial,
    spellcheckSave: async () => {
      throw Error("Disk full");
    },
  });
  await store.load();
  await store.save({ enabled: false, languages: [], useSystemLanguages: false });
  assert.equal(store.getSnapshot().value, initial);
  assert.equal(store.getSnapshot().busy, false);
  assert.equal(store.getSnapshot().error, "Disk full");
});

test("manual refresh during a background read forces one follow-up refresh", async () => {
  const background = deferred();
  const calls = [];
  const store = createSpellingState({
    spellcheckLoad: (force) => {
      calls.push(force);
      return force ? initial : background.promise;
    },
  });
  const reading = store.load();
  const manual = store.load(true);
  assert.equal(store.load(true), manual);
  assert.equal(store.getSnapshot().refreshing, true);
  background.resolve(initial);
  await Promise.all([reading, manual]);
  assert.deepEqual(calls, [false, true]);
  assert.equal(store.getSnapshot().refreshing, false);
  assert.equal(store.getSnapshot().message, "Dictionary status refreshed.");
});
