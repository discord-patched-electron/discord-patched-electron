// Keep OpenAsar's presets while letting explicit launcher flags take precedence.
module.exports = function append(commandLine, name, value) {
  if (name === "enable-features" || name === "disable-features") {
    const key = (feature) => feature.split(/[:<]/, 1)[0];
    const explicit = new Set(
      ["enable-features", "disable-features"].flatMap((switchName) =>
        commandLine.getSwitchValue(switchName).split(",").map(key),
      ),
    );
    const combined = commandLine.getSwitchValue(name).split(",").filter(Boolean);
    combined.push(...value.split(",").filter((feature) => !explicit.has(key(feature))));
    if (combined.length) commandLine.appendSwitch(name, combined.join(","));
  } else if (!commandLine.hasSwitch(name)) commandLine.appendSwitch(name, value);
};
