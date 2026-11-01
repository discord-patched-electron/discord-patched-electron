// Keep the last successful snapshot across tab changes. Revalidation never
// blanks the list, and a late read cannot overwrite a newer settings change.
function createSpellingState(native) {
  let snapshot = { value: undefined, busy: false, refreshing: false, error: "", message: "" };
  let reading, writing, manualRead;
  let revision = 0;
  const listeners = new Set();
  const requested = new Set();
  function withPendingDownloads(value) {
    if (!requested.size) return value;
    const downloads = new Map((value.downloads || []).map((item) => [item.language, item]));
    for (const language of requested) {
      if (
        !value.enabled ||
        !value.languages.includes(language) ||
        ["ready", "cached", "failed"].includes(downloads.get(language)?.state)
      ) {
        requested.delete(language);
      } else if (!downloads.has(language)) {
        downloads.set(language, { language, state: "pending" });
      }
    }
    return { ...value, downloads: [...downloads.values()] };
  }
  const update = (next) => {
    snapshot = { ...snapshot, ...next };
    for (const listener of listeners) listener(snapshot);
  };
  const message = (error) =>
    String(error?.message || error).replace(/^Error invoking remote method '[^']+': Error: /, "");
  function load(manual = false) {
    if (writing) return writing;
    if (reading) {
      if (!manual) return reading;
      // A user refresh must bypass the TTL even if a background read was first.
      if (!manualRead) {
        update({ refreshing: true });
        manualRead = reading.then(() => {
          manualRead = undefined;
          return load(true);
        });
      }
      return manualRead;
    }
    const version = revision;
    if (manual) update({ refreshing: true });
    reading = Promise.resolve()
      .then(() => native.spellcheckLoad(manual))
      .then((value) => {
        if (version === revision)
          update({
            value: withPendingDownloads(value),
            error: "",
            ...(manual ? { message: "Dictionary status refreshed." } : {}),
          });
      })
      .catch((error) => {
        if (version === revision) update({ error: message(error) });
      })
      .finally(() => {
        reading = undefined;
        update({ refreshing: false });
      });
    return reading;
  }
  function save({ enabled, languages, useSystemLanguages }) {
    if (writing) return writing;
    const previous = snapshot.value;
    revision++;
    const beforeRequest = new Set(requested);
    if (enabled && !useSystemLanguages) {
      for (const language of languages) {
        const known = previous.downloads?.find((item) => item.language === language)?.state;
        if (
          (!previous.languages.includes(language) || !previous.enabled) &&
          !["ready", "cached"].includes(known)
        )
          requested.add(language);
      }
    }
    update({
      value: withPendingDownloads({
        ...previous,
        enabled,
        useSystemLanguages,
        configuredLanguages: languages,
        downloads: (previous.downloads || []).filter(
          (item) =>
            !requested.has(item.language) ||
            beforeRequest.has(item.language) ||
            ["ready", "cached"].includes(item.state),
        ),
        languages: useSystemLanguages ? previous.languages : languages,
      }),
      busy: true,
      error: "",
      message: "",
    });
    writing = Promise.resolve()
      .then(() => native.spellcheckSave({ enabled, languages, useSystemLanguages }))
      .then((value) =>
        update({ value: withPendingDownloads(value), message: "Spelling settings saved." }),
      )
      .catch((error) => {
        requested.clear();
        for (const language of beforeRequest) requested.add(language);
        update({ value: previous, error: message(error) });
      })
      .finally(() => {
        writing = undefined;
        update({ busy: false });
      });
    return writing;
  }
  function uninstall(language) {
    if (writing) return writing;
    revision++;
    update({ busy: true, error: "", message: "" });
    writing = Promise.resolve()
      .then(() => native.spellcheckUninstall(language))
      .then((value) => update({ value }))
      .catch(async (error) => {
        // Preferences may already be saved if deleting the file failed.
        const text = message(error);
        try {
          update({ value: await native.spellcheckLoad(true) });
        } catch {
          /* Keep last known state. */
        }
        update({ error: text });
      })
      .finally(() => {
        writing = undefined;
        update({ busy: false });
      });
    return writing;
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      listener(snapshot);
      return () => listeners.delete(listener);
    },
    load,
    save,
    uninstall,
  };
}
module.exports = { createSpellingState };
