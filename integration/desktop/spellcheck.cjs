// Chromium owns dictionary downloads, caching, suggestions and personal words.
// Discord's existing spellcheck IPC and context menu remain the presentation layer.
const fs = require("node:fs/promises");
const path = require("node:path");

function usesSystemLanguages(value) {
  return typeof value.spellcheckUseSystemLanguages === "boolean"
    ? value.spellcheckUseSystemLanguages
    : !value.spellcheckLanguages?.length;
}

function dictionaryLocale(language) {
  if (!language.includes("-")) {
    try {
      return new Intl.Locale(language)
        .maximize()
        .baseName.replace(/-[A-Z][a-z]{3}(?=-)/, "")
        .toLowerCase();
    } catch {
      /* Unknown aliases stay unchanged. */
    }
  }
  return language.toLowerCase();
}
function dictionaryMatch(name, language) {
  const match = /^([a-z]{2,3}(?:-[a-z0-9]+)*)-\d+-\d+\.bdic$/i.exec(name);
  return Boolean(
    match &&
    (match[1].toLowerCase() === language.toLowerCase() ||
      match[1].toLowerCase() === dictionaryLocale(language)),
  );
}

async function cachedLanguages(directory, available) {
  const cached = new Set();
  if (!directory) return cached;
  let entries;
  try {
    entries = await fs.readdir(directory);
  } catch (error) {
    if (error.code === "ENOENT") return cached;
    throw error;
  }
  for (const name of entries) {
    // Chromium names BDic files after a locale followed by its numeric version.
    const match = /^([a-z]{2,3}(?:-[a-z0-9]+)*)-\d+-\d+\.bdic$/i.exec(name);
    if (!match) continue;
    let fd;
    try {
      fd = await fs.open(path.join(directory, name), "r");
      const header = Buffer.alloc(4);
      if (
        (await fd.stat()).size <= 32 ||
        (await fd.read(header, 0, 4, 0)).bytesRead !== 4 ||
        header.toString() !== "BDic"
      )
        continue;
      const locale = match[1].toLowerCase();
      for (const language of available) {
        // Unqualified catalog aliases (fr, de, en) share their default region's
        // dictionary. Never mark another region such as en-GB from en-US.
        let canonical = language;
        if (!language.includes("-")) {
          try {
            canonical = new Intl.Locale(language)
              .maximize()
              .baseName.replace(/-[A-Z][a-z]{3}(?=-)/, "");
          } catch {
            /* Leave unknown aliases unmatched. */
          }
        }
        if (language.toLowerCase() === locale || canonical.toLowerCase() === locale)
          cached.add(language);
      }
    } catch {
      /* A missing or unfinished download is not a cached dictionary. */
    } finally {
      if (fd !== undefined) await fd.close();
    }
  }
  return cached;
}

