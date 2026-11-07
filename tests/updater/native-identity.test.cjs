"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { matches } = require("../../integration/video/dpe-native-identity.cjs");
function fixture(run) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-native-identity-"));
  try {
    run(path.join(directory, "voice.node"));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
test("whole-module hashes use bounded chunks and stable identities share verification", () =>
  fixture((file) => {
    const bytes = Buffer.alloc(3 * 1024 * 1024 + 17, 0xa5);
    const expected = digest(bytes);
    fs.writeFileSync(file, bytes);
    const read = fs.readSync;
    let calls = 0;
    fs.readSync = (...args) => {
      calls++;
      assert.ok(args[1].byteLength <= 65536);
      assert.ok(args[3] <= 65536);
      return read(...args);
    };
    try {
      assert.equal(matches(file, expected), true);
      assert.ok(calls > 40);
      const before = calls;
      assert.equal(matches(file, expected), true);
      assert.equal(matches(file, "0".repeat(64)), false);
      assert.equal(calls, before);
      const alias = file + ".hardlink";
      fs.linkSync(file, alias);
      // link changes ctime, so the first verification refreshes the identity.
      assert.equal(matches(alias, expected), true);
      const shared = calls;
      assert.equal(matches(file, expected), true);
      assert.equal(calls, shared);
    } finally {
      fs.readSync = read;
    }
  }));
test("same-size replacements and writes invalidate the cached digest", () =>
  fixture((file) => {
    fs.writeFileSync(file, "original native bytes");
    const expected = digest("original native bytes");
    assert.equal(matches(file, expected), true);
    const times = fs.statSync(file);
    fs.writeFileSync(file, "modified native bytes");
    fs.utimesSync(file, times.atime, times.mtime);
    assert.equal(matches(file, expected), false);
    fs.writeFileSync(file + ".new", "original native bytes");
    fs.renameSync(file + ".new", file);
    assert.equal(matches(file, expected), true);
  }));
test("mid-read mutations and failures close descriptors and never populate the cache", () =>
  fixture((file) => {
    const bytes = Buffer.alloc(2 * 1024 * 1024, 1);
    fs.writeFileSync(file, bytes);
    const read = fs.readSync;
    let selected,
      changed = false;
    fs.readSync = (...args) => {
      selected = args[0];
      const count = read(...args);
      if (!changed) {
        changed = true;
        fs.appendFileSync(file, "changed");
      }
      return count;
    };
    try {
      assert.equal(matches(file, digest(bytes)), false);
    } finally {
      fs.readSync = read;
    }
    assert.throws(() => fs.fstatSync(selected), { code: "EBADF" });
    assert.equal(matches(file, digest(bytes)), false);
    fs.readSync = (...args) => {
      selected = args[0];
      throw Error("synthetic read failure");
    };
    try {
      assert.equal(matches(file, digest("another version")), false);
    } finally {
      fs.readSync = read;
    }
    assert.throws(() => fs.fstatSync(selected), { code: "EBADF" });
  }));
test("empty, oversized, symlink and unknown inputs fail before native use", () =>
  fixture((file) => {
    assert.equal(matches(file, "bad"), false);
    assert.equal(matches(file, "0".repeat(64)), false);
    fs.writeFileSync(file, "");
    assert.equal(matches(file, digest("")), false);
    const fd = fs.openSync(file, "w");
    fs.ftruncateSync(fd, 513 * 1024 * 1024);
    fs.closeSync(fd);
    assert.equal(matches(file, "0".repeat(64)), false);
    fs.writeFileSync(file, "native");
    fs.symlinkSync(file, file + ".link");
    assert.equal(matches(file + ".link", digest("native")), false);
    assert.equal(matches(file, digest("native")), true);
  }));
