"use strict";
// Whole-module verification without retaining a module-sized Node Buffer.
const fs = require("node:fs");
const { Buffer } = require("node:buffer");
const { createHash } = require("node:crypto");
const cache = new Map();
const MAXIMUM = 512n * 1024n * 1024n;
const CHUNK = 64 * 1024;
function identity(info) {
  if (!info.isFile() || info.size < 1n || info.size > MAXIMUM) return null;
  return [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].join("/");
}
function matches(filename, expected) {
  if (typeof expected !== "string" || !/^[a-f0-9]{64}$/.test(expected)) return false;
  let fd;
  try {
    fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const before = fs.fstatSync(fd, { bigint: true });
    const key = identity(before);
    if (!key) return false;
    let digest = cache.get(key);
    if (!digest) {
      const hash = createHash("sha256");
      const bytes = Buffer.allocUnsafe(CHUNK);
      const size = Number(before.size);
      let position = 0;
      while (position < size) {
        const count = fs.readSync(fd, bytes, 0, Math.min(CHUNK, size - position), position);
        if (!count) return false;
        hash.update(bytes.subarray(0, count));
        position += count;
      }
      if (fs.readSync(fd, bytes, 0, 1, size)) return false;
      digest = hash.digest("hex");
    }
    // An open descriptor pins the file; verify that neither it nor its pathname
    // changed while reading. Cache only stable immutable-file identities.
    if (
      identity(fs.fstatSync(fd, { bigint: true })) !== key ||
      identity(fs.lstatSync(filename, { bigint: true })) !== key
    )
      return false;
    cache.delete(key);
    cache.set(key, digest);
    if (cache.size > 32) cache.delete(cache.keys().next().value);
    return digest === expected;
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}
module.exports = { matches };
