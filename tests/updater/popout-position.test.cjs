const assert = require("node:assert/strict");
const { test } = require("node:test");
const { identify } = require("../../integration/desktop/popout-position.cjs");
const window = { isDestroyed: () => false, isFocused: () => true, getTitle: () => "PiP" };
const area = { x: 0, y: 0, width: 1280, height: 720 };

test("Niri geometry checkpoints protect manual drags, sizes, workspace changes and fullscreen", async () => {
  for (const change of ["none", "move", "resize", "workspace", "fullscreen", "retile"]) {
    const child = {
      id: 19,
      pid: process.pid,
      title: "PiP",
      is_floating: true,
      is_focused: true,
      workspace_id: 7,
      layout: {
        window_size: [512, 288],
        tile_size: [520, 296],
        tile_pos_in_workspace_view: [300, 200],
      },
    };
    let moves = 0;
    const run = async (_command, args) => {
      if (args[2] === "windows") return { stdout: JSON.stringify([child]) };
      if (args[2] === "workspaces") return { stdout: '[{"id":7,"output":"DP-1"}]' };
      if (args[2] === "outputs")
        return { stdout: JSON.stringify({ "DP-1": { logical: { ...area, scale: 1 } } }) };
      moves++;
      child.layout.tile_pos_in_workspace_view = [744, 408];
      return { stdout: "" };
    };
    const controller = identify(window, run, { NIRI_SOCKET: "/fixture" });
    await controller.place(area);
    if (change === "move") child.layout.tile_pos_in_workspace_view[0] -= 100;
    if (change === "resize") child.layout.window_size[0] += 100;
    if (change === "workspace") child.workspace_id++;
    if (change === "fullscreen") child.is_fullscreen = true;
    if (change === "retile") child.is_floating = false;
    assert.equal(await controller.unchanged(), change === "none", change);
    if (["move", "workspace", "fullscreen", "retile"].includes(change)) {
      await controller.place(area);
      assert.equal(moves, 1, change);
    }
  }
});

test("Hyprland handles rotated fractional monitors, reserved panels and both dispatcher syntaxes", async () => {
  for (const syntax of ["legacy", "lua", "lua-nonzero", "unsupported"]) {
    const child = {
      address: "0xabcdef",
      pid: process.pid,
      title: "PiP",
      floating: true,
      mapped: true,
      monitor: 2,
      workspace: { id: 8 },
      at: [-900, 400],
      size: [300, 500],
    };
    const calls = [];
    const run = async (_command, args) => {
      calls.push(args);
      if (args[0] === "-j") {
        const data = {
          clients: [child],
          activewindow: child,
          monitors: [
            {
              id: 2,
              x: -1080,
              y: 200,
              width: 1920,
              height: 1080,
              transform: 1,
              scale: 1.5,
              reserved: [10, 30, 20, 40],
            },
          ],
        };
        return { stdout: JSON.stringify(data[args[1]]) };
      }
      if (args[1] === "movewindowpixel" && syntax === "lua-nonzero") throw Error("legacy disabled");
      if ((args[1] === "movewindowpixel" && syntax !== "legacy") || syntax === "unsupported")
        return { stdout: "unknown dispatcher" };
      child.at = [-696, 924];
      return { stdout: "ok\n" };
    };
    const controller = identify(window, run, { HYPRLAND_INSTANCE_SIGNATURE: "fixture" });
    const display = await controller.display({ getDisplayMatching: () => ({ id: 2 }) });
    assert.deepEqual(display.workArea, { x: -1070, y: 230, width: 690, height: 1210 });
    assert.equal(display.scaleFactor, 1.5);
    await controller.place(display.workArea);
    const moves = calls.filter((args) => args[0] === "dispatch");
    assert.equal(moves.length, syntax === "legacy" ? 1 : 2, syntax);
    assert.deepEqual(moves[0], ["dispatch", "movewindowpixel", "exact -696 924,address:0xabcdef"]);
    if (syntax !== "legacy")
      assert.equal(
        moves[1][1],
        'hl.dsp.window.move({window="address:0xabcdef",x=-696,y=924,relative=false})',
      );
    if (syntax !== "unsupported") {
      assert.equal(await controller.unchanged(), true);
      child.at[1] -= 50;
      assert.equal(await controller.unchanged(), false);
    }
  }
});

