// Read only layout gaps/struts from the active Niri config. No config writes,
// commands from the config, window rules or bar-specific integration.
const fs = require("node:fs");
const path = require("node:path");
function nodes(source) {
  const tokens = [];
  for (let i = 0; i < source.length;) {
    const c = source[i];
    if (c === "\n" || c === ";" || c === "{" || c === "}") {
      tokens.push(c);
      i++;
      continue;
    }
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (source.startsWith("//", i)) {
      while (i < source.length && source[i] !== "\n") i++;
      continue;
    }
    if (source.startsWith("/*", i)) {
      let depth = 1;
      i += 2;
      while (i < source.length && depth) {
        if (source.startsWith("/*", i)) {
          depth++;
          i += 2;
        } else if (source.startsWith("*/", i)) {
          depth--;
          i += 2;
        } else i++;
      }
      if (depth) throw Error("Unclosed comment");
      continue;
    }
    if (source.startsWith("/-", i)) {
      tokens.push("/-");
      i += 2;
      continue;
    }
    const raw = source.slice(i).match(/^r(#+)?"/);
    if (c === '"' || raw) {
      const start = i;
      const delimiter = raw ? '"' + (raw[1] || "") : '"';
      i += raw ? raw[0].length : 1;
      const content = i;
      while (i < source.length && !source.startsWith(delimiter, i)) {
        if (!raw && source[i] === "\\") i++;
        i++;
      }
      if (i >= source.length) throw Error("Unclosed string");
      let value = source.slice(content, i);
      if (!raw)
        try {
          value = JSON.parse(source.slice(start, i + 1));
        } catch {
          value = value.replace(/\\(["\\])/g, "$1");
        }
      tokens.push({ value });
      i += delimiter.length;
      continue;
    }
    const start = i;
    while (
      i < source.length &&
      !/[\s{};"]/.test(source[i]) &&
      !source.startsWith("//", i) &&
      !source.startsWith("/*", i)
    )
      i++;
    if (i === start) throw Error("Invalid token");
    tokens.push({ value: source.slice(start, i) });
    if (tokens.length > 100000) throw Error("Oversized config");
  }
  let index = 0;
  function parse(depth = 0) {
    if (depth > 64) throw Error("Deep config");
    const result = [];
    while (index < tokens.length) {
      if (tokens[index] === "}") {
        index++;
        return result;
      }
      if (tokens[index] === "\n" || tokens[index] === ";") {
        index++;
        continue;
      }
      const disabled = tokens[index] === "/-";
      if (disabled) index++;
      const name = tokens[index++];
      if (!name || typeof name !== "object") throw Error("Invalid node");
      const node = { name: name.value, args: [], children: [] };
      while (index < tokens.length) {
        const token = tokens[index];
        if (token === "\n" || token === ";" || token === "}") break;
        index++;
        if (token === "{") {
          node.children = parse(depth + 1);
          break;
        }
        if (token === "/-") {
          if (tokens[index] === "{") {
            index++;
            parse(depth + 1);
          } else index++;
          continue;
        }
        node.args.push(token.value);
      }
      if (!disabled) result.push(node);
    }
    return result;
  }
  return parse();
}
function merge(layout, current) {
  const result = { ...current };
  const number = (node) => {
    const value = Number(String(node.args[0]).replaceAll("_", ""));
    return Number.isFinite(value) && Math.abs(value) <= 65535 ? value : undefined;
  };
  for (const node of layout.children) {
    if (node.name === "gaps") {
      const value = number(node);
      if (value >= 0) result.gaps = value;
    }
    if (node.name === "struts") {
      result.struts = { left: 0, right: 0, top: 0, bottom: 0 };
      for (const side of node.children)
        if (Object.hasOwn(result.struts, side.name)) {
          const value = number(side);
          if (value !== undefined) result.struts[side.name] = value;
        }
    }
  }
  return result;
}
function load(
  filename,
  workspace,
  read = (file) => {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > 262144) throw Error("Oversized config");
    return fs.readFileSync(file, "utf8");
  },
) {
  let remaining = 1048576,
    count = 0;
  const active = new Set();
  const expanded = [];
  function visit(file, depth = 0) {
    if (depth > 8 || ++count > 64 || active.has(file)) throw Error("Recursive config");
    const source = read(file);
    remaining -= source.length;
    if (remaining < 0) throw Error("Oversized config");
    active.add(file);
    for (const node of nodes(source)) {
      if (node.name === "include" && typeof node.args[0] === "string") {
        const include = node.args[0].startsWith("~/")
          ? path.join(process.env.HOME || "", node.args[0].slice(2))
          : path.resolve(path.dirname(file), node.args[0]);
        visit(include, depth + 1);
      } else expanded.push(node);
    }
    active.delete(file);
  }
  visit(path.resolve(filename));
  let result = { gaps: 16, struts: { left: 0, right: 0, top: 0, bottom: 0 } };
  for (const node of expanded) if (node.name === "layout") result = merge(node, result);
  for (const node of expanded)
    if (node.name === "workspace" && node.args[0] === workspace)
      for (const layout of node.children)
        if (layout.name === "layout") result = merge(layout, result);
  return result;
}
function filename(env = process.env) {
  if (env.NIRI_CONFIG) return env.NIRI_CONFIG;
  const pid = env.NIRI_SOCKET?.match(/\.(\d+)\.sock$/)?.[1];
  if (pid)
    try {
      if (
        fs.statSync(`/proc/${pid}`).uid === process.getuid() &&
        path.basename(fs.readlinkSync(`/proc/${pid}/exe`)) === "niri"
      ) {
        const args = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0");
        const option = args.findIndex((value) => value === "-c" || value === "--config");
        if (option >= 0 && args[option + 1])
          return path.resolve(fs.readlinkSync(`/proc/${pid}/cwd`), args[option + 1]);
        const inline = args.find((value) => value.startsWith("--config="));
        if (inline) return path.resolve(fs.readlinkSync(`/proc/${pid}/cwd`), inline.slice(9));
      }
    } catch {}
  const file = path.join(
    env.XDG_CONFIG_HOME || path.join(env.HOME || "", ".config"),
    "niri/config.kdl",
  );
  return fs.existsSync(file) ? file : "/etc/niri/config.kdl";
}
let cache;
function insets(workspace, env = process.env) {
  try {
    const file = filename(env);
    const key = file + "\0" + (workspace || "");
    if (!cache || cache.key !== key || Date.now() - cache.time > 2000)
      cache = { key, time: Date.now(), value: load(file, workspace) };
    return Object.fromEntries(
      ["left", "right", "top", "bottom"].map((side) => [
        side,
        Math.max(0, cache.value.gaps + cache.value.struts[side] - 16),
      ]),
    );
  } catch {
    return { left: 0, right: 0, top: 0, bottom: 0 };
  }
}
module.exports = { load, insets };
