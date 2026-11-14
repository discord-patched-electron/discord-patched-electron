const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const execute = promisify(execFile);
const pair = (value) => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);
const rect = (value) =>
  value &&
  [value.x, value.y, value.width, value.height].every(Number.isFinite) &&
  value.width > 0 &&
  value.height > 0;

function corner(area, width, height, preferred = "bottom-right") {
  if (
    preferred &&
    typeof preferred === "object" &&
    [preferred.x, preferred.y].every(Number.isFinite)
  )
    // Only internal restoration supplies a point. Preserve deliberate manual
    // placement, including partially off-screen bounds, across fullscreen.
    return { x: Math.round(preferred.x), y: Math.round(preferred.y) };

  const left = preferred === "top-left" || preferred === "bottom-left";
  const top = preferred === "top-left" || preferred === "top-right";
  return {
    x: Math.round(
      area.x +
        (left
          ? Math.min(16, Math.max(0, area.width - width))
          : Math.max(0, area.width - width - 16)),
    ),
    y: Math.round(
      area.y +
        (top
          ? Math.min(16, Math.max(0, area.height - height))
          : Math.max(0, area.height - height - 16)),
    ),
  };
}

function niri(run, env) {
  const query = async (kind = "windows") =>
    JSON.parse(
      (await run("niri", ["msg", "--json", kind], { timeout: 1500, maxBuffer: 1024 * 1024 }))
        .stdout,
    );
  const output = async (child) => {
    const [workspaces, outputs] = await Promise.all([query("workspaces"), query("outputs")]);
    return outputs[workspaces.find((item) => item.id === child.workspace_id)?.output]?.logical;
  };
  const areas = new Map();
  const key = (logical) =>
    [logical.x, logical.y, logical.width, logical.height, logical.scale].join(":");
  return {
    async prepare(child, preferred, allowed) {
      const logical = await output(child);
      if (!rect(logical) || areas.has(key(logical)) || !(await allowed())) return;
      const options = { timeout: 1500, maxBuffer: 4096 };
      const read = async () =>
        (await query()).find(
          (item) =>
            item.id === child.id &&
            item.pid === process.pid &&
            item.is_floating &&
            item.workspace_id === child.workspace_id,
        );
      const act = async (args) => {
        if (!(await allowed())) return false;
        await run("niri", ["msg", "action", ...args, "--id", String(child.id)], options);
        return true;
      };
      // Niri's fixed floating coordinates are relative to its real working
      // area, including layer-shell panels. Calibrate only this owned window
      // during authorized initial placement; never infer from other apps.
      if (!(await act(["center-window"]))) return;
      const centered = await read();
      if (
        !centered ||
        !pair(centered.layout?.tile_size) ||
        !pair(centered.layout?.tile_pos_in_workspace_view)
      )
        return;
      const center = centered.layout.tile_pos_in_workspace_view.map(
        (v, i) => v + centered.layout.tile_size[i] / 2,
      );
      const preferredPoint = corner(
        { x: 0, y: 0, width: logical.width, height: logical.height },
        ...centered.layout.tile_size,
        preferred,
      );
      if (
        !(await act([
          "move-floating-window",
          "--x",
          String(preferredPoint.x),
          "--y",
          String(preferredPoint.y),
        ]))
      )
        return;
      const moved = await read();
      if (!moved || !pair(moved.layout?.tile_pos_in_workspace_view)) return;
      const origin = moved.layout.tile_pos_in_workspace_view.map(
        (v, i) => v - [preferredPoint.x, preferredPoint.y][i],
      );
      const dimensions = center.map((v, i) => 2 * (v - origin[i]));
      const tolerance = 2 / (logical.scale || 1);
      if (
        origin.some((v) => v < -tolerance) ||
        dimensions.some((v) => v <= 0) ||
        origin[0] + dimensions[0] > logical.width + tolerance ||
        origin[1] + dimensions[1] > logical.height + tolerance
      )
        return;
      areas.set(key(logical), {
        x: logical.x + Math.max(0, origin[0]),
        y: logical.y + Math.max(0, origin[1]),
        width: dimensions[0],
        height: dimensions[1],
      });
    },
    windows: () => query(),
    async globalPosition(child) {
      const logical = await output(child);
      return (
        logical &&
        child.layout.tile_pos_in_workspace_view.map((n, i) => n + (i ? logical.y : logical.x))
      );
    },
    id: (item) => item.id,
    title: (item) => item.title,
    focused: (item) => item.is_focused,
    floating: (item) => item.is_floating,
    size: (item) => item.layout?.window_size,
    outerSize: (item) => item.layout?.tile_size,
    position: (item) => item.layout?.tile_pos_in_workspace_view,
    scope: (item) => item.workspace_id,
    normal: (item) => !item.is_fullscreen && !item.is_maximized,
    async display(child, screen) {
      const logical = await output(child);
      if (!rect(logical) || !(logical.scale > 0)) return undefined;
      const measured = areas.get(key(logical));
      const workspace = measured
        ? (await query("workspaces")).find((item) => item.id === child.workspace_id)?.name
        : undefined;
      const inset = measured
        ? require("./niri-insets.cjs").insets(workspace, env)
        : { left: 0, right: 0, top: 0, bottom: 0 };
      const area = measured && {
        x: measured.x + inset.left,
        y: measured.y + inset.top,
        width: measured.width - inset.left - inset.right,
        height: measured.height - inset.top - inset.bottom,
      };
      if (measured && !rect(area)) return undefined;
      return {
        ...screen.getDisplayMatching(logical),
        scaleFactor: logical.scale,
        ...(area ? { workArea: area } : {}),
      };
    },
    async move(child, area, allowed, preferred) {
      const size = child.layout?.tile_size;
      const position = child.layout?.tile_pos_in_workspace_view;
      const logical = await output(child);
      if (!pair(size) || size.some((v) => v <= 0) || !pair(position) || !rect(logical)) return;
      // Niri absolute moves are relative to its working-area origin,
      // not Electron's output origin. A delta from the reported
      // output-local tile position avoids mixing those coordinate systems.
      const target = corner(area, ...size, preferred);
      const delta = [target.x - logical.x - position[0], target.y - logical.y - position[1]];
      if (!(await allowed())) return;
      await run(
        "niri",
        [
          "msg",
          "action",
          "move-floating-window",
          "--id",
          String(child.id),
          "--x",
          signed(delta[0]),
          "--y",
          signed(delta[1]),
        ],
        { timeout: 1500, maxBuffer: 4096 },
      );
      return [target.x - logical.x, target.y - logical.y];
    },
  };
}
const signed = (value) => {
  const rounded = Math.round(value * 1000) / 1000;
  // Bare "0" means absolute origin to Niri; even rounded negative zero must
  // retain the explicit relative sign.
  return `${rounded < 0 ? "" : "+"}${rounded}`;
};

