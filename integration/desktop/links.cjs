// Keep only bounded Discord URLs; never pass flags or other protocols to the client.
function link(value) {
  if (typeof value !== "string" || value.length > 8192 || !/^discord:\/\/.+/i.test(value)) return;
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code <= 32 || code === 127) return;
  }
  try {
    if (new URL(value).protocol === "discord:") return value.replace(/^discord:/i, "discord:");
  } catch {
    /* Invalid external input is ignored. */
  }
}

function createQueue(argv = []) {
  const pending = [];
  let ready = false;
  function receive(args) {
    if (!Array.isArray(args)) return;
    for (const arg of args) {
      const url = link(arg);
      if (url && pending.length < 16) pending.push(url);
    }
  }
  function dispatch(core) {
    if (!ready || typeof core?.handleOpenUrl !== "function") return;
    for (const url of pending.splice(0)) {
      try {
        core.handleOpenUrl(url);
      } catch {
        console.warn("Could not open a Discord link.");
      }
    }
  }
  receive(argv);
  return {
    receive,
    dispatch,
    start(core) {
      ready = true;
      dispatch(core);
    },
    relaunchArguments(args) {
      // Delivered cold-launch URLs must not reopen after every update/restart.
      // Unconsumed Welcome/early-startup URLs must survive the launcher handoff.
      if (!ready) {
        const missing = args.map(link).filter((url) => url && !pending.includes(url));
        receive(missing);
      }
      return args
        .filter(
          (arg, index) =>
            !link(arg) && arg !== "--url" && !(arg === "--" && args[index - 1] === "--url"),
        )
        .concat(pending);
    },
  };
}
module.exports = { ...createQueue(process.argv), createQueue };