function createController(electron, getConfig, { scan = cachedLanguages, now = Date.now } = {}) {
  let config;
  let current;
  const downloads = new Map();
  const listeners = new Map();
  let cache;
  let error;
  const removed = new Set();
  function selected(session, value) {
    const available = session.availableSpellCheckerLanguages;
    const requested = Array.isArray(value.spellcheckLanguages) ? value.spellcheckLanguages : [];
    if (!usesSystemLanguages(value))
      return [...new Set(requested.filter((code) => available.includes(code)))];
    const preferred = electron.app.getPreferredSystemLanguages?.() || [electron.app.getLocale()];
    return [
      ...new Set(
        preferred
          .map((code) => {
            const exact = available.find((item) => item.toLowerCase() === code.toLowerCase());
            return exact || available.find((item) => item.split("-")[0] === code.split("-")[0]);
          })
          .filter(Boolean),
      ),
    ];
  }
  function session() {
    const next = electron.session.defaultSession;
    if (current !== next) {
      for (const [event, listener] of listeners) current?.removeListener(event, listener);
      listeners.clear();
      downloads.clear();
      removed.clear();
      current = next;
      cache = { languages: new Set(), expires: 0, revision: 0, pending: undefined };
      for (const [event, state] of [
        ["spellcheck-dictionary-download-begin", "downloading"],
        ["spellcheck-dictionary-initialized", "ready"],
        ["spellcheck-dictionary-download-success", "ready"],
        ["spellcheck-dictionary-download-failure", "failed"],
      ]) {
        const listener = (_event, language) => {
          if (removed.has(language)) return;
          downloads.set(language, state);
          cache.expires = 0;
          cache.revision++;
        };
        listeners.set(event, listener);
        next.on(event, listener);
      }
    }
    return next;
  }
  function refresh(force = false) {
    if (!electron.app.isReady()) return Promise.resolve();
    const active = session();
    const snapshot = cache;
    if (snapshot.pending) return snapshot.pending;
    if (!force && now() < snapshot.expires) return Promise.resolve();
    const revision = snapshot.revision;
    const directory = electron.app.getPath?.("userData");
    snapshot.pending = Promise.resolve()
      .then(() =>
        scan(
          directory && path.join(directory, "Dictionaries"),
          active.availableSpellCheckerLanguages,
        ),
      )
      .then((languages) => {
        snapshot.languages = languages;
        snapshot.error = undefined;
        snapshot.expires = revision === snapshot.revision ? now() + 30000 : 0;
      })
      .catch((cause) => {
        // A temporary read error must not erase the last known good list.
        snapshot.error = "Could not refresh downloaded dictionaries: " + cause.message;
        snapshot.expires = now() + 5000;
      })
      .finally(() => {
        snapshot.pending = undefined;
      });
    return snapshot.pending;
  }
  function status() {
    const value = config || getConfig();
    if (!electron.app.isReady())
      return {
        enabled: value.spellcheckEnabled !== false,
        languages: [],
        availableLanguages: [],
        downloads: [],
        error: "Spell checking is available after startup.",
      };
    const active = session();
    // Return cached metadata immediately while one asynchronous scan refreshes
    // it. Chromium alone downloads and validates the versioned dictionaries.
    void refresh();
    const states = new Map([...cache.languages].map((language) => [language, "cached"]));
    for (const [language, state] of downloads)
      if (state !== "pending" || !cache.languages.has(language)) states.set(language, state);
    return {
      enabled: active.spellCheckerEnabled,
      languages: active.getSpellCheckerLanguages(),
      availableLanguages: [...active.availableSpellCheckerLanguages],
      downloads: [...states].map(([language, state]) => ({ language, state })),
      ...(error ? { error } : {}),
      ...(cache.error ? { dictionaryError: cache.error } : {}),
    };
  }
  function apply(value = getConfig()) {
    config = value;
    if (!electron.app.isReady()) return status();
    const active = session();
    try {
      const languages = selected(active, value);
      for (const language of removed)
        if (languages.some((code) => dictionaryLocale(code) === dictionaryLocale(language)))
          removed.delete(language);
      // Preserve Chromium's default when no installed locale maps to its catalog.
      const previous = active.getSpellCheckerLanguages();
      if (
        (languages.length || !usesSystemLanguages(value)) &&
        (languages.length !== previous.length ||
          languages.some((language) => !previous.includes(language)))
      ) {
        for (const language of previous) {
          if (!languages.includes(language) && downloads.get(language) === "pending")
            downloads.delete(language);
        }
        for (const language of languages) {
          if (
            !previous.includes(language) &&
            !cache.languages.has(language) &&
            downloads.get(language) !== "ready"
          )
            downloads.set(language, "pending");
        }
        active.setSpellCheckerLanguages(languages);
      }
      active.spellCheckerEnabled =
        value.spellcheckEnabled !== false && (usesSystemLanguages(value) || languages.length > 0);
      error = undefined;
    } catch (cause) {
      for (const [language, state] of downloads)
        if (state === "pending") downloads.delete(language);
      error = "Could not apply spell checking languages: " + cause.message;
    }
    return status();
  }
  function aliases(language) {
    const active = session();
    if (typeof language !== "string" || !active.availableSpellCheckerLanguages.includes(language))
      throw new Error("Choose a supported spelling language.");
    return active.availableSpellCheckerLanguages.filter(
      (code) => dictionaryLocale(code) === dictionaryLocale(language),
    );
  }
  async function uninstall(language) {
    const affected = aliases(language);
    if (affected.some((code) => session().getSpellCheckerLanguages().includes(code)))
      throw new Error("Disable the dictionary before uninstalling it.");
    if (affected.some((code) => downloads.get(code) === "downloading"))
      throw new Error("Wait for the dictionary download to finish.");
    await cache.pending;
    const directory = path.join(electron.app.getPath("userData"), "Dictionaries");
    let names;
    try {
      names = await fs.readdir(directory);
    } catch (cause) {
      if (cause.code !== "ENOENT") throw cause;
      names = [];
    }
    for (const name of names)
      if (dictionaryMatch(name, language)) await fs.unlink(path.join(directory, name));
    for (const code of affected) {
      removed.add(code);
      downloads.delete(code);
      cache.languages.delete(code);
    }
    cache.expires = 0;
    cache.revision++;
    await refresh(true);
    return status();
  }
  return { apply, status, selected, refresh, aliases, uninstall };
}

