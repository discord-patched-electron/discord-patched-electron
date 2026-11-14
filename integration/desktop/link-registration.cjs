// Linux protocol ownership is the resolved desktop command, not its Discord name.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { createHash } = require("node:crypto");
const { execFileSync } = require("node:child_process");

function createRegistration({ env = process.env, run = execFileSync } = {}) {
  const home = env.HOME || os.homedir();
  const data = env.XDG_DATA_HOME || path.join(home, ".local/share");
  const roots = [data, ...(env.XDG_DATA_DIRS || "/usr/local/share:/usr/share").split(":")];
  let command;
  try {
    command = env.DPE_LINK_COMMAND ? JSON.parse(env.DPE_LINK_COMMAND) : [env.DPE_LAUNCHER];
  } catch {
    command = [];
  }
  const valid =
    Array.isArray(command) &&
    command.length > 0 &&
    command.every(
      (part) =>
        typeof part === "string" &&
        part.length &&
        ![...part].some(
          (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
        ),
    ) &&
    path.isAbsolute(command[0]);
  // Desktop values have two escaping layers: string escapes, then Exec quoting.
  const quote = (value) =>
    '"' +
    value
      .replace(/%/g, "%%")
      .replace(/[\\"`$]/g, "\\$&")
      .replace(/\\/g, "\\\\") +
    '"';
  // GLib checks the executable before expanding %%: keep paths containing %
  // in the argument list of env, which executes them without a shell.
  const expected = valid ? "/usr/bin/env -- " + command.map(quote).join(" ") + " %U" : "";
  const id = valid
    ? `dpe-links-${createHash("sha256").update(JSON.stringify(command)).digest("hex").slice(0, 24)}.desktop`
    : "";
  function query() {
    try {
      return run("xdg-mime", ["query", "default", "x-scheme-handler/discord"], {
        encoding: "utf8",
        timeout: 5000,
      }).trim();
    } catch {
      return null;
    }
  }
  function current() {
    const handler = query();
    if (!valid || !handler || path.basename(handler) !== handler || !fs.existsSync(command[0]))
      return false;
    for (const root of roots) {
      const file = path.join(root, "applications", handler);
      try {
        const text = fs.readFileSync(file, "utf8");
        const section = text.split(/^\[Desktop Entry\]\s*$/m)[1]?.split(/^\[/m)[0];
        if (
          !section ||
          !/^Type=Application\s*$/m.test(section) ||
          /^(?:Hidden|DBusActivatable)=true\s*$/m.test(section)
        )
          return false;
        const exec = section.match(/^Exec=(.*)$/m)?.[1];
        // Accept a conventional installed entry as well as our exact generated entry.
        if (exec === expected) return true;
        if (command.length !== 1) return false;
        const executable = exec?.match(/^(?:"([^"\\%]+)"|([^\s"\\%]+)) %[uU]$/);
        if (!executable) return false;
        const target = executable[1] || executable[2];
        if (!path.isAbsolute(target)) return false;
        return fs.realpathSync(target) === fs.realpathSync(command[0]);
      } catch (error) {
        if (error.code !== "ENOENT") return false;
        if (fs.existsSync(file)) return false;
      }
    }
    return false;
  }
  function claim() {
    if (current()) return;
    if (!valid || !fs.existsSync(command[0]))
      throw new Error("Could not determine this app's launcher for Discord links.");
    const directory = path.join(data, "applications");
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(
      path.join(directory, id),
      `[Desktop Entry]\nType=Application\nName=Discord (DPE)\nExec=${expected}\nTerminal=false\nNoDisplay=true\nMimeType=x-scheme-handler/discord;\n`,
      { mode: 0o644 },
    );
    try {
      run("xdg-mime", ["default", id, "x-scheme-handler/discord"], { timeout: 5000 });
    } catch {
      throw new Error("Could not make this app open Discord links.");
    }
    if (!current())
      throw new Error(
        "Discord link registration did not take effect. Check your default applications.",
      );
  }
  return { current, claim, elsewhere: () => !current() && query() !== "" };
}
module.exports = { createRegistration };