test("Hyprland never interpolates titles or malformed addresses into dispatchers", async () => {
  for (const mode of ["address", "foreign", "fullscreen", "tiled", "ambiguous", "hidden"]) {
    let moved = false;
    const child = {
      address: mode === "address" ? '0xabc";malicious' : "0xabc",
      pid: mode === "foreign" ? process.pid + 1 : process.pid,
      title: "PiP",
      floating: mode !== "tiled",
      mapped: true,
      fullscreen: mode === "fullscreen" ? 1 : 0,
      hidden: mode === "hidden",
    };
    const run = async (_command, args) => {
      if (args[0] !== "-j") moved = true;
      const clients = mode === "ambiguous" ? [child, { ...child }] : [child];
      return { stdout: JSON.stringify(args[1] === "clients" ? clients : child) };
    };
    await identify(window, run, { HYPRLAND_INSTANCE_SIGNATURE: "fixture" }).place(area);
    assert.equal(moved, false, mode);
  }
});

test("Niri fractional corrections retain their relative sign, including rounded negative zero", async () => {
  for (const offset of [0.4, 0.0001]) {
    let move;
    const child = {
      id: 19,
      pid: process.pid,
      title: "PiP",
      is_floating: true,
      is_focused: true,
      workspace_id: 7,
      layout: {
        window_size: [512, 288],
        tile_size: [520, 296],
        tile_pos_in_workspace_view: [744 + offset, 408 + offset],
      },
    };
    const run = async (_command, args) => {
      const data = {
        windows: [child],
        workspaces: [{ id: 7, output: "DP-1" }],
        outputs: { "DP-1": { logical: { ...area, scale: 1.25 } } },
      };
      if (args[2] === "move-floating-window") move = args;
      return { stdout: JSON.stringify(data[args[2]]) };
    };
    await identify(window, run, { NIRI_SOCKET: "/fixture" }).place(area);
    assert.deepEqual(move.slice(-4), [
      "--x",
      offset === 0.4 ? "-0.4" : "+0",
      "--y",
      offset === 0.4 ? "-0.4" : "+0",
    ]);
  }
});

test("Niri revalidates geometry after monitor queries before issuing a move", async () => {
  let moved = false;
  let reads = 0;
  const child = {
    id: 19,
    pid: process.pid,
    title: "PiP",
    is_floating: true,
    is_focused: true,
    workspace_id: 7,
    layout: {
      window_size: [512, 288],
      tile_size: [520, 296],
      tile_pos_in_workspace_view: [300, 200],
    },
  };
  const run = async (_command, args) => {
    if (args[2] === "windows" && ++reads === 3) child.layout.tile_pos_in_workspace_view[0] += 100;
    const data = {
      windows: [child],
      workspaces: [{ id: 7, output: "DP-1" }],
      outputs: { "DP-1": { logical: { ...area, scale: 1 } } },
    };
    if (args[2] === "move-floating-window") moved = true;
    return { stdout: JSON.stringify(data[args[2]]) };
  };
  await identify(window, run, { NIRI_SOCKET: "/fixture" }).place(area);
  assert.equal(moved, false);
});

test("compositor sizing fits an oversized default on the actual smaller output, including decorations", async () => {
  const child = {
    id: 19,
    pid: process.pid,
    title: "PiP",
    is_floating: true,
    is_focused: true,
    workspace_id: 7,
    layout: { window_size: [640, 360], tile_size: [648, 368], tile_pos_in_workspace_view: [0, 0] },
  };
  const run = async () => ({ stdout: JSON.stringify([child]) });
  const controller = identify(window, run, { NIRI_SOCKET: "/fixture" });
  const fit = await controller.fit({ x: -400, y: 20, width: 400, height: 250 });
  assert.ok(fit.width + 8 <= 368 && fit.height + 8 <= 218);
  assert.ok(Math.abs(fit.width / fit.height - 16 / 9) < 0.02);
  assert.equal(await controller.fit(area), undefined);
});

test("a checkpoint protects manual moves without issuing any placement command", async () => {
  const child = {
    id: 19,
    pid: process.pid,
    title: "PiP",
    is_floating: true,
    is_focused: true,
    workspace_id: 7,
    layout: { window_size: [512, 288], tile_pos_in_workspace_view: [300, 200] },
  };
  const run = async (_command, args) => {
    assert.equal(args[2], "windows", "Checkpointing only reads window state");
    return { stdout: JSON.stringify([child]) };
  };
  const controller = identify(window, run, { NIRI_SOCKET: "/fixture" });
  await controller.checkpoint();
  assert.equal(await controller.unchanged(), true);
  child.layout.tile_pos_in_workspace_view[0] += 50;
  assert.equal(await controller.unchanged(), false);
});

