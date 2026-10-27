// Keep OpenAsar's presets while letting explicit launcher flags take precedence.
module.exports = function append(commandLine, name, value) {
  // Both launcher stages already received the complete resolved argument list.
  // Recovery also suppresses the old OpenAsar module if launched independently.
  if (process.env.DPE_FLAGS_MANAGED === "1" || process.env.DPE_IGNORE_FLAGS === "1") return;
  // Presets must not bypass Chromium's driver blocklist. Deliberate launcher
  // overrides remain untouched, including when troubleshooting a blocked GPU.
  if (name === "ignore-gpu-blocklist") return;
  if (name === "enable-features" || name === "disable-features") {
    const key = (feature) => feature.split(/[:<]/, 1)[0];
    const explicit = new Set(
      ["enable-features", "disable-features"].flatMap((switchName) =>
        commandLine.getSwitchValue(switchName).split(",").map(key),
      ),
    );
    const combined = commandLine.getSwitchValue(name).split(",").filter(Boolean);
    combined.push(
      ...value.split(",").filter((feature) => {
        // OpenAsar's default Performance preset enables DrDc, which Chromium
        // explicitly does not support on Linux and which crashes the GPU.
        if (name === "enable-features" && key(feature) === "EnableDrDc") return false;
        return !explicit.has(key(feature));
      }),
    );
    if (combined.length) commandLine.appendSwitch(name, combined.join(","));
  } else if (!commandLine.hasSwitch(name)) commandLine.appendSwitch(name, value);
};
