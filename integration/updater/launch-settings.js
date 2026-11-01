export function initializeFlags() {
  const $ = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const editor = window.FlagEditor;
  const ui = window.DPEControls;
  let data, config, saved, baseline, fileRevision;
  let dirty = false,
    saving = false,
    loading = false,
    fileError = false,
    pending = false;
  let generation = 0,
    previewTimer,
    pendingFocus;
  function error(message = "") {
    $("error").textContent = message;
    $("error").hidden = !message;
  }
  function actionState() {
    const busy = loading || saving || (dirty && !fileError);
    window.LaunchSettings.setDirty?.(dirty);
    window.LaunchSettings.setSaving?.(busy);
    const changed = baseline !== undefined && saved !== baseline;
    document.body.dataset.flagsChanged = String(changed);
    $("status").dataset.dirty = String(dirty);
    window.dispatchEvent(
      new CustomEvent("dpe-flags-state", { detail: { changed, busy, error: fileError } }),
    );
    $("b_restart").disabled =
      dirty || fileError || pending || saving || document.body.dataset.modBusy === "true";
    $("b_restart").title = dirty ? "Flags must finish saving before restarting" : "";
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
    const inputText = [$("essential-flags").value, text, ...(data.originalArgs || [])].join("\n");
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
      $(key).checked = editor.selected(
        ["middleClickAutoscroll", "disableMiddleClickPaste"].includes(key) ? inputText : text,
        item,
      );
    $("disableMiddleClickPaste").disabled =
      pending || editor.middleClickAutoscrollEnabled(inputText);
    ui.sync($("settings"));
  }
  function enableControls(enabled) {
    if (!enabled && $("settings").contains(document.activeElement))
      pendingFocus = document.activeElement;
    pending = !enabled;
    for (const input of $("settings").querySelectorAll("input, textarea, select"))
      input.disabled = !enabled;
    essentialState();
    if (enabled) controls();
    else ui.sync($("settings"));
    if (enabled && pendingFocus) {
      if (
        document.activeElement === document.body &&
        pendingFocus.isConnected &&
        !pendingFocus.disabled
      )
        pendingFocus.focus({ preventScroll: true });
      pendingFocus = undefined;
    }
    actionState();
  }
  function markDirty(rewrite = false) {
    dirty = JSON.stringify(config) !== saved;
    $("status").textContent = dirty ? "Saving changes…" : "";
    fileError = false;
    clearTimeout(previewTimer);
    generation++;
    actionState();
    previewTimer = setTimeout(() => (dirty ? save() : preview(rewrite)), 180);
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
    if (item.experimental !== false) {
      const icon = ui.experimental(Boolean(item.knownCrash));
      icon.title = item.knownCrash
        ? "Reproduced in local runtime tests. Other GPUs may differ."
        : "May break Discord";
      label.append(icon);
    }
    label.append(item.label);
    label.id = key + "-label";
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
    input.className = "dpe-switch";
    input.setAttribute("aria-labelledby", label.id);
    if (item.description) input.setAttribute("aria-describedby", hint.id);
    input.addEventListener("change", () =>
      configure(
        [...item.flags, ...(input.checked ? item.removes || [] : [])],
        input.checked ? item.flags : [],
      ),
    );
    copy.append(label);
    if (item.description) copy.append(hint);
    row.append(copy, ui.switchControl(input));
    $(item.group === "input" ? "input-options" : "advanced-options").append(row);
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
      baseline ??= saved;
      dirty = false;
      $("backend").replaceChildren();
      for (const [key, item] of Object.entries(data.catalog.backends))
        $("backend").add(new Option(item.label, key));
      $("advanced-options").replaceChildren();
      $("input-options").replaceChildren();
      for (const [key, item] of Object.entries({
        ...data.catalog.video,
        ...data.catalog.experiments,
      }))
        option(key, item);
      document.querySelectorAll(".risk-icon:not([data-dpe-icon])").forEach((slot) => {
        slot.dataset.dpeIcon = "experimental";
      });
      ui.updateIcons();
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
  $("edit-essential").addEventListener("change", () => {
    essentialState();
    if (!$("edit-essential").checked) {
      config.profiles[config.active].essentialFlags =
        data.defaults.profiles[config.active].essentialFlags;
      $("essential-flags").value = config.profiles[config.active].essentialFlags;
      controls();
      markDirty();
    }
  });
  $("essential-flags").addEventListener("input", () => {
    config.profiles[config.active].essentialFlags = $("essential-flags").value;
    controls();
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
  async function save(focus = false) {
    if (!dirty || saving || loading) return;
    saving = true;
    clearTimeout(previewTimer);
    const ticket = ++generation;
    const submitted = clone(config);
    $("discard").disabled = true;
    actionState();
    try {
      const result = await window.LaunchSettings.save(submitted, fileRevision);
      if (result.error) {
        if (ticket === generation) {
          fileError = true;
          validation(result, focus);
          $("status").textContent = "Changes were not saved. Fix the error to continue.";
          enableControls(true);
        }
        return;
      }
      fileRevision = result.fileRevision;
      saved = JSON.stringify(submitted);
      dirty = JSON.stringify(config) !== saved;
      warnings(result.warnings);
      if (ticket === generation)
        renderPreview(result, !["flags", "essential-flags"].includes(document.activeElement.id));
      $("status").textContent = dirty ? "Saving more changes…" : "";
    } catch (failure) {
      if (ticket === generation) {
        fileError = true;
        error(failure.message);
        $("status").textContent = "Changes were not saved. Edit again to retry, or reload.";
        enableControls(true);
      }
    } finally {
      saving = false;
      $("discard").disabled = false;
      actionState();
      if (ticket !== generation) {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(() => (dirty ? save() : preview(true)), 0);
      }
    }
  }
  $("settings").addEventListener("submit", (event) => {
    event.preventDefault();
    void save(true);
  });
  window.addEventListener("dpe-flags-undo", () => {
    if (!baseline || saving || loading) return;
    config = JSON.parse(baseline);
    enableControls(false);
    markDirty(true);
  });
  $("b_restart").onclick = async () => {
    if (dirty || fileError || pending || saving || document.body.dataset.modBusy === "true") return;
    try {
      const result = await window.LaunchSettings.restart();
      if (result?.error) error(result.error);
    } catch (failure) {
      error(failure.message);
    }
  };
  window.addEventListener("beforeunload", (event) => {
    if (dirty || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("dpe-mod-busy", actionState);
  ui.switchControl($("edit-essential"));
  void load();
}
