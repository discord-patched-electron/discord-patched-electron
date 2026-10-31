"use strict";
// Audio-specific contracts; runtime access and identity checks are shared.
module.exports = function discoverPicker(key, ui) {
  const { signature, has } = ui;
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
  let best = { components: {}, found: {}, reason: "runtime-unavailable" };
  for (const runtime of ui.runtimes(key)) {
    const { React, collect, choose, ambiguous } = ui.inspect(runtime);
    const components = { React };
    // Only a runtime containing React and both modal operations can host us.
    const openModal = choose("openModal", specs.openModal),
      closeModal = choose("closeModal", specs.closeModal);
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
        components[name] = choose(name, specs[name], name === "Scroller");
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
          components.SegmentedControl = choose("SegmentedControl", specs.SegmentedControl);
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