test("Niri IPC receives the selected top-left corner in output-local coordinates", async () => {
  const child = {
    id: 19,
    pid: process.pid,
    title: "PiP",
    is_floating: true,
    is_focused: true,
    workspace_id: 7,
    layout: {
      window_size: [512, 288],
      tile_size: [520, 296],
      tile_pos_in_workspace_view: [300, 200],
    },
  };
  let move;
  const run = async (_command, args) => {
    const data = {
      windows: [child],
      workspaces: [{ id: 7, output: "DP-1" }],
      outputs: { "DP-1": { logical: { ...area, scale: 1 } } },
    };
    if (args[2] === "move-floating-window") move = args;
    return { stdout: JSON.stringify(data[args[2]]) };
  };
  await identify(window, run, { NIRI_SOCKET: "/fixture" }, "top-left").place(area);
  assert.deepEqual(move.slice(-4), ["--x", "-284", "--y", "-184"]);
});

test("Hyprland floating gaps, border overrides and panel reservations bound decorated corners", async () => {
  for (const mode of ["floating", "inherit", "old", "malformed"]) {
    const child = {
      address: "0xabc",
      pid: process.pid,
      title: "PiP",
      floating: true,
      mapped: true,
      monitor: 1,
      workspace: { id: 1 },
      at: [100, 100],
      size: [400, 300],
    };
    const moves = [];
    const run = async (_command, args) => {
      if (args[0] === "dispatch") {
        moves.push(args);
        return { stdout: "ok" };
      }
      let data;
      if (args[1] === "clients") data = [child];
      if (args[1] === "activewindow") data = child;
      if (args[1] === "monitors")
        data = [
          {
            id: 1,
            x: -1600,
            y: 100,
            width: 2000,
            height: 1250,
            scale: 1.25,
            reserved: [20, 40, 30, 50],
          },
        ];
      if (args[1] === "getprop") data = { border_size: 6 };
      if (args[1] === "getoption") {
        if (mode === "old") throw Error("unsupported");
        if (args[2].endsWith("float_gaps"))
          data = {
            css: mode === "inherit" ? "-1" : mode === "malformed" ? "NaN 4" : "10 20 30 40",
          };
        if (args[2].endsWith("gaps_out")) data = { custom: "24 28" };
        if (args[2].endsWith("border_size")) data = { int: 4 };
      }
      return { stdout: JSON.stringify(data) };
    };
    const controller = identify(
      window,
      run,
      { HYPRLAND_INSTANCE_SIGNATURE: "fixture" },
      "bottom-right",
    );
    const display = await controller.display({ getDisplayMatching: () => ({}) });
    const base = { x: -1580, y: 140, width: 1550, height: 910 };
    const expected =
      mode === "floating"
        ? { x: -1540, y: 150, width: 1490, height: 870 }
        : mode === "inherit"
          ? { x: -1552, y: 164, width: 1494, height: 862 }
          : base;
    assert.deepEqual(display.workArea, expected, mode);
    await controller.place(display.workArea);
    const x = expected.x + expected.width - 412 - 16 + 6;
    const y = expected.y + expected.height - 312 - 16 + 6;
    assert.equal(moves[0][2], `exact ${x} ${y},address:0xabc`, mode);
  }
});

test("Hyprland resizing revalidates owned geometry and never resizes a moved or unfocused popout", async () => {
  for (const change of ["none", "move", "focus", "invalid"]) {
    const child = {
      address: "0xabc",
      pid: process.pid,
      title: "PiP",
      floating: true,
      mapped: true,
      monitor: 1,
      workspace: { id: 1 },
      at: [100, 100],
      size: [400, 300],
    };
    let active = child;
    const commands = [];
    const run = async (_command, args) => {
      if (args[0] === "dispatch") {
        commands.push(args);
        return { stdout: args[1] === "resizewindowpixel" ? "unsupported" : "ok" };
      }
      const data = args[1] === "clients" ? [child] : args[1] === "activewindow" ? active : {};
      return { stdout: JSON.stringify(data) };
    };
    const controller = identify(window, run, { HYPRLAND_INSTANCE_SIGNATURE: "fixture" });
    await controller.checkpoint();
    if (change === "move") child.at[0] += 100;
    if (change === "focus") active = { address: "0xdef" };
    await controller.resize({ width: change === "invalid" ? NaN : 200, height: 400 });
    assert.equal(commands.length, change === "none" ? 2 : 0, change);
    if (change === "none")
      assert.equal(
        commands[1][1],
        'hl.dsp.window.resize({window="address:0xabc",x=200,y=400,relative=false})',
      );
  }
});

