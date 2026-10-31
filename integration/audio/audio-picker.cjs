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

function showFallback(
  options,
  respond,
  document = globalThis.document,
  resolveIcon = createResolver(),
) {
  const previous = document.activeElement;
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = fs.readFileSync(path.join(__dirname, "audio-picker.css"), "utf8");
  root.append(style);
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function icon(kind) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("fill", "currentColor");
    path.setAttribute(
      "d",
      {
        application:
          "M5 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h14a3 3 0 0 0 3-3V6a3 3 0 0 0-3-3H5Zm0 2h1v2H5V5Zm3 0h1v2H8V5Zm3 0h1v2h-1V5Z",
        system:
          "M5 2a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h6v2H8v2h8v-2h-3v-2h6a3 3 0 0 0 3-3V5a3 3 0 0 0-3-3H5Z",
        refresh: "M19 5V2h2v7h-7V7h3.7A7 7 0 1 0 19 14h2a9 9 0 1 1-2-9Z",
      }[kind],
    );
    svg.append(path);
    return svg;
  }
  const dialog = element("dialog");
  dialog.tabIndex = -1;
  dialog.setAttribute("aria-labelledby", "audio-title");
  dialog.setAttribute("aria-describedby", "audio-description");
  const header = element("header");
  const tabs = element("div", "", "tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Audio sources");
  const tabButtons = ["Applications", "System Audio"].map((label, index) => {
    const button = element("button", "", "tab");
    button.append(icon(index ? "system" : "application"), element("span", label));
    button.id = `audio-tab-${index}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "audio-panel");
    tabs.append(button);
    return button;
  });
  header.append(tabs);
  const content = element("div", "", "content");
  content.id = "audio-panel";
  content.setAttribute("role", "tabpanel");
  const title = element("h2", "Share audio from an application");
  title.id = "audio-title";
  const search = element("input");
  search.type = "search";
  search.placeholder = "Search";
  search.setAttribute("aria-label", "Search applications");
  const searchField = element("div", "", "search-field");
  const searchIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  searchIcon.setAttribute("viewBox", "0 0 24 24");
  searchIcon.setAttribute("aria-hidden", "true");
  const searchPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  searchPath.setAttribute("d", "M21 21l-5.2-5.2M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z");
  searchPath.setAttribute("fill", "none");
  searchPath.setAttribute("stroke", "currentColor");
  searchPath.setAttribute("stroke-width", "2");
  searchPath.setAttribute("stroke-linecap", "round");
  searchIcon.append(searchPath);
  const clearSearch = element("button", "×", "clear-search");
  clearSearch.setAttribute("aria-label", "Clear search");
  clearSearch.hidden = true;
  searchField.append(searchIcon, search, clearSearch);
  const list = element("fieldset");
  const legend = element("legend", "Audio source", "sr-only");
  list.append(legend);
  const empty = element("p", "", "empty");
  empty.setAttribute("role", "status");
  const hint = element("p", "Includes windows and tabs from the same app.", "hint");
  hint.id = "audio-description";
  content.append(title, searchField, list, empty);
  const footer = element("footer");
  const summary = element("div", "", "summary");
  const summaryTitle = element("strong", "Choose audio source");
  summary.append(summaryTitle, hint);
  const actions = element("div", "", "actions");
  const refresh = element("button", "", "refresh");
  refresh.append(icon("refresh"));
  refresh.setAttribute("aria-label", "Refresh applications");
  refresh.title = "Refresh applications";
  const skip = element("button", "Continue without audio", "secondary");
  const share = element("button", "Make Selection", "primary");
  share.disabled = true;
  actions.append(skip, share, refresh);
  footer.append(summary, actions);
  dialog.append(header, content, footer);
  root.append(dialog);
  let selected = null;
  let activeTab = 0;
  let closed = false;
  const rows = options.map((option) => {
    const system = option.id === "system";
    const row = element(system ? "div" : "label", "", system ? "source system-source" : "source");
    const radio = system ? null : element("input");
    if (radio) {
      radio.type = "radio";
      radio.name = "audio-source";
      radio.value = option.id;
      radio.addEventListener("change", () => {
        selected = option.id;
        share.disabled = false;
      });
      row.append(radio);
    }
    const text = element("span", "", "source-text");
    text.append(element("strong", option.label), element("small", option.detail));
    const visual = element("span", "", "source-visual");
    visual.append(icon(option.id === "system" ? "system" : "application"));
    row.title = `${option.label} — ${option.detail}`;
    row.append(visual, text);
    list.append(row);
    return { row, radio, option, visual };
  });
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
      if (row.hidden && radio?.checked) {
        radio.checked = false;
        selected = null;
        share.disabled = true;
      }
    }
    empty.hidden = activeTab === 1 || apps > 0;
    empty.textContent = query
      ? "No applications found. Try another search."
      : "No applications found. Play some audio, then select Refresh.";
  };
  search.addEventListener("input", () => {
    filter();
    content.scrollTop = 0;
  });
  clearSearch.onclick = () => {
    search.value = "";
    filter();
    content.scrollTop = 0;
    search.focus();
  };
  function switchTab(index) {
    activeTab = index;
    if (index === 0 && selected === "system") selected = null;
    tabButtons.forEach((button, i) => {
      button.setAttribute("aria-selected", String(i === index));
      button.tabIndex = i === index ? 0 : -1;
    });
    content.setAttribute("aria-labelledby", tabButtons[index].id);
    title.textContent = index ? "Share your system audio" : "Share audio from an application";
    hint.textContent = index
      ? "Includes all applications except Discord."
      : "Includes windows and tabs from the same app.";
    share.textContent = index ? "Share Audio" : "Make Selection";
    refresh.hidden = index === 1;
    searchField.hidden = index === 1;
    search.hidden = index === 1;
    content.scrollTop = 0;
    filter();
    if (index === 1 && rows.some(({ option }) => option.id === "system")) selected = "system";
    share.disabled = selected === null;
  }
  tabButtons.forEach((button, index) => {
    button.onclick = () => switchTab(index);
    button.addEventListener("keydown", (event) => {
      const target = { ArrowRight: 1 - index, ArrowLeft: 1 - index, Home: 0, End: 1 }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      switchTab(target);
      tabButtons[target].focus();
    });
  });
  // Four workers keep file lookups bounded; icons never delay opening or audio.
  let nextIcon = 0;
  async function loadIcons() {
    while (!closed && nextIcon < rows.length) {
      const { option, visual } = rows[nextIcon++];
      if (option.id === "system") continue;
      const data = await resolveIcon(option.iconNames);
      if (closed || !data) continue;
      const image = element("img");
      image.alt = "";
      image.width = image.height = 48;
      image.onload = () => {
        if (!closed) visual.replaceChildren(image);
      };
      image.src = data;
    }
  }
  function close() {
    if (closed) return;
    closed = true;
    dialog.close();
    host.remove();
    if (previous?.isConnected) previous.focus();
  }
  function finish(value) {
    if (closed) return;
    close();
    respond(value);
  }
  skip.onclick = () => finish("none");
  refresh.onclick = () => finish("refresh");
  share.onclick = () => {
    if (selected !== null) finish(selected);
  };
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish("none");
  });
  // Native modal dialog supplies focus containment and makes Discord inert.
  // Append inside its theme scope, without depending on hashed class names.
  (document.querySelector("#app-mount") || document.body).append(host);
  try {
    dialog.showModal();
    switchTab(0);
    dialog.focus();
    for (let worker = 0; worker < 4; worker++) void loadIcons().catch(() => {});
  } catch (error) {
    host.remove();
    throw error;
  }
  return close;
}
function show(options, respond, document = globalThis.document, resolveIcon = createResolver()) {
  if (available(document) && process.type === "renderer") {
    const { webFrame } = require("electron");
    if (webFrame?.executeJavaScript)
      return require("./native-picker-bridge.cjs").show(options, respond, {
        execute: (code) => webFrame.executeJavaScript(code),
        resolveIcon,
        fallback: () => showFallback(options, respond, document, resolveIcon),
      });
  }
  return showFallback(options, respond, document, resolveIcon);
}
module.exports = { available, show };
