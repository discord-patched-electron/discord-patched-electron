"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createResolver } = require("./app-icons.cjs");

// The native voice module runs in Discord's preload context. Keep the callback
// here: page scripts get no Node bridge, IPC channel, or arbitrary PID API.
function available(document = globalThis.document) {
  return Boolean(
    document?.body &&
    document.location?.protocol === "https:" &&
    ["discord.com", "canary.discord.com", "ptb.discord.com"].includes(document.location.hostname),
  );
}

// Rendering factories are also imported by the isolated component stress page.
// They accept fixture data, never production stores or native capture handles.
function createControls(
  document,
  fallbacks = require(
    require.resolve("./discord-ui/icons.cjs", { paths: [__dirname, path.dirname(__dirname)] }),
  )(),
  searchView = require("./search-view.cjs"),
  uiRuntime = require(
    require.resolve("./discord-ui/ui-runtime.cjs", { paths: [__dirname, path.dirname(__dirname)] }),
  ),
) {
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function icon(kind) {
    const parsed = new document.defaultView.DOMParser().parseFromString(
      fallbacks[kind],
      "image/svg+xml",
    );
    const svg = document.importNode(parsed.documentElement, true);
    svg.setAttribute("aria-hidden", "true");
    svg.dataset.dpeIcon = { application: "applications", system: "screen" }[kind] || kind;
    svg.dataset.iconOrigin = "fallback";
    return svg;
  }
  function sourceCopy(option) {
    const system = option.id === "system";
    const label =
      (typeof option.label === "string" && option.label.trim()) ||
      (system ? "System audio" : "Application");
    const detail =
      (typeof option.detail === "string" && option.detail.trim()) ||
      (system ? "All applications except Discord" : "Application audio");
    return { label, detail };
  }
  function sourceCard(option, onSelect = () => {}) {
    const system = option.id === "system";
    const { label, detail } = sourceCopy(option);
    const row = element(system ? "div" : "label", "", system ? "source system-source" : "source");
    if (system) {
      row.setAttribute("role", "group");
      row.setAttribute("aria-label", "System audio, selected");
    }
    const radio = system ? null : element("input");
    if (radio) {
      radio.type = "radio";
      radio.name = "audio-source";
      radio.value = option.id;
      radio.addEventListener("change", () => {
        onSelect(option.id);
      });
      row.append(radio);
    }
    const text = element("span", "", "source-text");
    text.append(element("strong", label), element("small", detail));
    const visual = element("span", "", "source-visual");
    visual.append(icon(option.id === "system" ? "system" : "application"));
    row.title = `${label} — ${detail}`;
    row.append(visual, text);
    return { row, radio, option, visual };
  }
  function searchControl(onInput = () => {}) {
    return searchView(document, onInput, icon, uiRuntime);
  }
  function sourceTabs(onSelect = () => {}) {
    const tabs = element("div", "", "tabs dpe-segments");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Audio sources");
    const tabButtons = ["Applications", "System Audio"].map((label, index) => {
      const button = element("button", "", "tab dpe-segment");
      button.append(icon(index ? "system" : "application"), element("span", label));
      button.id = `audio-tab-${index}`;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", "audio-panel");
      button.onclick = () => {
        setActive(index);
        onSelect(index);
      };
      button.addEventListener("keydown", (event) => {
        const target = { ArrowRight: 1 - index, ArrowLeft: 1 - index, Home: 0, End: 1 }[event.key];
        if (target === undefined) return;
        event.preventDefault();
        setActive(target);
        onSelect(target);
        tabButtons[target].focus();
      });
      tabs.append(button);
      return button;
    });
    function setActive(index) {
      tabButtons.forEach((button, i) => {
        button.setAttribute("aria-selected", String(i === index));
        button.tabIndex = i === index ? 0 : -1;
      });
    }
    setActive(0);
    return { tabs, tabButtons, setActive };
  }
  function sourceList() {
    const list = element("fieldset");
    list.append(element("legend", "Audio source", "sr-only"));
    const empty = element("p", "", "empty");
    empty.setAttribute("role", "status");
    list.append(empty);
    return { list, empty };
  }
  function actionButton(label, variant = "secondary", iconName) {
    const button = element("button", iconName ? "" : label, "dpe-button dpe-button--" + variant);
    button.type = "button";
    if (iconName) {
      button.append(icon(iconName));
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    return button;
  }
  return {
    element,
    icon,
    sourceCopy,
    sourceCard,
    searchControl,
    sourceTabs,
    sourceList,
    actionButton,
  };
}

// Accept only SVG geometry. The page never supplies markup capable of loading
// resources, running handlers or crossing the preload boundary.
function officialIcon(document, source) {
  if (typeof source !== "string" || source.length > 20000) return null;
  const parsed = new document.defaultView.DOMParser().parseFromString(source, "image/svg+xml");
  const allowed = new Set([
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "rect",
    "line",
    "polygon",
    "polyline",
  ]);
  const attrs = new Set([
    "viewBox",
    "d",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "fill-rule",
    "clip-rule",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
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
  if (parsed.documentElement.localName !== "svg" || parsed.querySelector("parsererror"))
    return null;
  let count = 0;
  function copy(node) {
    if (!allowed.has(node.localName) || ++count > 100) throw Error("Invalid icon");
    const result = document.createElementNS("http://www.w3.org/2000/svg", node.localName);
    for (const { name, value } of node.attributes) {
      if (!attrs.has(name) || /url\(|javascript:|[<>]/i.test(value)) continue;
      result.setAttribute(
        name,
        ["fill", "stroke"].includes(name) && !["none", "transparent"].includes(value)
          ? "currentColor"
          : value,
      );
    }
    for (const child of node.children) result.append(copy(child));
    return result;
  }
  try {
    const svg = copy(parsed.documentElement);
    svg.setAttribute("aria-hidden", "true");
    return svg;
  } catch {
    return null;
  }
}
async function readOfficialIcons() {
  const resolve = (name) =>
    require.resolve("./discord-ui/" + name, { paths: [__dirname, path.dirname(__dirname)] });
  const presentation = require(resolve("presentation.cjs"));
  const source = require(resolve("source.cjs"));
  return require("electron").webFrame.executeJavaScript(
    source.call(presentation, { iconsOnly: true }),
  );
}

function show(
  options,
  respond,
  document = globalThis.document,
  resolveIcon = createResolver(),
  presentation = readOfficialIcons,
) {
  const previous = document.activeElement;
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  const components = require.resolve("./discord-ui/components.css", {
    paths: [__dirname, path.dirname(__dirname)],
  });
  style.textContent =
    fs.readFileSync(components, "utf8") +
    "\n" +
    fs.readFileSync(path.join(__dirname, "audio-picker.css"), "utf8");
  root.append(style);
  const { element, sourceCopy, sourceCard, searchControl, sourceTabs, sourceList, actionButton } =
    createControls(document);
  const dialog = element("dialog", "", "dpe-ui");
  dialog.tabIndex = -1;
  dialog.dataset.reducedMotion = String(Boolean(document.querySelector(".reduce-motion")));
  dialog.setAttribute("aria-labelledby", "audio-title");
  dialog.setAttribute("aria-describedby", "audio-description");
  const header = element("header");
  const { tabs, tabButtons, setActive } = sourceTabs((index) => switchTab(index));
  header.append(tabs);
  const content = element("div", "", "content");
  content.id = "audio-panel";
  content.setAttribute("role", "tabpanel");
  const title = element("h2", "Share audio from an application");
  title.id = "audio-title";
  const { search, searchField, clearSearch } = searchControl(() => {
    filter();
    content.scrollTop = 0;
  });
  const { list, empty } = sourceList();
  const hint = element("p", "", "hint");
  const scope = element("span", "", "scope");
  const [appScope, systemScope] = [
    "Includes windows from the same app",
    "Includes all applications except Discord",
  ].map((text, index) => {
    const copy = element("span");
    const separator = element("span", "•");
    separator.setAttribute("aria-hidden", "true");
    const detection = element("a", "Automatic detection");
    detection.href =
      "https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/wayland-audio.md#automatic-detection";
    detection.target = "_blank";
    detection.rel = "noopener noreferrer";
    const detectionLink = element("span", "", "detection-link");
    detectionLink.append(separator, detection);
    copy.append(element("span", text, "scope-copy"));
    if (index === 0) copy.append(detectionLink);
    scope.append(copy);
    return copy;
  });
  hint.append(scope);
  hint.id = "audio-description";
  const body = element("div", "", "audio-body");
  body.append(title, searchField, list);
  content.append(body);
  const footer = element("footer");
  const summary = element("div", "", "summary");
  const summaryTitle = element("strong", "Choose audio source");
  summary.append(summaryTitle, hint);
  const actions = element("div", "", "actions");
  const refresh = actionButton("Refresh applications", "icon", "refresh");
  refresh.classList.add("refresh");
  const skip = actionButton("Continue without audio");
  skip.classList.add("secondary");
  const share = actionButton("Make Selection", "primary");
  share.classList.add("primary");
  share.disabled = true;
  const status = element("span", "", "refresh-status sr-only");
  status.setAttribute("role", "status");
  actions.append(skip, share, refresh, status);
  footer.append(summary, actions);
  dialog.append(header, content, footer);
  root.append(dialog);
  let selected = null;
  let activeTab = 0;
  let closed = false;
  let finishing = false;
  let enterFrame;
  let entranceTimer;
  let exitTimer;
  let refreshing = false;
  let refreshFocus = null;
  let rows = [];
  let officialIcons = {};
  const applyIcons = (container) => {
    for (const node of container.querySelectorAll("[data-dpe-icon]")) {
      const glyph = officialIcon(document, officialIcons[node.dataset.dpeIcon]);
      if (glyph) {
        glyph.dataset.dpeIcon = node.dataset.dpeIcon;
        glyph.dataset.iconOrigin = "discord";
        node.replaceWith(glyph);
      }
    }
  };
  const makeRow = (option) => {
    const entry = sourceCard(option, (id) => {
      selected = id;
      share.disabled = refreshing;
    });
    applyIcons(entry.row);
    list.append(entry.row);
    return entry;
  };
  rows = options.map(makeRow);
  const filter = () => {
    const query = search.value.trim().toLocaleLowerCase();
    clearSearch.hidden = search.value.length === 0;
    let apps = 0;
    for (const { row, radio, option } of rows) {
      row.hidden =
        (option.id === "system") !== (activeTab === 1) ||
        (activeTab === 0 &&
          !`${option.label} ${option.detail}`.toLocaleLowerCase().includes(query));
      if (!row.hidden && option.id !== "system") apps++;
      if (activeTab === 0 && row.hidden && radio?.checked) {
        radio.checked = false;
        selected = null;
        share.disabled = true;
      }
    }
    if (activeTab === 0 && selected === null) {
      const first = rows.find(({ row, radio }) => radio && !row.hidden);
      if (first) {
        first.radio.checked = true;
        selected = first.option.id;
      }
    }
    share.disabled =
      refreshing ||
      (activeTab === 0 ? selected === null : !rows.some(({ option }) => option.id === "system"));
    empty.hidden = activeTab === 1 || apps > 0;
    empty.textContent = query
      ? "No applications found. Try another search."
      : "No applications found. Play some audio, then select Refresh.";
  };
  function switchTab(index) {
    activeTab = index;
    setActive(index);
    content.setAttribute("aria-labelledby", tabButtons[index].id);
    title.textContent = index ? "Share your system audio" : "Share audio from an application";
    appScope.setAttribute("aria-hidden", String(index === 1));
    systemScope.setAttribute("aria-hidden", String(index === 0));
    refresh.disabled = index === 1 || refreshing;
    searchField.setAttribute("aria-hidden", String(index === 1));
    search.disabled = index === 1;
    content.scrollTop = 0;
    filter();
    share.disabled =
      refreshing ||
      (index === 1 ? !rows.some(({ option }) => option.id === "system") : selected === null);
  }
  // Four workers keep file lookups bounded; icons never delay opening or audio.
  let iconQueue = [...rows];
  async function loadIcons() {
    while (!closed && iconQueue.length) {
      const entry = iconQueue.shift();
      const { option, visual } = entry;
      const names = JSON.stringify(option.iconNames);
      if (option.id === "system") continue;
      let data;
      try {
        data = await resolveIcon(option.iconNames);
      } catch {
        continue;
      }
      if (
        closed ||
        !data ||
        !rows.includes(entry) ||
        names !== JSON.stringify(entry.option.iconNames)
      )
        continue;
      const image = element("img");
      image.alt = "";
      image.width = image.height = 48;
      image.onload = () => {
        if (!closed && rows.includes(entry) && names === JSON.stringify(entry.option.iconNames))
          visual.replaceChildren(image);
      };
      image.src = data;
    }
  }
  function close() {
    if (closed) return;
    closed = true;
    document.defaultView.cancelAnimationFrame(enterFrame);
    clearTimeout(exitTimer);
    clearTimeout(entranceTimer);
    observer?.disconnect();
    dialog.close();
    host.remove();
    if (previous?.isConnected) previous.focus();
  }
  function finish(value) {
    if (closed || finishing) return;
    finishing = true;
    document.defaultView.cancelAnimationFrame(enterFrame);
    dialog.dataset.state = "closing";
    for (const control of root.querySelectorAll("button, input")) control.disabled = true;
    const complete = () => {
      if (closed) return;
      const reply = respond;
      close();
      reply(value);
    };
    // Current Discord ModalRoot uses 200ms easeOutQuad for both opacity and
    // scale(.7↔1). Reduced motion keeps scale1 and retains the opacity fade.
    // A detached host has no meaningful exit to animate.
    if (!host.isConnected) complete();
    else exitTimer = setTimeout(complete, 200);
  }
  skip.onclick = () => finish("none");
  refresh.onclick = () => {
    if (closed || finishing || refreshing || activeTab !== 0) return;
    refreshing = true;
    refreshFocus = root.activeElement;
    refresh.disabled = true;
    share.disabled = true;
    refresh.setAttribute("aria-busy", "true");
    status.textContent = "Refreshing applications…";
    respond("refresh");
  };
  share.onclick = () => {
    if (activeTab === 1) finish("system");
    else if (selected !== null) finish(selected);
  };
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish("none");
  });
  const outside = (event) => {
    const bounds = dialog.getBoundingClientRect();
    return (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    );
  };
  let backdropPressed = false;
  dialog.addEventListener("pointerdown", (event) => {
    backdropPressed = event.button === 0 && event.target === dialog && outside(event);
  });
  dialog.addEventListener("pointercancel", () => {
    backdropPressed = false;
  });
  dialog.addEventListener("click", (event) => {
    if (backdropPressed && event.target === dialog && outside(event)) finish("none");
    backdropPressed = false;
  });
  // A host rerender must not leave the audio helper waiting for an invisible picker.
  const Observer = document.defaultView?.MutationObserver;
  const observer = Observer
    ? new Observer(() => {
        if (!host.isConnected) finish("none");
      })
    : null;
  // Native modal dialog supplies focus containment and makes Discord inert.
  // Append inside its theme scope, without depending on hashed class names.
  (document.querySelector("#app-mount") || document.body).append(host);
  try {
    observer?.observe(document.body, { childList: true, subtree: true });
    dialog.dataset.state = "entering";
    dialog.showModal();
    const startEntrance = () => {
      clearTimeout(entranceTimer);
      if (closed || finishing || enterFrame !== undefined) return;
      void document.defaultView.getComputedStyle(dialog).opacity;
      enterFrame = document.defaultView.requestAnimationFrame(() => {
        if (!closed && !finishing) dialog.dataset.state = "open";
      });
    };
    const fallbackEntrance = () => {
      if (closed || finishing) return;
      // Never briefly scale before a slow Discord accessibility preference arrives.
      // If presentation is unavailable, an opacity-only entrance is always safe.
      dialog.dataset.reducedMotion = "true";
      startEntrance();
    };
    entranceTimer = setTimeout(fallbackEntrance, 80);
    switchTab(0);
    dialog.focus();
    for (let worker = 0; worker < 4; worker++) void loadIcons().catch(() => {});
    void Promise.resolve()
      .then(presentation)
      .then((value) => {
        if (closed || finishing) return;
        dialog.dataset.reducedMotion = String(value?.reducedMotion === true);
        const geometry = new Set([
          "--dpe-discord-modal-width",
          "--dpe-discord-scrollbar-width",
          "--dpe-discord-control-height",
          "--dpe-discord-control-sm-height",
          "--dpe-discord-button-min-width",
          "--dpe-discord-source-picker-height",
          "--dpe-discord-source-card-width",
          "--dpe-discord-source-card-height",
        ]);
        for (const [key, token] of Object.entries(value?.tokens || {})) {
          if (!geometry.has(key) || typeof token !== "string" || token.length > 32) continue;
          const length = /^(\d+(?:\.\d+)?)(px|rem|em)$/.exec(token);
          if (
            !length ||
            Number(length[1]) <= 0 ||
            Number(length[1]) > (length[2] === "px" ? 4096 : 256)
          )
            continue;
          dialog.style.setProperty(key, token);
        }
        officialIcons = value?.icons || {};
        applyIcons(root);
        startEntrance();
      })
      .catch(fallbackEntrance);
  } catch (error) {
    observer?.disconnect();
    host.remove();
    throw error;
  }
  close.update = (nextOptions, nextRespond = respond) => {
    if (closed) return false;
    respond = nextRespond;
    if (finishing) return true;
    const scroll = content.scrollTop;
    const focused =
      root.activeElement && root.activeElement !== dialog ? root.activeElement : refreshFocus;
    refreshFocus = null;
    const focusedId = focused?.name === "audio-source" ? focused.value : null;
    const before = new Map(rows.map((entry) => [entry.option.id, entry]));
    const nextRows = nextOptions.map((option) => {
      const entry = before.get(option.id);
      before.delete(option.id);
      if (!entry) return makeRow(option);
      if (entry.option.label !== option.label || entry.option.detail !== option.detail) {
        const { label, detail } = sourceCopy(option);
        entry.row.querySelector("strong").textContent = label;
        entry.row.querySelector("small").textContent = detail;
        entry.row.title = `${label} — ${detail}`;
      }
      if (JSON.stringify(entry.option.iconNames) !== JSON.stringify(option.iconNames))
        iconQueue.push(entry);
      entry.option = option;
      return entry;
    });
    for (const entry of before.values()) entry.row.remove();
    const existing = new Set(rows);
    rows = nextRows;
    for (const entry of rows) {
      // Appending an already correctly ordered node can still disturb radio focus.
      const expected =
        rows.indexOf(entry) === 0
          ? empty.nextElementSibling
          : rows[rows.indexOf(entry) - 1].row.nextElementSibling;
      if (expected !== entry.row) list.insertBefore(entry.row, expected);
      if (!existing.has(entry)) iconQueue.push(entry);
    }
    if (!rows.some(({ option }) => option.id === selected)) selected = null;
    refreshing = false;
    refresh.disabled = activeTab === 1;
    refresh.removeAttribute("aria-busy");
    filter();
    content.scrollTop = scroll;
    if (focused?.isConnected && root.activeElement !== focused) {
      focused.focus({ preventScroll: true });
    } else if (focusedId && !focused?.isConnected) {
      const replacement = rows.find(({ option }) => option.id === selected)?.radio;
      (replacement || search).focus({ preventScroll: true });
    }
    status.textContent = "Applications refreshed.";
    for (let worker = 0; worker < 4; worker++) void loadIcons().catch(() => {});
    return true;
  };
  return close;
}
// Stable DOM primitives deliberately avoid Discord's private webpack component
// contracts. Only semantic theme tokens and the document's font/zoom are inherited.
module.exports = { available, show, createControls, officialIcon };
