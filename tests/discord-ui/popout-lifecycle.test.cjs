const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const adapter = require("../../integration/discord-ui/popout-lifecycle.cjs");

test("video close releases the name synchronously while preserving native calls and live reuse", () => {
  const key = "DISCORD_CHANNEL_CALL_POPOUT";
  const calls = [];
  const child = {
    name: key,
    close(...args) {
      calls.push({ name: this.name, args });
      return 42;
    },
  };
  const context = vm.createContext({
    URL,
    location: {
      href: "https://canary.discord.com/channels/@me",
      origin: "https://canary.discord.com",
    },
    open(...args) {
      calls.push(args);
      return child;
    },
  });
  vm.runInContext(`(${adapter.toString()})()`, context);
  const open = context.open;
  assert.equal(context.open("/popout", key, "width=640"), child);
  assert.equal(child.name, key, "Opening an existing live window does not release its name");
  const close = child.close;
  context.open("/popout", key);
  assert.equal(child.close, close);
  assert.equal(child.close("argument"), 42);
  assert.deepEqual(calls.at(-1), { name: "", args: ["argument"] });
  vm.runInContext(`(${adapter.toString()})()`, context);
  assert.equal(context.open, open, "Installation is idempotent");
});

test("debug windows, overlays, foreign origins and unrelated URLs retain their close function", () => {
  for (const [url, name] of [
    ["/popout", "DISCORD_RTC_DEBUG_POPOUT"],
    ["/popout", "DISCORD_OutOfProcessOverlay"],
    ["https://example.com/popout", "DISCORD_CHANNEL_CALL_POPOUT"],
    ["/other", "DISCORD_CHANNEL_CALL_POPOUT"],
  ]) {
    const child = { name, close() {} },
      close = child.close;
    const context = vm.createContext({
      URL,
      location: { href: "https://discord.com/", origin: "https://discord.com" },
      open() {
        return child;
      },
    });
    vm.runInContext(`(${adapter.toString()})()`, context);
    assert.equal(context.open(url, name), child);
    assert.equal(child.close, close);
  }
});

test("failed native close restores the name and preserves the error", () => {
  const error = Error("close failed");
  const context = vm.createContext({
    name: "DISCORD_CALL_TILE_POPOUT_test",
    open() {},
    close() {
      throw error;
    },
  });
  vm.runInContext(`(${adapter.toString()})()`, context);
  assert.throws(
    () => context.close(),
    (value) => value === error,
  );
  assert.equal(context.name, "DISCORD_CALL_TILE_POPOUT_test");
});