function storedConfig() {
  return global.settings?.get
    ? global.settings.get("openasar", {})
    : require("../updater/profile.cjs").config();
}
let controller;
function getController() {
  if (!controller) {
    controller = createController(require("electron"), storedConfig);
    controller.apply();
  }
  return controller;
}
function install(electron, { getConfig } = {}) {
  controller ||= createController(electron, getConfig || storedConfig);
  void electron.app
    .whenReady()
    .then(() => controller.apply())
    .catch(console.error);
}
function setLanguages(_session, _requested) {
  // Discord may send its UI locale or detected language on each edit. Keep all
  // languages chosen in DPE active together instead of replacing the second one.
  const result = getController().apply();
  if (result.error) throw new Error(result.error);
}
function openLanguages(
  address,
  contents,
  open = (...args) => require("../updater/config.cjs").open(...args),
) {
  // Discord's Languages menu opens this help article instead of a language picker.
  // Resolve that native navigation locally, preserving its translated menu and
  // keyboard behavior without depending on private renderer component exports.
  if (!URL.canParse(address) || !URL.canParse(contents.getURL())) return false;
  const target = new URL(address);
  const source = new URL(contents.getURL());
  if (
    source.protocol !== "https:" ||
    !["discord.com", "canary.discord.com", "ptb.discord.com"].includes(source.hostname) ||
    target.protocol !== "https:" ||
    target.hostname !== "support.discord.com" ||
    !/^\/hc\/(?:[a-z]{2}(?:-[a-z0-9]+)*\/)?articles\/360000877191(?:-[^/]*)?\/?$/i.test(
      target.pathname,
    )
  )
    return false;
  open("spelling", contents);
  return true;
}
function patchDesktop(source, adapter, replace) {
  for (const argument of ["[locale]", "[...languages]"]) {
    source = replace(
      source,
      `electron.session.defaultSession.setSpellCheckerLanguages(${argument})`,
      `require(${JSON.stringify(adapter)}).setLanguages(electron.session.defaultSession,${argument})`,
    );
  }
  source = replace(
    source,
    "mainWebContents.setWindowOpenHandler(details=>{",
    `mainWebContents.setWindowOpenHandler(details=>{if(require(${JSON.stringify(adapter)}).openLanguages(details.url,mainWebContents))return{action:"deny"};`,
  );
  source = replace(
    source,
    'popoutWindow.webContents.setWindowOpenHandler(({url})=>((0,securityUtils_1.saferShellOpenExternal)(url),{action:"deny"}))',
    `popoutWindow.webContents.setWindowOpenHandler(({url})=>(require(${JSON.stringify(adapter)}).openLanguages(url,popoutWindow.webContents)||(0,securityUtils_1.saferShellOpenExternal)(url),{action:"deny"}))`,
  );
  return source;
}
module.exports = {
  createController,
  usesSystemLanguages,
  cachedLanguages,
  dictionaryMatch,
  install,
  setLanguages,
  openLanguages,
  patchDesktop,
  apply: (value) => getController().apply(value),
  status: () => getController().status(),
  refresh: (force) => getController().refresh(force),
  aliases: (language) => getController().aliases(language),
  uninstall: (language) => getController().uninstall(language),
};
