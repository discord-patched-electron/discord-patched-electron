const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  enabled,
  skipScreenPicker,
  observation,
  createObserver,
  createMutterTracker,
} = require("../../integration/audio/portal-detection.cjs");
const variant = (type, data) => ({ type, data });
function reply(sourceType, restore, serial = 100) {
  return {
    type: "method_return",
    payload: variant("ua{sv}", [
      0,
      {
        streams: variant("a(ua{sv})", [
          [42, { source_type: variant("u", sourceType), "pipewire-serial": variant("t", serial) }],
        ]),
        restore_data: variant("(suv)", restore),
      },
    ]),
  };
}
test("portal formats identify monitor or app without retaining titles/tokens", () => {
  assert.deepEqual(observation(reply(1)), {
    node: 42,
    serial: "100",
    kind: "system",
    backend: "portal-monitor",
  });
  for (const restore of [
    [
      "GNOME",
      1,
      variant("(xxa(uuv))", [
        1,
        2,
        [[7, 2, variant("(ss)", ["org.mozilla.firefox", "PRIVATE TITLE"])]],
      ]),
    ],
    [
      "KDE",
      1,
      variant("a{sv}", { windows: variant("a(ss)", [["org.mozilla.firefox", "PRIVATE TITLE"]]) }),
    ],
    [
      "hyprland",
      3,
      variant("a{sv}", {
        windowClass: variant("s", "org.mozilla.firefox"),
        token: variant("s", "PRIVATE TOKEN"),
      }),
    ],
  ]) {
    const result = observation(reply(2, restore));
    assert.equal(result.appId, "org.mozilla.firefox");
    assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
  }
  for (const r of [reply(4), reply(2), reply(2, ["GNOME", 99, []]), reply(2, ["unknown", 1, {}])])
    assert.equal(observation(r), null);
  const multiple = reply(1);
  multiple.payload.data[1].streams.data.push([43, {}]);
  assert.equal(observation(multiple), null);
  const denied = reply(1);
  denied.payload.data[0] = 1;
  assert.equal(observation(denied), null);
});
test("Mutter calls correlate replies and stream signals by caller, cookie and path", () => {
  const parse = createMutterTracker();
  parse({
    type: "method_call",
    interface: "org.gnome.Mutter.ScreenCast.Session",
    member: "RecordWindow",
    sender: ":1.4",
    cookie: 12,
    payload: variant("a{sv}", [{ "window-id": variant("t", 999) }]),
  });
  parse({
    type: "method_return",
    sender: ":1.5",
    destination: ":1.9",
    reply_cookie: 12,
    payload: variant("o", ["/wrong"]),
  });
  parse({
    type: "method_return",
    sender: ":1.5",
    destination: ":1.4",
    reply_cookie: 12,
    payload: variant("o", ["/stream/1"]),
  });
  const signal = {
    type: "signal",
    sender: ":1.5",
    path: "/stream/1",
    interface: "org.gnome.Mutter.ScreenCast.Stream",
    member: "PipeWireStreamAdded",
    payload: variant("u", [42]),
  };
  assert.equal(parse({ ...signal, path: "/wrong" }), null);
  assert.equal(parse({ ...signal, sender: ":1.7" }), null);
  assert.deepEqual(parse(signal), {
    node: 42,
    serial: null,
    kind: "application",
    windowId: 999,
    backend: "mutter-window",
  });
});
test("observer bounds input, decodes fragmented replies and rejects late children", (t) => {
  const children = [];
  const observer = createObserver({
    spawnMonitor(command, args) {
      assert.equal(command, "/usr/bin/python3");
      assert.equal(path.basename(args[0]), "portal-monitor.py");
      assert.equal(args[1], String(process.pid));
      assert.ok(
        args.includes(
          "--match=type='method_return',sender='org.freedesktop.impl.portal.desktop.kde'",
        ),
      );
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.kill = () => (child.killed = true);
      children.push(child);
      return child;
    },
  });
  t.after(() => observer.clear());
  observer.start();
  const line = JSON.stringify(reply(1)) + "\n";
  children[0].stdout.emit("data", Buffer.from(line.slice(0, 20)));
  assert.deepEqual(observer.snapshot(), []);
  children[0].stdout.emit("data", Buffer.from(line.slice(20)));
  assert.equal(observer.snapshot()[0].node, 42);
  observer.start();
  children[0].emit("error", new Error("late"));
  children[0].stdout.emit("data", Buffer.from(line));
  assert.deepEqual(observer.snapshot(), []);
  assert.equal(children[1].killed, undefined);
  children[1].stdout.emit("data", Buffer.alloc(262145, 120));
  assert.equal(children[1].killed, true);
});
test("settings default on, take effect on next read, and environment can force manual", (t) => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "audio-detection-"));
  const old = process.env.DPE_PROFILE_DIR,
    override = process.env.DPE_AUDIO_AUTO_DETECT;
  t.after(() => {
    fs.rmSync(profile, { recursive: true });
    if (old === undefined) delete process.env.DPE_PROFILE_DIR;
    else process.env.DPE_PROFILE_DIR = old;
    if (override === undefined) delete process.env.DPE_AUDIO_AUTO_DETECT;
    else process.env.DPE_AUDIO_AUTO_DETECT = override;
  });
  process.env.DPE_PROFILE_DIR = profile;
  delete process.env.DPE_AUDIO_AUTO_DETECT;
  assert.equal(enabled(), true);
  assert.equal(skipScreenPicker(), true);
  fs.writeFileSync(
    path.join(profile, "settings.json"),
    JSON.stringify({ openasar: { audioAutoDetect: false } }),
  );
  assert.equal(enabled(), false);
  assert.equal(skipScreenPicker(), true);
  fs.writeFileSync(
    path.join(profile, "settings.json"),
    JSON.stringify({
      openasar: {
        audioAutoDetect: false,
        audioSkipScreenPicker: false,
      },
    }),
  );
  assert.equal(skipScreenPicker(), false);
  const observer = createObserver({
    spawnMonitor() {
      throw new Error("must not start");
    },
  });
  observer.start();
  assert.deepEqual(observer.snapshot(), []);
  fs.writeFileSync(
    path.join(profile, "settings.json"),
    JSON.stringify({ openasar: { audioAutoDetect: true } }),
  );
  assert.equal(enabled(), true);
  process.env.DPE_AUDIO_AUTO_DETECT = "0";
  assert.equal(enabled(), false);
  assert.equal(skipScreenPicker(), false);
});
