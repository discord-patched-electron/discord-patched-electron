"use strict";
// Read presentation from the running client. No account data, stores or network calls.
// Icons remain Discord-owned runtime assets; none are copied into the package.
module.exports = function presentation(request, ui) {
  const cacheKey = Symbol.for("dpe.discordPresentation");
  const cache = globalThis[cacheKey] || (globalThis[cacheKey] = { icons: {}, iconAttempt: 0 });
  const names = {
    settings: ["SettingsIcon"],
    appearance: ["PaintPaletteIcon", "PaintbrushIcon"],
    mods: ["PuzzlePieceIcon"],
    flags: ["FlagIcon"],
    troubleshooting: ["CircleQuestionIcon", "HelpIcon"],
    github: ["GithubIcon", "GitHubIcon"],
    external: ["ExternalLinkIcon", "OpenExternalIcon", "ArrowSquareUpRightIcon"],
    download: ["DownloadIcon"],
    refresh: ["ArrowClockwiseIcon", "RotateClockwiseIcon", "RefreshIcon"],
    applications: ["BrowserIcon", "WindowIcon", "AppsIcon"],
    screen: ["ScreenIcon", "MonitorIcon"],
    check: ["CheckmarkLargeIcon", "CheckmarkIcon"],
    search: ["MagnifyingGlassIcon", "SearchIcon"],
    clear: ["CircleXIcon", "CircleXFilledIcon", "CircleXmarkIcon"],
    trash: ["TrashIcon"],
    close: ["CloseIcon", "XSmallIcon", "XIcon", "XLargeIcon"],
    minimize: ["MinusIcon"],
    maximize: ["WindowIcon"],
    restore: ["WindowMultipleIcon"],
    experimental: ["FlaskIcon", "BeakerIcon"],
    copy: ["CopyIcon"],
    chevron: ["ChevronSmallDownIcon", "ChevronDownSmallIcon", "ChevronDownIcon"],
  };
  const tags = new Set([
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "rect",
    "polygon",
    "polyline",
    "line",
  ]);
  const attributes = new Set([
    "viewBox",
    "d",
    "fill",
    "stroke",
    "strokeWidth",
    "strokeLinecap",
    "strokeLinejoin",
    "fillRule",
    "clipRule",
    "opacity",
    "fillOpacity",
    "strokeOpacity",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "x2",
    "y1",
    "y2",
    "width",
    "height",
    "points",
    "transform",
  ]);
  const escape = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  function serialize(element, depth = 0) {
    if (element == null || typeof element === "boolean") return "";
    if (Array.isArray(element)) return element.map((child) => serialize(child, depth + 1)).join("");
    if (depth > 16 || !tags.has(element.type)) throw Error("Non-SVG icon");
    const props = element.props || {};
    let attrs = "";
    for (const [key, value] of Object.entries(props)) {
      if (!attributes.has(key) || !["string", "number"].includes(typeof value)) continue;
      const attribute =
        key === "viewBox" ? key : key.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
      const color =
        ["fill", "stroke"].includes(key) && !["none", "transparent"].includes(value)
          ? "currentColor"
          : value;
      attrs += ` ${attribute}="${escape(color)}"`;
    }
    return `<${element.type}${attrs}>${serialize(props.children, depth + 1)}</${element.type}>`;
  }
  function fromDOM(node) {
    if (!node || !tags.has(node.localName)) throw Error("Unsupported SVG node");
    const props = {};
    for (const attribute of node.attributes) {
      const name =
        attribute.name === "viewBox"
          ? "viewBox"
          : attribute.name.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      if (attributes.has(name)) props[name] = attribute.value;
    }
    props.children = [...node.children].map(fromDOM);
    return { type: node.localName, props };
  }
  const domIcons = {
    settings:
      '[data-settings-sidebar-item="dpe_openasar"] svg, [data-list-item-id="settings-sidebar___dpe_openasar"] svg',
    appearance: '[data-settings-sidebar-item="appearance_panel"] svg',
    flags: '[data-settings-sidebar-item="system_panel"] svg',
    close:
      '[class*="contentHeader_"] > [role="button"]:last-child svg, [class*="contentHeader_"] > button:last-child svg',
    chevron:
      '[data-mana-component="select"] svg, [role="combobox"] svg, [class*="chevronButton_"] svg',
  };
  for (const [key, selector] of Object.entries(domIcons)) {
    const node = document.querySelector(selector);
    if (node)
      try {
        cache.icons[key] = serialize(fromDOM(node));
      } catch {
        /* Keep prior valid drawing. */
      }
  }
  if (Date.now() - cache.iconAttempt > 5000) {
    cache.iconAttempt = Date.now();
    for (const runtime of ui.runtimes()) {
      const scan = ui.inspect(runtime);
      if (!cache.accessibility)
        cache.accessibility = scan.objects.find((value) => {
          const name = ui.safe(value, "getName");
          return (
            typeof name === "function" && ui.signature(name)?.strings.includes("AccessibilityStore")
          );
        });
      for (const [key, aliases] of Object.entries(names)) {
        if (cache.icons[key]) continue;
        for (const name of aliases) {
          // Only named, loaded, pure SVG exports qualify. Never run arbitrary
          // factory modules or controllers to obtain a drawing.
          let icon = scan.choose(
            name,
            (signature, exports) => exports.has(name) && ui.has(signature, "svg"),
          );
          if (!icon) {
            // Known geometry anchors identify the same pure, loaded native icons
            // when webpack exports have minified names. No drawings are bundled.
            const anchors = {
              applications: ["M20 3a3 3 0 0 1 3 3v12", "h16ZM4 5"],
              screen: ["M5 2a3 3 0 0 0-3 3v8"],
              refresh: ["M21 2a1 1 0 0 1 1 1v6"],
              external: ["M15 2a1 1 0 0 1 1-1h6", "M5 2a3 3 0 0 0-3 3v14"],
              experimental: ["M16 10.49V4a1 1 0 1 0 0-2H8"],
              chevron: ["M3.3 8.3a1 1 0 0 1 1.4 0l7.3 7.29"],
            }[key];
            if (anchors)
              icon = scan.choose(
                key,
                (signature) =>
                  ui.has(signature, "svg") &&
                  anchors.every((anchor) =>
                    signature.strings.some((value) => value.includes(anchor)),
                  ),
              );
          }
          const fn = ui.unwrap(icon);
          if (!fn) continue;
          try {
            const svg = serialize(fn({ width: 24, height: 24, color: "currentColor" }));
            if (svg.startsWith("<svg") && svg.length < 16384) cache.icons[key] = svg;
          } catch {
            /* Unsupported presentation component keeps its accessible fallback. */
          }
          if (cache.icons[key]) break;
        }
      }
    }
  }
  const reducedMotion =
    matchMedia("(prefers-reduced-motion: reduce)").matches ||
    ui.safe(cache.accessibility, "useReducedMotion") === true ||
    document.documentElement.classList.contains("reduce-motion");
  // Read native dimensions independently of our dialog. CSS pixels already
  // include Discord's font scaling, but not browser zoom: never divide a screen
  // measurement by a guessed zoom factor. Retain valid roles across lazy views.
  const geometry = cache.geometry || (cache.geometry = {});
  function length(value, min, max) {
    const match = /^(\d+(?:\.\d+)?)(px|rem)$/.exec(value || "");
    if (!match) return;
    const pixels = Number(match[1]) * (match[2] === "rem" ? 16 : 1);
    return pixels >= min && pixels <= max ? value : undefined;
  }
  function remember(name, value, min, max) {
    const valid = length(value, min, max);
    if (valid) geometry["--dpe-discord-" + name] = valid;
  }
  function measure(node, properties) {
    if (!node || typeof getComputedStyle !== "function") return;
    if (node.getClientRects && !node.getClientRects().length) return;
    const computed = getComputedStyle(node);
    for (const [name, property, min, max] of properties) {
      let value = computed.getPropertyValue(property).trim();
      if (
        ["width", "height"].includes(property) &&
        computed.getPropertyValue("box-sizing") === "content-box" &&
        /^\d+(?:\.\d+)?px$/.test(value)
      ) {
        const sides = property === "width" ? ["left", "right"] : ["top", "bottom"];
        const extra = sides.reduce(
          (sum, side) =>
            sum +
            (parseFloat(computed.getPropertyValue("padding-" + side)) || 0) +
            (parseFloat(computed.getPropertyValue("border-" + side + "-width")) || 0),
          0,
        );
        value = (parseFloat(value) + extra).toFixed(4) + "px";
      }
      remember(name, value, min, max);
    }
  }
  measure(document.documentElement, [["root-font-size", "font-size", 8, 40]]);
  const nativeSidebar = document.querySelector("aside:has([data-settings-sidebar-item])");
  measure(nativeSidebar, [["sidebar-width", "width", 180, 440]]);
  const settingsRoot = nativeSidebar?.parentElement;
  measure(settingsRoot?.querySelector('[class*="contentHeader_"]'), [
    ["content-header-height", "height", 24, 120],
  ]);
  // A real control's computed values include inherited density/theme overrides.
  // Do not use rectangles, which would bake entrance transforms into dimensions.
  measure(settingsRoot?.querySelector('[role="switch"]'), [
    ["switch-width", "width", 28, 96],
    ["switch-height", "height", 16, 48],
  ]);
  for (const [role, prefix, min, max] of [
    ["text-md", "text-md/normal_", 10, 32],
    ["text-sm", "text-sm/normal_", 8, 28],
    ["text-xs", "text-xs/normal_", 8, 24],
    ["heading-xl", "heading-xl/semibold_", 14, 48],
  ])
    measure(
      settingsRoot?.querySelector(`[class*="${prefix}"]`) ||
        document.querySelector(`[class*="${prefix}"]`),
      [
        [role + "-size", "font-size", min, max],
        [role + "-line-height", "line-height", min, max * 2],
      ],
    );
  if (!cache.geometryAttempt || Date.now() - cache.geometryAttempt > 5000) {
    cache.geometryAttempt = Date.now();
    const matches = new Map();
    let visited = 0;
    function record(name, value, min, max) {
      if (!length(value, min, max)) return;
      if (!matches.has(name)) matches.set(name, new Set());
      matches.get(name).add(value);
    }
    function visit(rules, depth = 0) {
      if (depth > 6) return;
      for (const rule of rules || []) {
        if (++visited > 30000) return;
        // Conditional rules are resolved only for the client's current context.
        if (rule.media && !matchMedia(rule.conditionText).matches) continue;
        if (rule.type === 12 && globalThis.CSS?.supports && !CSS.supports(rule.conditionText))
          continue;
        if (rule.cssRules) visit(rule.cssRules, depth + 1);
        const selector = rule.selectorText || "";
        if (!rule.style) continue;
        // Stable semantic prefixes, never a pinned Discord build/hash. Ambiguous
        // declarations are ignored instead of selecting another feature's size.
        if (/^\.size-xl_[a-zA-Z0-9_]+$/.test(selector))
          record("modal-width", rule.style.getPropertyValue("max-width").trim(), 480, 1600);
        if (/^\.auto_[a-zA-Z0-9_]+::-webkit-scrollbar$/.test(selector))
          record("scrollbar-width", rule.style.getPropertyValue("width").trim(), 4, 32);
        if (
          /^\.panel_[a-zA-Z0-9_]+$/.test(selector) &&
          rule.style.getPropertyValue("margin-inline").trim() === "auto" &&
          rule.style.getPropertyValue("position").trim() === "relative"
        ) {
          // The Settings panel has a content-box width. Its native spacing
          // remains live --space-* tokens in our stylesheet, not frozen pixels.
          record(
            "settings-panel-max-width",
            rule.style.getPropertyValue("max-width").trim(),
            360,
            1400,
          );
          record(
            "settings-panel-min-width",
            rule.style.getPropertyValue("min-width").trim(),
            120,
            600,
          );
        }
      }
    }
    for (const sheet of document.styleSheets || []) {
      if (sheet.disabled) continue;
      try {
        visit(sheet.cssRules);
      } catch {
        /* Cross-origin or still loading. */
      }
    }
    for (const [name, values] of matches)
      if (values.size === 1) geometry["--dpe-discord-" + name] = [...values][0];
  }
  if (request?.iconsOnly) return { icons: cache.icons, reducedMotion, tokens: { ...geometry } };
  const node = document.querySelector("#app-mount") || document.body || document.documentElement;
  const style = getComputedStyle(node);
  const tokens = {};
  for (const key of style) {
    if (
      /^--(?:switch-|scrollbar-|background|bg-|text-|header-|interactive-|button-|input-|control-|border-|brand-|primary-|white|black|red-|green-|yellow-|status-|focus-|font-|radius-|spacing-|space-|size-|custom-theme|neutral-|opacity-)/.test(
        key,
      ) ||
      ["--custom-app-top-bar-height", "--custom-zoom"].includes(key)
    ) {
      const value = style.getPropertyValue(key).trim();
      if (value && value.length < 2048) tokens[key] = value;
    }
  }
  Object.assign(tokens, geometry);
  const sidebar = document.querySelector('aside[class*="sidebar"]');
  const content = sidebar?.parentElement?.querySelector('[class*="content_"]');
  if (sidebar) tokens["--dpe-discord-sidebar"] = getComputedStyle(sidebar).backgroundColor;
  if (content) tokens["--dpe-discord-content"] = getComputedStyle(content).backgroundColor;
  const classes = [];
  for (let current = node; current; current = current.parentElement)
    for (const name of current.classList)
      if (/^theme-(?:light|dark|darker|midnight)$/.test(name) && !classes.includes(name))
        classes.push(name);
  if (!cache.fonts) {
    const fonts = [];
    const visit = (rules, base) => {
      for (const rule of rules) {
        if (rule.type === 5) {
          const family = rule.style.getPropertyValue("font-family").replace(/["']/g, "").trim();
          if (!/^(gg sans|Noto Sans|ABC Ginto Normal|ABC Ginto Nord)$/.test(family)) continue;
          for (const match of rule.style
            .getPropertyValue("src")
            .matchAll(/url\(["']?([^"')]+)["']?\)/g)) {
            try {
              fonts.push({
                family,
                weight: rule.style.getPropertyValue("font-weight") || "400",
                style: rule.style.getPropertyValue("font-style") || "normal",
                url: new URL(match[1], base).href,
              });
            } catch {
              /* Bad stylesheet URL. */
            }
          }
        } else if (rule.cssRules) visit(rule.cssRules, base);
      }
    };
    for (const sheet of document.styleSheets)
      try {
        visit(sheet.cssRules, sheet.href || location.href);
      } catch {
        /* Cross-origin sheets are not inspected. */
      }
    cache.fonts = fonts.slice(0, 20);
  }
  return {
    tokens,
    reducedMotion,
    classes,
    theme: classes.includes("theme-light") ? "light" : "dark",
    fontFamily: style.getPropertyValue("--font-primary").trim() || style.fontFamily,
    fonts: cache.fonts,
    icons: cache.icons,
  };
};