test("Hyprland fullscreen recovery only resets the previously identified owned window after native exit", async () => {
  for (const mode of ["normal", "still-fullscreen", "foreign"]) {
    const child = {
      pid: process.pid,
      address: "0xabc",
      title: "PiP",
      mapped: true,
      floating: true,
      size: [400, 300],
      at: [100, 100],
      monitor: 1,
      workspace: { id: 1 },
    };
    let full = false;
    const calls = [];
    const run = async (_command, args) => {
      if (args[0] === "dispatch") {
        calls.push(args);
        child.fullscreen = 0;
        child.fullscreenClient = 0;
        return { stdout: "ok" };
      }
      return {
        stdout: JSON.stringify(
          args[1] === "clients" ? [child] : args[1] === "activewindow" ? child : {},
        ),
      };
    };
    const controller = identify({ ...window, isFullScreen: () => full }, run, {
      HYPRLAND_INSTANCE_SIGNATURE: "fixture",
    });
    await controller.checkpoint();
    child.fullscreenClient = 1;
    full = mode === "still-fullscreen";
    if (mode === "foreign") child.pid++;
    await controller.resumeNormal();
    assert.equal(calls.length, mode === "normal" ? 1 : 0, mode);
    if (calls.length) assert.match(calls[0][1], /window="address:0xabc",internal=0,client=0/);
  }
});

test("Niri layout insets follow ordered includes, disabled nodes and workspace overrides", () => {
  const { load } = require("../../integration/desktop/niri-insets.cjs");
  const files = {
    "/config/main.kdl":
      'layout { gaps 20; struts { top 30; left 10; }; }; include "generated.kdl"; /- include "missing.kdl"; workspace "Call" {layout {gaps 32; struts {top 64;};};}',
    "/config/generated.kdl":
      '/* outer /* nested */ comment */ layout {gaps 10;}; /- layout {gaps 99;}; window-rule {match app-id r#"foo{ // }"#;};',
  };
  const read = (file) => {
    assert.ok(Object.hasOwn(files, file));
    return files[file];
  };
  assert.deepEqual(load("/config/main.kdl", undefined, read), {
    gaps: 10,
    struts: { top: 30, left: 10, right: 0, bottom: 0 },
  });
  assert.deepEqual(load("/config/main.kdl", "Call", read), {
    gaps: 32,
    struts: { top: 64, left: 0, right: 0, bottom: 0 },
  });
  assert.throws(
    () => load("/config/main.kdl", undefined, () => 'include "main.kdl";'),
    /Recursive/,
  );
  assert.throws(() => load("/config/main.kdl", undefined, () => " ".repeat(1048577)), /Oversized/);
});

test("Niri calibrates panel-reserved area using only the owned floating window", async () => {
  const child = {
    id: 19,
    pid: process.pid,
    title: "PiP",
    is_floating: true,
    is_focused: true,
    workspace_id: 7,
    layout: {
      window_size: [400, 300],
      tile_size: [408, 308],
      tile_pos_in_workspace_view: [300, 200],
    },
  };
  const reserved = { x: 20, y: 64, width: 1240, height: 616 };
  const commands = [];
  const run = async (_command, args) => {
    if (args[2] === "windows") return { stdout: JSON.stringify([child]) };
    if (args[2] === "workspaces") return { stdout: '[{"id":7,"output":"DP-1"}]' };
    if (args[2] === "outputs")
      return { stdout: JSON.stringify({ "DP-1": { logical: { ...area, scale: 1 } } }) };
    commands.push(args);
    assert.ok(args.includes(String(child.id)));
    if (args[2] === "center-window")
      child.layout.tile_pos_in_workspace_view = [
        reserved.x + (reserved.width - 408) / 2,
        reserved.y + (reserved.height - 308) / 2,
      ];
    else
      child.layout.tile_pos_in_workspace_view = [
        reserved.x + Number(args[args.indexOf("--x") + 1]),
        reserved.y + Number(args[args.indexOf("--y") + 1]),
      ];
    return { stdout: "" };
  };
  const controller = identify(window, run, { NIRI_SOCKET: "/fixture" }, "top-right");
  await controller.prepare("top-right");
  assert.deepEqual(
    (await controller.display({ getDisplayMatching: () => ({ workArea: area }) })).workArea,
    reserved,
  );
  const total = commands.length;
  await controller.prepare("bottom-left");
  assert.equal(commands.length, total, "Once calibrated, corner changes need no calibration moves");
});
