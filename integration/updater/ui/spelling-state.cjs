// Keep the last successful snapshot across tab changes. Revalidation never
// blanks the list, and a late read cannot overwrite a newer settings change.
function createSpellingState(native) {
  let snapshot = { value: undefined, busy: false, refreshing: false, error: "", message: "" };
  let reading, writing, manualRead;
  let revision = 0;
  const listeners = new Set();
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
            value,
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
    update({
      value: {
        ...previous,
        enabled,
        useSystemLanguages,
        configuredLanguages: languages,
        languages: useSystemLanguages ? previous.languages : languages,
      },
      busy: true,
      error: "",
      message: "",
    });
    writing = Promise.resolve()
      .then(() => native.spellcheckSave({ enabled, languages, useSystemLanguages }))
      .then((value) => update({ value, message: "Spelling settings saved." }))
      .catch((error) => update({ value: previous, error: message(error) }))
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
  };
}
module.exports = { createSpellingState };
