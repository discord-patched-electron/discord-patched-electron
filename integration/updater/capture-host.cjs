"use strict";
// A desktop user service can perform the existing polkit flow when Chromium's
// process restriction prevents its children from using pkexec. No flag is
// cleared, no permission is granted here, and no service runs as root.
const fs = require("node:fs");
const path = require("node:path");
const { execFile, spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const RUN = "/usr/bin/systemd-run";
const CONTROL = "/usr/bin/systemctl";
const CAT = "/usr/bin/cat";
const VARIABLES = [
  "XDG_RUNTIME_DIR",
  "DBUS_SESSION_BUS_ADDRESS",
  "PIPEWIRE_REMOTE",
  "PIPEWIRE_RUNTIME_DIR",
  "WAYLAND_DISPLAY",
  "DISPLAY",
  "XAUTHORITY",
  "DRI_PRIME",
  "DPE_KMS_AUTH_SOCKET",
  "DPE_KMS_AUTH_TOKEN",
];
let probe,
  expires = 0;

function restricted() {
  try {
    return /^NoNewPrivs:\s+1$/m.test(fs.readFileSync("/proc/self/status", "utf8"));
  } catch {
    return false; // Permission status separately fails closed on unreadable proc.
  }
}
function trusted(file) {
  try {
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.uid !== 0 || info.mode & 0o022) return false;
    for (let parent = path.dirname(file); ; parent = path.dirname(parent)) {
      const directory = fs.lstatSync(parent);
      if (!directory.isDirectory() || directory.uid !== 0 || directory.mode & 0o022) return false;
      if (parent === path.dirname(parent)) break;
    }
    fs.accessSync(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}
function environment(input = process.env) {
  const value = { PATH: "/usr/bin", LANG: "C" };
  for (const name of VARIABLES) if (input[name]) value[name] = input[name];
  value.XDG_RUNTIME_DIR ||= `/run/user/${process.getuid()}`;
  return value;
}
function argumentsFor(file, args, env, unit, runtimeSeconds) {
  return [
    "--user",
    "--pipe",
    "--wait",
    "--collect",
    "--quiet",
    "--expand-environment=no",
    ...(unit ? [`--unit=${unit}`] : []),
    ...(runtimeSeconds ? [`--property=RuntimeMaxSec=${runtimeSeconds}`] : []),
    ...Object.entries(environment(env)).map(([key, value]) => `--setenv=${key}=${value}`),
    "--",
    file,
    ...args,
  ];
}
function available() {
  if (probe && Date.now() < expires) return probe;
  expires = Date.now() + 10000;
  probe = new Promise((resolve) => {
    if (![RUN, CONTROL, CAT].every(trusted)) return resolve(false);
    execFile(
      RUN,
      argumentsFor(CAT, ["/proc/self/status"], process.env),
      { env: environment(), timeout: 3000, maxBuffer: 16384 },
      (error, stdout) => resolve(!error && /^NoNewPrivs:\s+0$/m.test(stdout || "")),
    );
  });
  return probe;
}
function execute(file, args, options, callback) {
  return execFile(
    RUN,
    argumentsFor(
      file,
      args,
      options.env || process.env,
      undefined,
      Math.ceil((options.timeout || 120000) / 1000),
    ),
    { ...options, env: environment() },
    callback,
  );
}
function worker(file, args, options) {
  if (!restricted()) return spawn(file, args, options);
  const unit = `dpe-capture-${process.pid}-${randomUUID()}.service`;
  const child = spawn(RUN, argumentsFor(file, args, options.env, unit), {
    ...options,
    env: environment(options.env),
  });
  let stopped = false;
  child.stopHostCapture = () => {
    if (stopped) return;
    stopped = true;
    // The service also exits on stdin EOF if the browser crashes. Explicit
    // stop handles a client disappearing while systemd still starts its unit.
    execFile(
      CONTROL,
      ["--user", "--no-block", "stop", unit],
      { env: environment(), timeout: 3000, maxBuffer: 4096 },
      () => {},
    );
  };
  return child;
}
module.exports = { restricted, available, execFile: execute, spawn: worker };
