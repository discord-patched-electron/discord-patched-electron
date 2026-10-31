"use strict";
// One page-world runtime shared by native UI adapters. No Node APIs or DOM edits.
module.exports = function discordUI() {
  const key = Symbol.for("dpe.discordUI");
  if (globalThis[key]) return globalThis[key];
  const safe = (object, property) => {
    try {
      return object?.[property];
    } catch {
      return undefined;
    }
  };
  function unwrap(value) {
    const seen = new Set();
    for (let depth = 0; depth < 8 && value && !seen.has(value); depth++) {
      seen.add(value);
      if (typeof value === "function") return value;
      const kind = safe(value, "$$typeof");
      if (kind === Symbol.for("react.memo")) value = safe(value, "type");
      else if (kind === Symbol.for("react.forward_ref")) value = safe(value, "render");
      else return null;
    }
    return null;
  }
  // Tokenize instead of matching minifier punctuation/quotes/variable names.
  // Comments cannot supply evidence and string contents aren't whitespace-stripped.
  function signature(fn) {
    let source;
    try {
      source = Function.prototype.toString.call(fn);
      // Mod-owned callable proxies expose the original source via toString;
      // Function.prototype alone sees only [native code], hiding lazy controls.
      if (source.includes("[native code]")) source = fn.toString();
      if (typeof source !== "string") return null;
    } catch {
      return null;
    }
    if (source.length > 200000) return null;
    const words = new Set(),
      strings = [];
    const tokens =
      source.match(
        /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\*[\s\S]*?\*\/|\/\/[^\n\r]*|[A-Za-z_$][\w$]*|[^\s]/g,
      ) || [];
    const normalized = [];
    for (const token of tokens) {
      if (token.startsWith("//") || token.startsWith("/*")) continue;
      if (token[0] === '"' || token[0] === "'") {
        const value = token
          .slice(1, -1)
          .replace(/\\(?:u([\da-f]{4})|x([\da-f]{2})|([\s\S]))/gi, (_m, u, x, c) =>
            u || x
              ? String.fromCharCode(parseInt(u || x, 16))
              : ({ n: "\n", r: "\r", t: "\t" }[c] ?? c),
          );
        strings.push(value);
        words.add(value);
        normalized.push(JSON.stringify(value));
      } else {
        words.add(token);
        normalized.push(token);
      }
    }
    return { source, words, strings, normalized: normalized.join("") };
  }
  const has = (s, ...words) => words.every((word) => s.words.has(word));

  const watchers = new Map(),
    runtimes = new Set(),
    chunkArrays = new WeakMap();
  const wrapped = new WeakMap();
  const modListeners = new WeakMap();
  function notifyModule(exports, runtime, id) {
    try {
      const factory = runtime.m?.[id];
      if (typeof factory !== "function") return;
      const source = factory.toString();
      for (const watcher of watchers.values())
        if (watcher.match(source)) notify(watcher, exports, runtime);
    } catch {
      /* A mod may hide unsupported modules from discovery. */
    }
  }
  function useModListener(runtime) {
    // Vencord/Equicord recompile source factories. Assigning a wrapper to their
    // proxied module table can silently discard it during patching. Subscribe
    // after their patches execute, while exports are still reaching the caller.
    const webpack = safe(safe(globalThis, "Vencord"), "Webpack");
    const listeners = safe(webpack, "moduleListeners");
    if (safe(webpack, "wreq") !== runtime || typeof safe(listeners, "add") !== "function")
      return false;
    if (modListeners.get(runtime) !== listeners) {
      listeners.add((exports, id) => notifyModule(exports, runtime, id));
      modListeners.set(runtime, listeners);
    }
    return true;
  }
  function notify(watcher, exports, runtime) {
    try {
      watcher.visit(exports, runtime);
    } catch (error) {
      console.warn("[DPE Discord UI] Adapter failed", error);
    }
  }
  function observe(runtime, ids) {
    if (!runtime?.c) return;
    runtimes.add(runtime);
    if (!watchers.size) return;
    let moduleIds;
    try {
      moduleIds = ids || Object.keys(runtime.m || {});
    } catch {
      return;
    }
    if (useModListener(runtime)) {
      for (const id of moduleIds) {
        const cached = safe(runtime.c, id);
        if (cached) notifyModule(safe(cached, "exports"), runtime, id);
      }
      return;
    }
    for (const id of moduleIds) {
      try {
        const factory = runtime.m[id];
        if (typeof factory !== "function") continue;
        let record = wrapped.get(factory);
        const original = record?.original || factory;
        const source = original.toString();
        const matches = [...watchers.values()].filter((watcher) => watcher.match(source));
        if (!matches.length) continue;
        if (!record) {
          record = { original, watchers: new Set() };
          const wrapper = function (...args) {
            const result = Reflect.apply(original, this, args);
            for (const watcher of record.watchers) notify(watcher, args[0].exports, runtime);
            return result;
          };
          wrapper.toString = () => original.toString();
          wrapped.set(wrapper, record);
          runtime.m[id] = wrapper;
        }
        for (const watcher of matches) {
          if (record.watchers.has(watcher)) continue;
          record.watchers.add(watcher);
          if (runtime.c[id]) notify(watcher, runtime.c[id].exports, runtime);
        }
      } catch {
        /* An unknown or read-only factory must not stop Discord loading. */
      }
    }
  }
  function discover(key = "dpe-ui") {
    const names = [
      "webpackChunkdiscord_app",
      ...Object.getOwnPropertyNames(globalThis).filter(
        (name) => name.startsWith("webpackChunk") && name !== "webpackChunkdiscord_app",
      ),
    ].slice(0, 12);
    for (const name of names) {
      const chunks = safe(globalThis, name);
      if (!Array.isArray(chunks)) continue;
      if (watchers.size && chunkArrays.get(chunks) !== chunks.push) {
        const push = chunks.push;
        chunks.push = function (...items) {
          const incoming = items.map((chunk) => {
            if (!Array.isArray(chunk) || !Array.isArray(chunk[0])) return chunk;
            const callback = chunk[2];
            return [
              chunk[0],
              chunk[1],
              (runtime) => {
                observe(runtime, Object.keys(chunk[1] || {}));
                return typeof callback === "function" ? callback(runtime) : undefined;
              },
            ];
          });
          return Reflect.apply(push, this, incoming);
        };
        chunkArrays.set(chunks, chunks.push);
      }
      // Remove the actual inserted probe, including the wrapper produced above.
      const chunkId = key + "-" + name;
      try {
        chunks.push([[chunkId], {}, observe]);
      } catch {
        /* Another bundle's loader. */
      } finally {
        const index = chunks.findIndex((chunk) => chunk?.[0]?.[0] === chunkId);
        if (index >= 0) chunks.splice(index, 1);
      }
    }
    return [...runtimes].slice(0, 12);
  }
  let waiting = false;
  function awaitRuntime(attempt = 0) {
    if (runtimes.size || attempt >= 20 || typeof setTimeout !== "function") {
      waiting = false;
      return;
    }
    waiting = true;
    setTimeout(() => {
      discover();
      awaitRuntime(attempt + 1);
    }, 1000);
  }
  function watch(id, match, visit) {
    if (watchers.has(id)) return;
    watchers.set(id, { match, visit });
    discover(id);
    for (const runtime of runtimes) observe(runtime);
    if (!waiting) awaitRuntime();
  }
  function inspect(runtime) {
    const candidates = [],
      reacts = [],
      seen = new Set(),
      functions = new Map();
    let inspected = 0;
    function collect(value, name = "", depth = 0) {
      if (
        !value ||
        (typeof value !== "object" && typeof value !== "function") ||
        value === globalThis ||
        inspected++ > 100000
      )
        return;
      if (safe(value, Symbol.toStringTag) === "IntlMessagesProxy") return;
      const fn = unwrap(value);
      if (fn) {
        let candidate = functions.get(fn);
        if (!candidate) {
          const sig = signature(fn);
          if (sig) {
            candidate = { value, fn, sig, names: new Set() };
            functions.set(fn, candidate);
            candidates.push(candidate);
          }
        }
        candidate?.names.add(name);
      }
      if (seen.has(value)) return;
      seen.add(value);
      if (
        typeof safe(value, "version") === "string" &&
        ["createElement", "Component", "useState", "useEffect", "useLayoutEffect", "useRef"].every(
          (k) => typeof safe(value, k) === "function",
        )
      ) {
        if (!reacts.some((react) => react.useState === value.useState)) reacts.push(value);
      }
      if (depth >= 2 || typeof value !== "object" || safe(value, "$$typeof")) return;
      let keys;
      try {
        keys = Object.getOwnPropertyNames(value);
      } catch {
        return;
      }
      // Descend into namespace wrappers; avoid giant constant/data trees.
      if (depth > 0 && keys.length > 128) return;
      for (const key of keys) collect(safe(value, key), key, depth + 1);
    }
    let entries;
    try {
      entries = Object.values(runtime.c);
    } catch {
      entries = [];
    }
    for (const entry of entries) collect(safe(entry, "exports"));
    const ambiguous = [];
    function choose(name, test, allowEquivalent = false) {
      let matches = candidates.filter((c) => test(c.sig, c.names));
      // Stable export names may disambiguate, but never bypass structural checks.
      const named = matches.filter((c) => c.names.has(name));
      if (named.length) matches = named;
      // Reject enclosing implementations; don't guess by source length.
      matches = matches.filter(
        (c) =>
          !matches.some(
            (other) =>
              other !== c &&
              c.sig.normalized !== other.sig.normalized &&
              c.sig.normalized.includes(other.sig.normalized),
          ),
      );
      if (
        allowEquivalent &&
        matches.length &&
        matches.every((c) => c.sig.normalized === matches[0].sig.normalized)
      )
        matches = matches.slice(0, 1);
      if (matches.length === 1) return matches[0].value;
      if (matches.length > 1) ambiguous.push(name);
      return undefined;
    }

    return {
      collect,
      choose,
      candidates,
      objects: [...seen],
      ambiguous,
      React: reacts.length === 1 ? reacts[0] : undefined,
    };
  }
  const api = { safe, unwrap, signature, has, runtimes: discover, inspect, watch };
  globalThis[key] = api;
  return api;
};