function sway(run) {
  const query = async () => {
    const tree = JSON.parse(
      (await run("swaymsg", ["-r", "-t", "get_tree"], { timeout: 1500, maxBuffer: 1024 * 1024 }))
        .stdout,
    );
    const windows = [];
    function visit(node, workspace, floating = false, workspaceId) {
      if (node.type === "workspace") {
        workspace = node.rect;
        workspaceId = node.id;
      }
      if (node.pid) windows.push({ ...node, workspace, floating, workspaceId });
      for (const child of node.nodes || []) visit(child, workspace, floating, workspaceId);
      for (const child of node.floating_nodes || []) visit(child, workspace, true, workspaceId);
    }
    visit(tree);
    return windows;
  };
  return {
    windows: query,
    id: (item) => item.id,
    title: (item) => item.name,
    focused: (item) => item.focused,
    floating: (item) => item.floating,
    size: (item) => [item.window_rect?.width, item.window_rect?.height],
    outerSize: (item) => [item.rect?.width, item.rect?.height],
    position: (item) => [item.rect?.x, item.rect?.y],
    scope: (item) => item.workspaceId,
    normal: (item) => !item.fullscreen_mode,
    async display(child, screen) {
      if (!rect(child.workspace)) return undefined;
      return { ...screen.getDisplayMatching(child.workspace), workArea: child.workspace };
    },
    async move(child, _area, allowed, preferred) {
      if (!rect(child.rect) || !rect(child.workspace)) return;
      const target = corner(child.workspace, child.rect.width, child.rect.height, preferred);
      // Criteria contain only a validated numeric ID, never a title or regex.
      if (!(await allowed())) return;
      const result = JSON.parse(
        (
          await run(
            "swaymsg",
            ["-r", `[con_id=${child.id}] move absolute position ${target.x} px ${target.y} px`],
            { timeout: 1500, maxBuffer: 4096 },
          )
        ).stdout,
      );
      if (result.length && result.every((item) => item.success)) return [target.x, target.y];
    },
  };
}

