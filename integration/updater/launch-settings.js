(() => {
  const $ = (id) => document.getElementById(id);
  let data,
    config,
    saved,
    dirty = false,
    saving = false,
    fileError = false,
    generation = 0,
    previewTimer;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const active = () => config.profiles[config.active];
  const error = (message = "") => {
    $("error").textContent = message;
    $("error").hidden = !message;
  };
  function validation(result, focus = false) {
    for (const id of ["preset", "preset-flags", "extra-flags"]) {
      $(id).removeAttribute("aria-invalid");
      const description = $(id).getAttribute("aria-describedby") || "";
      $(id).setAttribute("aria-describedby", description.replace(/ ?error/g, ""));
    }
    error(result.error || "");
    if (!result.error) return;
    const id = result.preset && result.preset !== config.active ? "preset" : result.field;
    const target = $(id) || $("error");
    if (id) {
      target.setAttribute("aria-invalid", "true");
      target.setAttribute(
        "aria-describedby",
        (target.getAttribute("aria-describedby") || "") + " error",
      );
    } else target.tabIndex = -1;
    if (focus) target.focus();
  }
  function markDirty() {
    dirty = JSON.stringify(config) !== saved;
    $("status").textContent = dirty
      ? "Unsaved changes. Save, then restart to apply."
      : "No unsaved changes.";
    $("restart").disabled = dirty || fileError;
    $("discard").disabled = !dirty;
    clearTimeout(previewTimer);
    // Invalidate pending results immediately, before the next debounce fires.
    generation++;
    previewTimer = setTimeout(preview, 180);
  }
  function option(parent, id, item, checked, change) {
    const wrapper = document.createElement("div");
    wrapper.className = "option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id;
    input.name = id;
    input.checked = checked;
    input.setAttribute("aria-describedby", id + "-help");
    input.addEventListener("change", () => {
      change(input.checked);
      markDirty();
      updateCount();
    });
    const copy = document.createElement("div");
    const label = document.createElement("label");
    label.htmlFor = id;
    label.textContent = item.label;
    const help = document.createElement("p");
    help.className = "hint";
    help.id = id + "-help";
    help.textContent = item.description;
    const flags = document.createElement("code");
    flags.textContent = item.flags.join("\n");
    copy.append(label, help, flags);
    wrapper.append(input, copy);
    parent.append(wrapper);
  }
  function updateCount() {
    $("experiment-count").textContent = `(${active().experiments.length} enabled)`;
  }
  function renderPreset() {
    const selected = active();
    $("preset").value = config.active;
    $("preset-description").textContent = data.catalog.presets[config.active].description;
    $("preset-flags").value = selected.flags;
    $("preset-flags").placeholder = "No extra flags. Chromium's defaults are used.";
    $("backend").value = selected.backend;
    $("video-options").replaceChildren();
    $("experimental-options").replaceChildren();
    for (const [key, item] of Object.entries(data.catalog.video))
      option($("video-options"), key, item, selected[key], (checked) => {
        selected[key] = checked;
      });
    for (const [key, item] of Object.entries(data.catalog.experiments))
      option(
        $("experimental-options"),
        key,
        item,
        selected.experiments.includes(key),
        (checked) => {
          selected.experiments = selected.experiments.filter((name) => name !== key);
          if (checked) selected.experiments.push(key);
        },
      );
    updateCount();
  }
  function renderPreview(result) {
    fileError = Boolean(result.fileErrors?.length);
    if (fileError) result = { ...result, error: result.fileErrors.join("\n") };
    $("restart").disabled = dirty || fileError;
    validation(result);
    if (result.error) {
      $("effective").value = "Resolve the error above to preview this draft.";
      return false;
    }
    $("effective").value = result.normalEffective.join("\n") || "(No launch flags)";
    $("layers").replaceChildren();
    for (const layer of result.layers) {
      if (!layer.flags.length) continue;
      const section = document.createElement("div");
      section.className = "layer";
      const heading = document.createElement("h3");
      heading.textContent = layer.name;
      const flags = document.createElement("code");
      flags.textContent = layer.flags.join("\n");
      section.append(heading, flags);
      $("layers").append(section);
    }
    return true;
  }
  async function preview() {
    const ticket = ++generation;
    try {
      const result = await window.LaunchSettings.preview(clone(config));
      if (ticket === generation) renderPreview(result);
    } catch (failure) {
      if (ticket === generation) error(failure.message);
    }
  }
  async function load() {
    try {
      if (!window.LaunchSettings)
        throw new Error("Open this editor from Discord's OpenAsar settings.");
      data = await window.LaunchSettings.load();
      if (data.error) throw new Error(data.error);
      config = clone(data.config);
      saved = JSON.stringify(config);
      dirty = false;
      $("preset").replaceChildren();
      $("backend").replaceChildren();
      for (const [key, item] of Object.entries(data.catalog.presets))
        $("preset").add(new Option(item.label, key));
      for (const [key, item] of Object.entries(data.catalog.backends))
        $("backend").add(new Option(item.label, key));
      $("display").value = config.display;
      $("extra-flags").value = config.extra;
      $("warnings").textContent = data.warnings.join("\n");
      $("warnings").hidden = !data.warnings.length;
      $("recovery").hidden = !data.recovery;
      $("restart").textContent = data.recovery ? "Restart with saved flags" : "Restart Discord";
      $("restart").disabled = false;
      $("discard").disabled = true;
      $("settings").hidden = false;
      $("loading").hidden = true;
      renderPreset();
      renderPreview(data);
      $("status").textContent = "Changes apply after restarting Discord.";
    } catch (failure) {
      $("loading").hidden = true;
      error(failure.message);
    }
  }
  $("preset").addEventListener("change", () => {
    config.active = $("preset").value;
    renderPreset();
    markDirty();
  });
  $("display").addEventListener("change", () => {
    config.display = $("display").value;
    markDirty();
  });
  $("backend").addEventListener("change", () => {
    active().backend = $("backend").value;
    markDirty();
  });
  $("preset-flags").addEventListener("input", () => {
    active().flags = $("preset-flags").value;
    markDirty();
  });
  $("extra-flags").addEventListener("input", () => {
    config.extra = $("extra-flags").value;
    markDirty();
  });
  $("reset-preset").addEventListener("click", () => {
    config.profiles[config.active] = clone(data.defaults.profiles[config.active]);
    renderPreset();
    markDirty();
  });
  $("discard").addEventListener("click", () => {
    clearTimeout(previewTimer);
    generation++;
    void load();
  });
  $("settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving) return;
    saving = true;
    clearTimeout(previewTimer);
    const ticket = ++generation;
    const submitted = clone(config);
    $("save").setAttribute("aria-disabled", "true");
    $("save").textContent = "Saving…";
    try {
      const result = await window.LaunchSettings.save(submitted);
      if (result.error) {
        validation(result, true);
        return;
      }
      saved = JSON.stringify(submitted);
      dirty = JSON.stringify(config) !== saved;
      if (ticket === generation) renderPreview(result);
      $("status").textContent = dirty
        ? "Saved. You have additional unsaved changes."
        : "Saved. Restart Discord to apply.";
      $("restart").disabled = dirty || fileError;
      $("discard").disabled = !dirty;
    } catch (failure) {
      error(failure.message);
    } finally {
      saving = false;
      $("save").removeAttribute("aria-disabled");
      $("save").textContent = "Save changes";
    }
  });
  $("restart").addEventListener("click", async () => {
    if (dirty || fileError) return;
    try {
      const result = await window.LaunchSettings.restart();
      if (result?.error) error(result.error);
    } catch (failure) {
      error(failure.message);
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  void load();
})();
