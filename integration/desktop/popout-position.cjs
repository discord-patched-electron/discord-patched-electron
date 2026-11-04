const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const execute = promisify(execFile);

// Wayland ignores app-set top-level x/y. Use niri's window ID only after
// correlating the newly focused child with our PID; never move "the focused
// window" later, since that could be the user's editor or another Discord.
function identify(window, run = execute, env = process.env) {
  if (!env.NIRI_SOCKET) return undefined;
  const query = async (kind = "windows") =>
    JSON.parse(
      (await run("niri", ["msg", "--json", kind], { timeout: 1500, maxBuffer: 1024 * 1024 }))
        .stdout,
    );
  const identity = query()
    .then((windows) => {
      if (window.isDestroyed() || !window.isFocused()) return undefined;
      const matches = windows.filter(
        (item) =>
          item.pid === process.pid &&
          item.is_focused &&
          item.is_floating &&
          item.title === window.getTitle(),
      );
      return matches.length === 1 ? matches[0] : undefined;
    })
    .catch(() => undefined);
  return {
    async display(screen) {
      const child = await identity;
      if (!child || window.isDestroyed()) return undefined;
      try {
        const [workspaces, outputs] = await Promise.all([query("workspaces"), query("outputs")]);
        const output = workspaces.find((item) => item.id === child.workspace_id)?.output;
        const logical = outputs[output]?.logical;
        if (
          !logical ||
          ![logical.width, logical.height, logical.scale].every(
            (value) => Number.isFinite(value) && value > 0,
          )
        )
          return undefined;
        // Wayland cannot report a top-level's global position. Resolve the
        // actual output before choosing its logical work area and scale.
        return { ...screen.getDisplayMatching(logical), scaleFactor: logical.scale };
      } catch {
        return undefined;
      }
    },
    async place(area) {
      const target = (await identity)?.id;
      if (!Number.isSafeInteger(target) || window.isDestroyed()) return;
      try {
        const current = (await query()).find(
          (item) => item.id === target && item.pid === process.pid,
        );
        if (!current?.is_floating || window.isDestroyed()) return;
        const size = current.layout?.tile_size;
        if (!size?.every((value) => Number.isFinite(value) && value > 0) || size.length !== 2)
          return;
        await run(
          "niri",
          [
            "msg",
            "action",
            "move-floating-window",
            "--id",
            String(target),
            "--x",
            String(Math.max(0, Math.round(area.width - size[0] - 16))),
            "--y",
            String(Math.max(0, Math.round(area.height - size[1] - 16))),
          ],
          { timeout: 1500, maxBuffer: 4096 },
        );
      } catch {
        /* Compositor rules or missing IPC retain the WM's placement. */
      }
    },
  };
}
module.exports = { identify };
