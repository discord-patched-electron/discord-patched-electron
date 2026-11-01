// Pure transformations shared by the single flags box and its controls.
((root) => {
  const featureKey = (value) => value.trim().replace(/^\*/, "").split(/[:<]/, 1)[0];
  const parts = (line) => {
    const at = line.indexOf("=");
    return at < 0 ? [line, ""] : [line.slice(0, at), line.slice(at + 1)];
  };
  const features = (name) => ["--enable-features", "--disable-features"].includes(name);
  const settingKey = (value) => "blink-setting:" + parts(value)[0];
  const lines = (text) =>
    text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("--"));
  function state(text) {
    const result = new Map();
    for (const line of lines(text)) {
      const [name, value] = parts(line);
      if (features(name)) {
        for (const feature of value.split(","))
          result.set("feature:" + featureKey(feature), name + "=" + feature);
      } else if (name === "--blink-settings") {
        for (const setting of value.split(","))
          if (setting) result.set(settingKey(setting), name + "=" + setting);
      } else result.set(name, line);
    }
    return result;
  }
  function has(text, flags) {
    const current = state(text);
    return [...state(flags.join("\n"))].every(([key, value]) => current.get(key) === value);
  }
  function middleClickAutoscrollEnabled(text) {
    const current = state(text);
    const feature = current.get("feature:MiddleClickAutoscroll");
    let enabled =
      feature && !parts(feature)[1].trim().startsWith("*")
        ? feature.startsWith("--enable-features=")
        : current.has("--enable-blink-test-features");
    // Match Chromium's explicit Blink precedence over the base feature list.
    for (const name of ["--enable-blink-features", "--disable-blink-features"])
      if (
        parts(current.get(name) || "")[1]
          .split(",")
          .some((feature) => feature.trim() === "MiddleClickAutoscroll")
      )
        enabled = name === "--enable-blink-features";
    return enabled;
  }
  function selected(text, item) {
    if (item.flags.includes("--enable-features=MiddleClickAutoscroll"))
      return middleClickAutoscrollEnabled(text);
    if (
      item.flags.includes("--blink-settings=middleClickPasteAllowed=false") &&
      middleClickAutoscrollEnabled(text)
    )
      return true;
    return has(text, item.flags) && !(item.blockedBy || []).some((flag) => has(text, [flag]));
  }
  function replace(text, remove, add) {
    const keys = new Set(state([...remove, ...add].join("\n")).keys());
    const autoscrollEdit = keys.has("feature:MiddleClickAutoscroll");
    if (
      autoscrollEdit &&
      !state(add.join("\n")).has("feature:MiddleClickAutoscroll") &&
      state(text).has("--enable-blink-test-features")
    )
      add = [...add, "--disable-features=MiddleClickAutoscroll"];
    for (const flag of add) {
      const [name] = parts(flag);
      if (!features(name) && /^--(enable|disable)-/.test(name))
        keys.add(
          name.replace(/^--(enable|disable)-/, (_, value) =>
            value === "enable" ? "--disable-" : "--enable-",
          ),
        );
    }
    const result = [];
    for (const line of text.split("\n")) {
      const [name, value] = parts(line.trim());
      if (features(name)) {
        const kept = value.split(",").filter((item) => !keys.has("feature:" + featureKey(item)));
        if (kept.length) result.push(name + "=" + kept.join(","));
      } else if (
        autoscrollEdit &&
        ["--enable-blink-features", "--disable-blink-features"].includes(name)
      ) {
        const kept = value.split(",").filter((item) => item.trim() !== "MiddleClickAutoscroll");
        if (kept.length) result.push(name + "=" + kept.join(","));
      } else if (name === "--blink-settings") {
        const kept = value.split(",").filter((item) => !keys.has(settingKey(item)));
        if (kept.length) result.push(name + "=" + kept.join(","));
      } else if (!keys.has(name)) result.push(line);
    }
    return [...result.filter((line) => line.trim()), ...add].join("\n");
  }
  function edit(config, text, preservedExtra = config.extra) {
    // The box owns user overrides. Absorb outside-file overrides into
    // each preset before clearing them, preserving inactive presets' behavior.
    for (const [name, profile] of Object.entries(config.profiles)) {
      if (name !== config.active && config.extra)
        profile.flags = [profile.flags, preservedExtra].filter(Boolean).join("\n");
    }
    config.extra = "";
    Object.assign(config.profiles[config.active], {
      flags: text,
      decode: false,
      encode: false,
      backend: "auto",
      experiments: [],
    });
    delete config.profiles[config.active].replaceDefaults;
  }
  function configure(config, catalog, remove, add, preservedExtra = config.extra) {
    const profile = config.profiles[config.active];
    const options = [
      ...catalog.backends[profile.backend].flags,
      ...["decode", "encode"].flatMap((key) => (profile[key] ? catalog.video[key].flags : [])),
      ...profile.experiments.flatMap((key) => catalog.experiments[key].flags),
      profile.flags,
      preservedExtra,
    ];
    // Convenience controls edit only optional flags. Automatic display and
    // codec paths always keep following the session/profile.
    edit(config, replace(options.filter(Boolean).join("\n"), remove, add), preservedExtra);
    delete profile.replaceDefaults;
  }
  const api = { has, selected, replace, edit, configure, state, middleClickAutoscrollEnabled };
  if (typeof module !== "undefined") module.exports = api;
  else root.FlagEditor = api;
})(globalThis);
