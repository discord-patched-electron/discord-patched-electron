(() => {
  const $ = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const editor = window.FlagEditor;
  const warningIcon =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M5 5.782V2.5h-.25a.75.75 0 0 1 0-1.5h6.5a.75.75 0 0 1 0 1.5H11v3.282l3.666 5.76C15.619 13.04 14.543 15 12.767 15H3.233c-1.776 0-2.852-1.96-1.899-3.458L5 5.782zM9.5 2.5h-3V6a.75.75 0 0 1-.117.403L4.73 9h6.54L9.617 6.403A.75.75 0 0 1 9.5 6V2.5zm-6.9 9.847L3.775 10.5h8.45l1.175 1.847a.75.75 0 0 1-.633 1.153H3.233a.75.75 0 0 1-.633-1.153z"/></svg>';
  let data, config, saved, fileRevision;
  let dirty = false,
    saving = false,
    loading = false,
    fileError = false,
    pending = false;
  let generation = 0,
    previewTimer;
  const active = () => config.profiles[config.active];
  function error(message = "") {
    $("error").textContent = message;
    $("error").hidden = !message;
  }
  function restartState() {
    $("b_restart").disabled = dirty || fileError || pending || saving;
    $("b_restart").title = dirty ? "Save flags before restarting" : "";
  }
  function validation(result, focus = false) {
    for (const id of ["preset", "flags"]) {
      $(id).removeAttribute("aria-invalid");
      $(id).setAttribute("aria-describedby", id === "flags" ? "flags-help" : "");
    }
    error(result.error || "");
    if (!result.error) return;
    const target = result.preset && result.preset !== config.active ? $("preset") : $("flags");
    target.setAttribute("aria-invalid", "true");
    target.setAttribute("aria-describedby", target.id === "flags" ? "flags-help error" : "error");
    if (focus) target.focus();
  }
  function controls() {
    const text = $("flags").value;
    const selected = editor.state(text);
    const display = selected.get("--ozone-platform")?.split("=")[1];
    $("display").value = active().replaceDefaults ? display || "auto" : config.display;
    $("backend").value =
      Object.entries(data.catalog.backends).find(
        ([key, item]) => key !== "auto" && editor.has(text, item.flags),
      )?.[0] || "auto";
    for (const [key, item] of Object.entries({
      ...data.catalog.video,
      ...data.catalog.experiments,
    }))
      $(key).checked = editor.has(text, item.flags);
  }
  function enableControls(enabled) {
    pending = !enabled;
    for (const input of $("settings").querySelectorAll("input, textarea, select"))
      if (input.id !== "preset") input.disabled = !enabled;
    restartState();
  }
  function markDirty(rewrite = false) {
    dirty = JSON.stringify(config) !== saved;
    $("status").textContent = dirty ? "Unsaved changes." : "";
    clearTimeout(previewTimer);
    generation++;
    restartState();
    previewTimer = setTimeout(() => preview(rewrite), 180);
  }
  function edit(text) {
    editor.edit(config, text, data.preservedExtra);
    $("flags").value = text;
    controls();
    markDirty();
  }
  function configure(remove, add) {
    if (active().replaceDefaults) return edit(editor.replace($("flags").value, remove, add));
    editor.configure(config, data.catalog, remove, add, config.extra ? data.preservedExtra : "");
    enableControls(false);
    markDirty(true);
  }
  function warnings(messages) {
    // Details about retired switches belong in contributor notes, not this tab.
    const relevant = messages.filter(
      (message) => !message.includes("Removed a") && !message.includes("Removed an"),
    );
    $("warnings").textContent = relevant.join("\n");
    $("warnings").hidden = !relevant.length;
  }
  function renderPreview(result, rewrite = true) {
    if (result.fileErrors?.length) result = { ...result, error: result.fileErrors.join("\n") };
    if (result.fileRevision && result.fileRevision !== fileRevision)
      result = { ...result, error: "Flags changed on disk. Reload before saving." };
    fileError = Boolean(result.error);
    validation(result);
    // Malformed files still expose their literal text for repair.
    if (rewrite && result.editableFlags !== undefined) {
      $("flags").value = result.editableFlags;
      if (result.originalArgs?.length)
        $("flags").value +=
          "\n# Command line (this session):\n" +
          result.originalArgs.map((arg) => "# " + arg).join("\n");
      if (document.activeElement !== $("flags")) {
        $("flags").setSelectionRange(0, 0);
        $("flags").scrollTop = 0;
      }
      controls();
    }
    enableControls(true);
  }
  async function preview(rewrite = false) {
    if (saving || loading) return;
    const ticket = ++generation;
    try {
      const result = await window.LaunchSettings.preview(clone(config));
      if (ticket === generation) renderPreview(result, rewrite);
    } catch (failure) {
      if (ticket === generation) {
        fileError = true;
        error(failure.message);
        enableControls(true);
      }
    }
  }
  function option(key, item) {
    const row = document.createElement("div");
    row.className = "flag-row";
    const copy = document.createElement("div");
    const label = document.createElement("label");
    label.htmlFor = key;
    const icon = document.createElement("span");
    icon.className = "risk-icon";
    icon.setAttribute("role", "img");
    icon.setAttribute("aria-label", "May break Discord");
    icon.title = "May break Discord";
    icon.innerHTML = warningIcon;
    label.append(icon, item.label);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.id = key + "-help";
    hint.textContent = item.description;
    const input = document.createElement("input");
    input.id = key;
    input.name = key;
    input.type = "checkbox";
    input.className = "flag-switch";
    input.setAttribute("aria-describedby", hint.id);
    input.addEventListener("change", () => configure(item.flags, input.checked ? item.flags : []));
    copy.append(label, hint);
    row.append(copy, input);
    $("breaking-options").append(row);
  }
  async function load() {
    if (loading || saving) return;
    loading = true;
    clearTimeout(previewTimer);
    generation++;
    $("settings").inert = true;
    try {
      data = await window.LaunchSettings.load();
      if (data.error) throw new Error(data.error);
      config = clone(data.config);
      fileRevision = data.fileRevision;
      saved = JSON.stringify(config);
      dirty = false;
      $("preset").replaceChildren();
      for (const [key, item] of Object.entries(data.catalog.presets)) {
        if (
          !item.legacy ||
          config.active === key ||
          JSON.stringify(config.profiles[key]) !== JSON.stringify(data.defaults.profiles[key])
        )
          $("preset").add(new Option(item.label, key));
      }
      $("preset").value = config.active;
      $("backend").replaceChildren();
      for (const [key, item] of Object.entries(data.catalog.backends))
        $("backend").add(new Option(item.label, key));
      $("breaking-options").replaceChildren();
      for (const [key, item] of Object.entries({
        ...data.catalog.video,
        ...data.catalog.experiments,
      }))
        option(key, item);
      document.querySelectorAll(".risk-icon").forEach((icon) => {
        icon.innerHTML = warningIcon;
      });
      $("flags").title = data.flagsFile;
      warnings(data.warnings);
      $("recovery").hidden = !data.recovery;
      $("settings").hidden = false;
      $("loading").hidden = true;
      $("status").textContent = "";
      renderPreview(data);
    } catch (failure) {
      fileError = true;
      $("loading").hidden = true;
      error(failure.message);
      restartState();
    } finally {
      loading = false;
      $("settings").inert = false;
    }
  }
  $("preset").addEventListener("change", () => {
    config.active = $("preset").value;
    enableControls(false);
    markDirty(true);
  });
  $("display").addEventListener("change", () => {
    const display = $("display").value;
    config.display = display;
    // Automatic returns this preset to dynamic session detection.
    configure(
      ["--ozone-platform"],
      active().replaceDefaults && display !== "auto" ? ["--ozone-platform=" + display] : [],
    );
  });
  $("backend").addEventListener("change", () => {
    const all = Object.values(data.catalog.backends).flatMap((item) => item.flags);
    configure(all, data.catalog.backends[$("backend").value].flags);
  });
  $("flags").addEventListener("input", () => edit($("flags").value));
  $("reset-preset").addEventListener("click", () => {
    if (saving || loading) return;
    config.profiles[config.active] = clone(data.defaults.profiles[config.active]);
    enableControls(false);
    markDirty(true);
  });
  $("discard").addEventListener("click", () => void load());
  $("settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving || loading || pending) return;
    saving = true;
    clearTimeout(previewTimer);
    const ticket = ++generation;
    const submitted = clone(config);
    $("save").setAttribute("aria-disabled", "true");
    $("save").textContent = "Saving…";
    $("discard").disabled = true;
    restartState();
    try {
      const result = await window.LaunchSettings.save(submitted, fileRevision);
      if (result.error) {
        fileError = true;
        validation(result, true);
        return;
      }
      fileRevision = result.fileRevision;
      saved = JSON.stringify(submitted);
      dirty = JSON.stringify(config) !== saved;
      warnings(result.warnings);
      if (ticket === generation) renderPreview(result);
      else previewTimer = setTimeout(() => preview(true), 0);
      $("status").textContent = dirty ? "Saved. More changes pending." : "Saved. Restart to apply.";
    } catch (failure) {
      fileError = true;
      error(failure.message);
    } finally {
      saving = false;
      $("save").removeAttribute("aria-disabled");
      $("save").textContent = "Save";
      $("discard").disabled = false;
      restartState();
    }
  });
  $("b_restart").onclick = async () => {
    if (dirty || fileError || pending || saving) return;
    try {
      const result = await window.LaunchSettings.restart();
      if (result?.error) error(result.error);
    } catch (failure) {
      error(failure.message);
    }
  };
  window.addEventListener("beforeunload", (event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  void load();
})();
