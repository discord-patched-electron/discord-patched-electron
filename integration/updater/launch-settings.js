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
  function error(message = "") {
    $("error").textContent = message;
    $("error").hidden = !message;
  }
  function actionState() {
    $("b_restart").disabled = dirty || fileError || pending || saving;
    $("b_restart").title = dirty ? "Save flags before restarting" : "";
    $("save").disabled = !dirty || loading || pending || saving;
  }
  function essentialState() {
    $("essential-flags").disabled = pending || !$("edit-essential").checked;
  }
  function validation(result, focus = false) {
    for (const id of ["flags", "essential-flags"]) {
      $(id).removeAttribute("aria-invalid");
      $(id).setAttribute("aria-describedby", id === "flags" ? "flags-help" : "essential-help");
    }
    error(result.error || "");
    if (!result.error) return;
    const target = result.field === "essential-flags" ? $("essential-flags") : $("flags");
    if (target.id === "essential-flags") {
      $("edit-essential").checked = true;
      essentialState();
    }
    target.setAttribute("aria-invalid", "true");
    target.setAttribute(
      "aria-describedby",
      target.id === "flags" ? "flags-help error" : "essential-help error",
    );
    if (focus) target.focus();
  }
  function controls() {
    const text = $("flags").value;
    const selected = editor.state(text);
    const display = selected.get("--ozone-platform")?.split("=")[1];
    $("display").value = display || config.display;
    $("backend").value =
      Object.entries(data.catalog.backends).find(
        ([key, item]) => key !== "auto" && editor.has(text, item.flags),
      )?.[0] || "auto";
    for (const [key, item] of Object.entries({
      ...data.catalog.video,
      ...data.catalog.experiments,
    }))
      $(key).checked = editor.selected(text, item);
  }
  function enableControls(enabled) {
    pending = !enabled;
    for (const input of $("settings").querySelectorAll("input, textarea, select"))
      input.disabled = !enabled;
    essentialState();
    actionState();
  }
  function markDirty(rewrite = false) {
    dirty = JSON.stringify(config) !== saved;
    $("status").textContent = dirty ? "Unsaved changes." : "";
    clearTimeout(previewTimer);
    generation++;
    actionState();
    previewTimer = setTimeout(() => preview(rewrite), 180);
  }
  function edit(text) {
    editor.edit(config, text, data.preservedExtra);
    $("flags").value = text;
    controls();
    markDirty();
  }
  function configure(remove, add) {
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
    if (result.fileErrors?.length)
      result = { ...result, error: result.fileErrors.join("\n"), field: result.fileErrorField };
    if (result.fileRevision && result.fileRevision !== fileRevision)
      result = { ...result, error: "Flags changed on disk. Reload before saving." };
    fileError = Boolean(result.error);
    validation(result);
    // Malformed files still expose their literal text for repair.
    if (rewrite && result.editableFlags !== undefined) {
      $("flags").value = result.editableFlags;
      $("essential-flags").value = config.profiles[config.active].essentialFlags;
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
    icon.classList.toggle("danger", Boolean(item.knownCrash));
    icon.setAttribute("role", "img");
    icon.setAttribute("aria-label", item.knownCrash ? "Known to crash!" : "May break Discord");
    icon.title = item.knownCrash
      ? "Reproduced in local runtime tests; other GPUs may differ."
      : "May break Discord";
    icon.innerHTML = warningIcon;
    label.append(icon, item.label);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.id = key + "-help";
    hint.textContent = item.description;
    if (key === "nvidiaVaapi") {
      const link = document.createElement("a");
      link.id = "nvidia-vaapi-project";
      link.href = "https://github.com/elFarto/nvidia-vaapi-driver";
      link.textContent = "nvidia-vaapi-driver";
      link.addEventListener("click", (event) => {
        event.preventDefault();
        void window.openConfigLink("nvidia");
      });
      hint.append(" Requires ", link, ".");
    }
    if (item.knownCrash) {
      const warning = document.createElement("strong");
      warning.className = "crash-warning";
      warning.textContent = "Known to crash!";
      hint.append(" ", warning);
    }
    const input = document.createElement("input");
    input.id = key;
    input.name = key;
    input.type = "checkbox";
    input.className = "flag-switch";
    input.setAttribute("aria-describedby", hint.id);
    input.addEventListener("change", () =>
      configure(
        [...item.flags, ...(input.checked ? item.removes || [] : [])],
        input.checked ? item.flags : [],
      ),
    );
    copy.append(label, hint);
    row.append(copy, input);
    $("advanced-options").append(row);
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
      // Use the same draft shape for load and edit, so reverting an edit is
      // unchanged even when older files store options or global overrides.
      editor.edit(config, data.editableFlags, data.preservedExtra);
      fileRevision = data.fileRevision;
      saved = JSON.stringify(config);
      dirty = false;
      $("backend").replaceChildren();
      for (const [key, item] of Object.entries(data.catalog.backends))
        $("backend").add(new Option(item.label, key));
      $("advanced-options").replaceChildren();
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
      actionState();
    } finally {
      loading = false;
      $("settings").inert = false;
      actionState();
    }
  }
  $("display").addEventListener("change", () => {
    const display = $("display").value;
    config.display = display;
    // Automatic returns to dynamic session detection.
    configure(["--ozone-platform"], []);
  });
  $("backend").addEventListener("change", () => {
    const all = Object.values(data.catalog.backends).flatMap((item) => item.flags);
    configure(all, data.catalog.backends[$("backend").value].flags);
  });
  $("flags").addEventListener("input", () => edit($("flags").value));
  $("edit-essential").addEventListener("change", essentialState);
  $("essential-flags").addEventListener("input", () => {
    config.profiles[config.active].essentialFlags = $("essential-flags").value;
    markDirty();
  });
  $("reset-flags").addEventListener("click", () => {
    if (saving || loading) return;
    editor.edit(config, "", data.preservedExtra);
    config.profiles[config.active] = clone(data.defaults.profiles[config.active]);
    config.display = data.defaults.display;
    enableControls(false);
    markDirty(true);
  });
  $("discard").addEventListener("click", () => void load());
  $("settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!dirty || saving || loading || pending) return;
    saving = true;
    clearTimeout(previewTimer);
    const ticket = ++generation;
    const submitted = clone(config);
    $("save").textContent = "Saving…";
    $("discard").disabled = true;
    actionState();
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
      $("save").textContent = "Save";
      $("discard").disabled = false;
      actionState();
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
