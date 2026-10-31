"use strict";
// Serialized into the page world. This adapter never calls component functions.
module.exports = function discoverPicker(key) {
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
  const specs = {
    ModalRoot: (s) =>
      has(s, "transitionState", "onClose", "children") &&
      (has(s, "contentOutsideContainer", "fullScreenOnMobile") ||
        has(s, "paddingSize", "maxHeight", "dismissable")),
    Button: (s) =>
      has(s, "buttonRef", "disabled", "onClick") &&
      (has(s, "loadingStartedLabel", "variant") ||
        has(s, "data-mana-component", "button", "fullWidth", "icon", "text")),
    SearchBar: (s) =>
      has(s, "query", "onChange", "onClear") &&
      (has(s, "searchbox", "placeholder") ||
        has(s, "MagnifyingGlassIcon", "inputProps", "focusProps")),
    SegmentedControl: (s) =>
      has(s, "options", "value", "onChange", "optionClassName") &&
      (has(s, "scrollToStart", "tooltipAriaLabel") || has(s, "navigator", "pill", "tablist")),
    Clickable: (s) => has(s, "ignoreKeyPress", "innerRef", "onClick", "renderNonInteractive"),
    Scroller: (s) =>
      has(s, "scrollbarGutter", "disableFocusRingScope", "children", "dir", "orientation") &&
      !["renderRow", "sections", "rowHeight", "useImperativeHandle"].some((word) =>
        s.words.has(word),
      ),
    openModal: (s) =>
      has(
        s,
        "modalKey",
        "onCloseRequest",
        "onCloseCallback",
        "setState",
        "render",
        "stackingBehavior",
      ),
    closeModal: (s) =>
      has(s, "onCloseCallback", "getState", "key") &&
      (has(s, "find", "filter", "setState") || has(s, "delete", "setState")),
  };
  const runtimes = [];
  // Support webpack's standard prefix if Discord renames its chunk global.
  const names = [
    "webpackChunkdiscord_app",
    ...Object.getOwnPropertyNames(globalThis).filter(
      (name) => name.startsWith("webpackChunk") && name !== "webpackChunkdiscord_app",
    ),
  ].slice(0, 12);
  for (const name of names) {
    const chunks = safe(globalThis, name);
    if (!Array.isArray(chunks)) continue;
    const chunk = [
      [key + "-" + name],
      {},
      (runtime) => {
        if (runtime?.c && !runtimes.includes(runtime)) runtimes.push(runtime);
      },
    ];
    try {
      chunks.push(chunk);
    } catch {
      /* Another bundle's chunk loader. */
    } finally {
      const index = chunks.indexOf(chunk);
      if (index >= 0) chunks.splice(index, 1);
    }
  }
  let best = { components: {}, found: {}, reason: "runtime-unavailable" };
  for (const runtime of runtimes.slice(0, 12)) {
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
      continue;
    }
    for (const entry of entries) collect(safe(entry, "exports"));
    const ambiguous = [];
    function choose(name, test = specs[name], allowEquivalent = false) {
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
    const React = reacts.length === 1 ? reacts[0] : undefined;
    const components = { React };
    // Only a runtime containing React and both modal operations can host us.
    const openModal = choose("openModal"),
      closeModal = choose("closeModal");
    if (!React || !openModal || !closeModal) {
      if (!Object.keys(best.found).length)
        best = {
          components: {},
          found: { React: !!React, openModal: !!openModal, closeModal: !!closeModal },
          reason: ambiguous.length ? "ambiguous-components" : "no-compatible-runtime",
          ambiguous,
        };
      continue;
    }
    components.api = { openModal, closeModal };
    for (const name of Object.keys(specs))
      if (!["openModal", "closeModal"].includes(name))
        components[name] = choose(name, undefined, name === "Scroller");
    if (!components.Scroller)
      components.Scroller = choose(
        "Scroller",
        (s) =>
          has(
            s,
            "scrollbarGutter",
            "disableFocusRingScope",
            "children",
            "dir",
            "orientation",
            "useImperativeHandle",
            "getScrollerNode",
          ) && !["renderRow", "sections", "rowHeight"].some((word) => s.words.has(word)),
        true,
      );
    if (
      !components.SegmentedControl &&
      !ambiguous.includes("SegmentedControl") &&
      typeof runtime === "function"
    ) {
      // Initialize only the small, already-registered presentation module.
      // Never load chunks, controllers, source enumerators, or capture hooks.
      let factories = [];
      try {
        factories = Object.entries(runtime.m || {}).filter(([, fn]) => {
          const s = signature(fn);
          return (
            s &&
            s.source.length < 16000 &&
            specs.SegmentedControl(s) &&
            ![
              "getDisplayMedia",
              "getUserMedia",
              "getDesktopSources",
              "getDesktopSource",
              "setGoLiveSource",
              "fetchSources",
            ].some((word) => s.words.has(word)) &&
            has(s, "tooltipAriaLabel", "scrollToEnd")
          );
        });
      } catch {
        /* Unknown loader. */
      }
      if (factories.length === 1)
        try {
          collect(runtime(factories[0][0]));
          components.SegmentedControl = choose("SegmentedControl");
        } catch {
          /* Original picker remains available. */
        }
    }
    const icon = (name, exportNames, paths) =>
      choose(
        name,
        (s, names) =>
          has(s, "svg") &&
          (exportNames.some((n) => names.has(n)) ||
            paths.every((path) => s.strings.some((value) => value.includes(path)))),
      );
    components.AppIcon = icon("AppIcon", ["BrowserIcon"], ["M20 3a3 3 0 0 1 3 3v12", "h16ZM4 5"]);
    components.ScreenIcon = icon("ScreenIcon", ["ScreenIcon"], ["M5 2a3 3 0 0 0-3 3v8"]);
    components.RefreshIcon = icon("RefreshIcon", ["RefreshIcon"], ["M21 2a1 1 0 0 1 1 1v6"]);
    const found = Object.fromEntries(
      Object.entries(components).map(([name, value]) => [name, !!value]),
    );
    const required = [
      "React",
      "api",
      "ModalRoot",
      "Button",
      "SearchBar",
      "SegmentedControl",
      "Clickable",
      "Scroller",
    ];
    const complete = required.every((name) => found[name]);
    const unresolved = required.filter((name) => !found[name] && ambiguous.includes(name));
    const result = {
      components,
      found,
      reason: complete
        ? "ready"
        : unresolved.length
          ? "ambiguous-components"
          : "missing-components",
      ambiguous: unresolved,
    };
    if (complete) return result;
    if (
      Object.values(found).filter(Boolean).length > Object.values(best.found).filter(Boolean).length
    )
      best = result;
  }
  return best;
};
