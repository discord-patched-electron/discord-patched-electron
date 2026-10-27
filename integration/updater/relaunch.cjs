// Re-resolve saved preferences on every launch. Reusing process.argv would feed
// yesterday's effective flags back as higher-priority command-line overrides.
function originalArguments(env = process.env) {
  try {
    const args = JSON.parse(env.DPE_LAUNCH_ARGS || "[]");
    return Array.isArray(args) && args.every((arg) => typeof arg === "string") ? args : [];
  } catch {
    return [];
  }
}

function restart(app, { normal = false } = {}) {
  let args = originalArguments();
  if (normal) {
    delete process.env.DPE_IGNORE_FLAGS;
    args = args.filter((arg) => arg !== "--ignore-flags");
  }
  app.relaunch({ execPath: process.env.DPE_LAUNCHER, args });
  app.quit();
}

module.exports = { arguments: originalArguments, restart };