function hyprland(run) {
  const query = async (...args) =>
    JSON.parse(
      (
        await run("hyprctl", ["-j", ...args], {
          timeout: 1500,
          maxBuffer: 1024 * 1024,
        })
      ).stdout,
    );
  const option = async (name) => {
    for (const separator of [":", "."]) {
      try {
        return await query("getoption", `general${separator}${name}`);
      } catch {}
    }
  };
  const settings = Promise.all([option("float_gaps"), option("gaps_out"), option("border_size")]);
  const cssGaps = (value) => {
    const raw = value?.css ?? value?.custom ?? value?.str;
    if (typeof raw !== "string") return undefined;
    const values = raw.trim().split(/\s+/).map(Number);
    if (
      !values.length ||
      values.length > 4 ||
      values.some((n) => !Number.isFinite(n) || Math.abs(n) > 65535)
    )
      return undefined;
    const [top, right = top, bottom = top, left = right] = values;
    return [top, right, bottom, left];
  };
  const display = async (child, screen) => {
    const monitor = (await query("monitors")).find(
      (item) => item.id === child.monitor && !item.disabled,
    );
    if (!monitor || !rect(monitor) || !(monitor.scale > 0)) return undefined;
    const rotated = [1, 3, 5, 7].includes(monitor.transform);
    const width = (rotated ? monitor.height : monitor.width) / monitor.scale;
    const height = (rotated ? monitor.width : monitor.height) / monitor.scale;
    const reserved = monitor.reserved;
    if (
      !Array.isArray(reserved) ||
      reserved.length !== 4 ||
      !reserved.every((v) => Number.isFinite(v) && v >= 0)
    )
      return undefined;
    const [floating, outer] = await settings;
    let gaps = cssGaps(floating);
    if (gaps?.some((n) => n < 0)) gaps = cssGaps(outer);
    const [gapTop, gapRight, gapBottom, gapLeft] = (gaps || [0, 0, 0, 0]).map((n) =>
      Math.max(0, n),
    );
    const [left, top, right, bottom] = reserved.map(
      (n, i) => n + [gapLeft, gapTop, gapRight, gapBottom][i],
    );
    const area = {
      x: monitor.x + left,
      y: monitor.y + top,
      width: width - left - right,
      height: height - top - bottom,
    };
    if (!rect(area)) return undefined;
    return { ...screen?.getDisplayMatching(area), scaleFactor: monitor.scale, workArea: area };
  };
  let initial = true;
  return {
    async windows() {
      if (initial) {
        initial = false;
        // Constraint removal can commit after the first focus notification.
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const [clients, active] = await Promise.all([query("clients"), query("activewindow")]);
      const [, , border] = await settings;
      return Promise.all(
        clients.map(async (item) => {
          let width = Number.isFinite(border?.int) ? border.int : 0;
          if (
            item.pid === process.pid &&
            item.address === active.address &&
            /^0x[0-9a-f]+$/i.test(item.address)
          ) {
            try {
              const props = await query("getprop", `address:${item.address}`, "border_size");
              if (Number.isFinite(props.border_size) && props.border_size >= 0)
                width = props.border_size;
            } catch {
              /* Older releases expose only the global border size. */
            }
          }
          return {
            ...item,
            border: Math.max(0, Math.min(100, width)),
            focused: item.address === active.address,
          };
        }),
      );
    },
    id: (item) => item.address,
    validId: (id) => typeof id === "string" && /^0x[0-9a-f]+$/i.test(id) && !/^0x0+$/i.test(id),
    title: (item) => item.title,
    focused: (item) => item.focused,
    floating: (item) => item.floating,
    normal: (item) => item.mapped && !item.hidden && !item.fullscreen && !item.fullscreenClient,
    size: (item) => item.size,
    outerSize: (item) => item.size?.map((n) => n + 2 * item.border),
    position: (item) => item.at?.map((n) => n - item.border),
    scope: (item) =>
      `${item.monitor}:${item.workspace?.address ?? item.workspace?.id ?? item.workspace?.name}`,
    display,
    resizedAtCenter(child, anchor) {
      return (
        anchor &&
        pair(anchor.outerSize) &&
        pair(child.size) &&
        near(
          child.at.map((n) => n - child.border),
          anchor.position.map(
            (n, axis) => n + (anchor.outerSize[axis] - child.size[axis] - 2 * child.border) / 2,
          ),
        )
      );
    },
    async resumeNormal(child, allowed) {
      if (!child.mapped || child.hidden || !(await allowed())) return;
      if (child.fullscreen || child.fullscreenClient)
        await run(
          "hyprctl",
          [
            "dispatch",
            `hl.dsp.window.fullscreen_state({window="address:${child.address}",internal=0,client=0,action="set"})`,
          ],
          { timeout: 1500, maxBuffer: 4096 },
        );
    },
    async resize(child, size, allowed) {
      if (!(await allowed())) return;
      const options = { timeout: 1500, maxBuffer: 4096 };
      const result = await run(
        "hyprctl",
        [
          "dispatch",
          "resizewindowpixel",
          `exact ${size.width} ${size.height},address:${child.address}`,
        ],
        options,
      ).catch(() => ({ stdout: "" }));
      if (result.stdout.trim() !== "ok") {
        if (!(await allowed())) return;
        await run(
          "hyprctl",
          [
            "dispatch",
            `hl.dsp.window.resize({window="address:${child.address}",x=${size.width},y=${size.height},relative=false})`,
          ],
          options,
        );
      }
    },
    async move(child, _area, allowed, preferred) {
      const output = await display(child);
      if (!output || !pair(child.size) || child.size.some((v) => v <= 0) || !(await allowed()))
        return;
      const outside = corner(
        output.workArea,
        ...child.size.map((n) => n + 2 * child.border),
        preferred,
      );
      const target = { x: outside.x + child.border, y: outside.y + child.border };
      const options = { timeout: 1500, maxBuffer: 4096 };
      // Older releases take named dispatchers; current releases use Lua
      // dispatcher expressions. Both address only our validated hex window ID.
      const result = await run(
        "hyprctl",
        ["dispatch", "movewindowpixel", `exact ${target.x} ${target.y},address:${child.address}`],
        options,
      ).catch(() => ({ stdout: "" }));
      if (result.stdout.trim() !== "ok") {
        if (!(await allowed())) return;
        const lua = `hl.dsp.window.move({window="address:${child.address}",x=${target.x},y=${target.y},relative=false})`;
        const fallback = await run("hyprctl", ["dispatch", lua], options);
        if (fallback.stdout.trim() !== "ok") return;
      }
      return [outside.x, outside.y];
    },
  };
}

function kwin(run) {
  const query = async (request = {}) => {
    const source = `()=>(${require("./kwin-popout.cjs").toString()})(${JSON.stringify({ pid: process.pid, ...request })})`;
    return JSON.parse(
      (
        await run(
          "/usr/bin/python3",
          ["-I", require("node:path").join(__dirname, "kwin-popout.py"), source],
          {
            timeout: 4500,
            maxBuffer: 65536,
          },
        )
      ).stdout,
    );
  };
  return {
    async windows(id) {
      const child = await query(id ? { id } : {});
      return child ? [child] : [];
    },
    id: (item) => item.id,
    validId: (id) =>
      typeof id === "string" && /^\{?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}?$/i.test(id),
    title: (item) => item.title,
    focused: (item) => item.focused,
    floating: () => true,
    normal: () => true, // The KWin script rejects tiled/fullscreen/special windows.
    size: (item) => [item.client?.width, item.client?.height],
    outerSize: (item) => [item.bounds?.width, item.bounds?.height],
    position: (item) => [item.bounds?.x, item.bounds?.y],
    scope: (item) => `${item.output?.x}:${item.output?.y}:${item.workspace}`,
    resizedAtCenter(child, anchor) {
      const size = [child.bounds?.width, child.bounds?.height];
      return (
        (anchor &&
          pair(anchor.outerSize) &&
          pair(size) &&
          near(
            [child.bounds.x, child.bounds.y],
            anchor.position.map((value, axis) => value + (anchor.outerSize[axis] - size[axis]) / 2),
          )) ||
        (anchor &&
          pair(anchor.outerSize) &&
          pair(size) &&
          near(
            [child.bounds.x, child.bounds.y],
            anchor.position.map((value, axis) => {
              const origin = axis ? child.area.y : child.area.x;
              const extent = axis ? child.area.height : child.area.width;
              return Math.max(
                origin,
                Math.min(
                  origin + extent - size[axis],
                  value + (anchor.outerSize[axis] - size[axis]) / 2,
                ),
              );
            }),
          ))
      );
    },
    async display(child, screen) {
      if (!rect(child.area) || !rect(child.output)) return undefined;
      return {
        ...screen.getDisplayMatching(child.output),
        workArea: child.area,
        scaleFactor: child.scale || 1,
      };
    },
    async topmost(child, enabled) {
      await query({ id: child.id, keepAbove: enabled });
    },
    async move(child, area, allowed, preferred) {
      if (!rect(child.bounds) || !rect(child.area) || !(await allowed())) return;
      const target = corner(child.area, child.bounds.width, child.bounds.height, preferred);
      const result = await query({ id: child.id, before: child.bounds, target });
      if (result && rect(result.bounds)) return [result.bounds.x, result.bounds.y];
    },
  };
}

// Identify once at map/focus, then address only that owned floating window.
// Missing IPC, ambiguity, focus loss, user interaction and retiling all stop
// automatic placement. X11 uses BrowserWindow geometry without compositor IPC.
function identify(window, run = execute, env = process.env, preferred = "bottom-right") {
  const backend = env.NIRI_SOCKET
    ? niri(run, env)
    : env.SWAYSOCK
      ? sway(run)
      : env.HYPRLAND_INSTANCE_SIGNATURE
        ? hyprland(run)
        : env.WAYLAND_DISPLAY &&
            /(?:KDE|PLASMA)/i.test(env.XDG_CURRENT_DESKTOP || env.XDG_SESSION_DESKTOP || "")
          ? kwin(run)
          : undefined;
  if (!backend) return undefined;
  const alive = (background = false) => !window.isDestroyed() && (background || window.isFocused());
  const owned = (item) =>
    item.pid === process.pid && backend.floating(item) && backend.normal(item);
  let anchor;
  const atAnchor = (child, checkSize = false) =>
    !anchor ||
    (backend.scope(child) === anchor.scope &&
      near(backend.position(child), anchor.position) &&
      (!checkSize || near(backend.size(child), anchor.size)));
  const identity = backend
    .windows()
    .then((windows) => {
      if (!alive()) return undefined;
      const matches = windows.filter(
        (item) => owned(item) && backend.focused(item) && backend.title(item) === window.getTitle(),
      );
      return matches.length === 1 &&
        (backend.validId || Number.isSafeInteger)(backend.id(matches[0]))
        ? backend.id(matches[0])
        : undefined;
    })
    .catch(() => undefined);
  async function current(background = false) {
    const id = await identity;
    if (id === undefined || !alive(background)) return undefined;
    return (await backend.windows(id)).find(
      (item) => backend.id(item) === id && owned(item) && (background || backend.focused(item)),
    );
  }
  async function snapshot(screen) {
    const child = await current(true);
    if (!child) return;
    const display = await backend.display(child, screen);
    const size = backend.outerSize(child);
    const position = backend.globalPosition
      ? await backend.globalPosition(child)
      : backend.position(child);
    if (!display || !pair(size) || !pair(position)) return;
    return {
      bounds: { x: position[0], y: position[1], width: size[0], height: size[1] },
      area: display.workArea,
      scope: backend.scope(child),
    };
  }
  return {
    async prepare(preferred, allowed = () => true) {
      if (!backend.prepare) return;
      const child = await current(true);
      if (child)
        await backend.prepare(child, preferred, async () => {
          const latest = await current(true);
          return (
            !!latest &&
            backend.scope(latest) === backend.scope(child) &&
            near(backend.size(latest), backend.size(child)) &&
            alive(true) &&
            allowed()
          );
        });
    },
    async topmost(enabled) {
      try {
        const child = await current(true);
        if (child) await backend.topmost?.(child, enabled);
      } catch {
        /* Unsupported compositor retains its stacking policy. */
      }
    },
    snapshot: async (screen) => {
      try {
        return await snapshot(screen);
      } catch {
        return undefined;
      }
    },
    async resumeNormal() {
      if (!backend.resumeNormal) return;
      const id = await identity;
      if (id === undefined || !alive(true) || window.isFullScreen()) return;
      const find = async () =>
        (await backend.windows(id)).find(
          (item) => backend.id(item) === id && item.pid === process.pid,
        );
      const child = await find();
      if (child)
        await backend.resumeNormal(
          child,
          async () => alive(true) && !window.isFullScreen() && !!(await find()),
        );
    },
    async resizeTo(size, screen, previous) {
      const child = await current(true);
      if (!child) return;
      const before = previous || (await snapshot(screen));
      await backend.resize?.(child, size, async () => {
        const latest = await current(true);
        return !!latest && backend.scope(latest) === backend.scope(child);
      });
      const latest = await current(true);
      const positioned = latest && before ? await snapshot(screen) : undefined;
      if (
        latest &&
        before &&
        positioned &&
        !near([positioned.bounds.x, positioned.bounds.y], [before.bounds.x, before.bounds.y])
      )
        await backend.move(
          latest,
          before.area,
          async () => {
            const check = await current(true);
            return (
              !!check &&
              backend.scope(check) === backend.scope(child) &&
              near(backend.position(check), backend.position(latest))
            );
          },
          { x: before.bounds.x, y: before.bounds.y },
        );
    },
    async restore(screen, saved) {
      const child = await current(true);
      if (!child) return;
      const actual = await snapshot(screen);
      if (!actual) return;
      await backend.resize?.(
        child,
        {
          width: Math.max(
            1,
            Math.round(
              saved.client?.width ||
                saved.bounds.width - (backend.outerSize(child)[0] - backend.size(child)[0]),
            ),
          ),
          height: Math.max(
            1,
            Math.round(
              saved.client?.height ||
                saved.bounds.height - (backend.outerSize(child)[1] - backend.size(child)[1]),
            ),
          ),
        },
        () => alive(true),
      );
      const position = { x: saved.bounds.x, y: saved.bounds.y };
      for (let attempt = 0; attempt < 10; attempt++) {
        const latest = await current(true);
        if (!latest) return;
        const size = backend.outerSize(latest);
        if (near(size, [saved.bounds.width, saved.bounds.height])) {
          await backend.move(
            latest,
            actual.area,
            async () => {
              const check = await current(true);
              return (
                !!check &&
                backend.scope(check) === backend.scope(latest) &&
                near(backend.position(check), backend.position(latest))
              );
            },
            position,
          );
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 40));
      }
    },
    async moveTo(preferred, screen, before) {
      try {
        const child = await current(true);
        if (!child) return;
        const same = (value) =>
          value &&
          ["x", "y", "width", "height"].every(
            (key) => Math.abs(value.bounds[key] - before.bounds[key]) <= 2,
          );
        if (!same(await snapshot(screen))) return;
        await backend.move(
          child,
          before.area,
          async () => alive(true) && same(await snapshot(screen)),
          preferred,
        );
        // This explicit move becomes the checkpoint for pending media sizing.
        const latest = await current(true);
        if (latest)
          anchor = {
            position: backend.position(latest),
            size: backend.size(latest),
            outerSize: backend.outerSize(latest),
            scope: backend.scope(latest),
          };
      } catch {
        /* Unsupported compositors retain placement. */
      }
    },
    async fit(area) {
      try {
        const child = await current();
        if (!child || !rect(area)) return undefined;
        const size = backend.size(child),
          outer = backend.outerSize(child);
        if (!pair(size) || !pair(outer) || size.some((v) => v <= 0) || outer.some((v) => v <= 0))
          return undefined;
        const scale = Math.min(
          1,
          Math.max(1, area.width - 32 - Math.max(0, outer[0] - size[0])) / size[0],
          Math.max(1, area.height - 32 - Math.max(0, outer[1] - size[1])) / size[1],
        );
        if (scale >= 1) return undefined;
        return {
          width: Math.max(1, Math.floor(size[0] * scale)),
          height: Math.max(1, Math.floor(size[1] * scale)),
        };
      } catch {
        return undefined;
      }
    },
    async checkpoint(expected) {
      try {
        const child = await current();
        if (child)
          anchor = {
            position: backend.position(child),
            size: expected ? [expected.width, expected.height] : backend.size(child),
            outerSize: backend.outerSize(child),
            scope: backend.scope(child),
          };
      } catch {
        /* Unavailable IPC leaves Electron's resize guard active. */
      }
    },
    async unchanged() {
      try {
        if (!anchor) return true;
        const child = await current();
        return !!child && atAnchor(child, true);
      } catch {
        return false;
      }
    },
    async display(screen) {
      try {
        const child = await current();
        return child ? await backend.display(child, screen) : undefined;
      } catch {
        return undefined;
      }
    },
    async resize(size, allowed = () => true) {
      if (
        !backend.resize ||
        ![size?.width, size?.height].every((n) => Number.isSafeInteger(n) && n > 0)
      )
        return;
      try {
        const child = await current();
        if (!child || !atAnchor(child, true) || !allowed()) return;
        await backend.resize(child, size, async () => {
          const latest = await current();
          return !!latest && atAnchor(latest, true) && allowed() && alive();
        });
      } catch {
        /* Unsupported dispatchers retain Electron's size request. */
      }
    },
    async place(area, expected, allowed = () => true) {
      try {
        // setBounds is asynchronous on Wayland. Do not anchor using the old
        // tile size while the compositor is still committing our new size.
        for (let attempt = 0; attempt < 10; attempt++) {
          const child = await current();
          if (
            !child ||
            !allowed() ||
            !alive() ||
            (!atAnchor(child) &&
              !(
                expected &&
                anchor?.scope === backend.scope(child) &&
                backend.resizedAtCenter?.(child, anchor)
              ))
          )
            return;
          const size = backend.size(child);
          if (
            !expected ||
            (pair(size) &&
              Math.abs(size[0] - expected.width) <= 2 &&
              Math.abs(size[1] - expected.height) <= 2)
          ) {
            const position = await backend.move(
              child,
              area,
              async () => {
                if (!alive() || !allowed()) return false;
                const latest = await current();
                return (
                  !!latest &&
                  backend.scope(latest) === backend.scope(child) &&
                  near(backend.position(latest), backend.position(child)) &&
                  near(backend.size(latest), backend.size(child)) &&
                  alive() &&
                  allowed()
                );
              },
              preferred,
            );
            if (pair(position))
              anchor = {
                position,
                size: backend.size(child),
                outerSize: backend.outerSize(child),
                scope: backend.scope(child),
              };
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 40));
        }
      } catch {
        /* Missing/unsupported IPC retains the WM's placement. */
      }
    },
  };
}
function near(a, b) {
  return pair(a) && pair(b) && a.every((value, index) => Math.abs(value - b[index]) <= 2);
}
module.exports = { identify, corner };
