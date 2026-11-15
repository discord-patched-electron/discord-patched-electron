"use strict";
// Remove only the paired empty-call prompts, never active activity/media tiles.
module.exports = function pipPrompts() {
  if (document.documentElement.hasAttribute("data-dpe-pip-prompts")) return;
  document.documentElement.setAttribute("data-dpe-pip-prompts", "");
  const marked = new Set();
  const layouts = new Set();
  let observedGrid = null;
  let scheduled = false;
  const resize = new ResizeObserver(() => schedule());
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(repair);
  };
  function repair() {
    scheduled = false;
    const previousGrid = observedGrid;
    observedGrid = null;
    try {
      repairLayout();
    } finally {
      if (previousGrid !== observedGrid) {
        resize.disconnect();
        if (observedGrid) resize.observe(observedGrid);
      }
    }
  }
  const noticeButtons = new WeakSet();
  function repairNotice() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const text = walker.currentNode;
      if (
        !/^Participants without video are currently hidden(?: in PiP)?\.?$/.test(text.data.trim())
      )
        continue;
      let container = text.parentElement;
      for (let depth = 0; container && depth < 4; depth++, container = container.parentElement) {
        const buttons = container.querySelectorAll('button,[role="button"]');
        if (buttons.length !== 1 || container.querySelector("video,iframe,canvas")) continue;
        const button = buttons[0];
        const label = "Participants without video are currently hidden in PiP.";
        if (text.data !== label) text.data = label;
        if (button.textContent !== "Open DPE Settings") button.textContent = "Open DPE Settings";
        if (!noticeButtons.has(button)) {
          noticeButtons.add(button);
          button.addEventListener(
            "click",
            (event) => {
              event.preventDefault();
              event.stopImmediatePropagation();
              void window.DPEPiP?.openSettings?.();
            },
            true,
          );
        }
        break;
      }
    }
  }
  function repairLayout() {
    repairNotice();
    for (const node of marked) node.removeAttribute("data-dpe-pip-prompt");
    marked.clear();
    for (const node of layouts) {
      node.removeAttribute("data-dpe-pip-grid");
      node.removeAttribute("data-dpe-pip-cell");
      node.style.removeProperty("--dpe-pip-columns");
      node.style.removeProperty("--dpe-pip-rows");
    }
    layouts.clear();
    const buttons = [...document.querySelectorAll('button,[role="button"]')];
    const action = (node, id, label) =>
      node.dataset.action === id || node.textContent.trim() === label;
    const invites = buttons.filter((node) => action(node, "invite-to-voice", "Invite to Voice"));
    const activities = buttons.filter((node) => action(node, "choose-activity", "Choose Activity"));
    if (invites.length !== 1 || activities.length !== 1) return;
    const invite = invites[0],
      activity = activities[0];
    const tile = invite.closest('[class*="tile" i]');
    if (
      !tile ||
      tile === document.body ||
      !tile.contains(activity) ||
      tile.querySelector("video,iframe,canvas,[data-participant-id],[data-user-id]")
    )
      return;
    const controls = [...tile.querySelectorAll('button,[role="button"]')];
    if (controls.length !== 2 || !controls.includes(invite) || !controls.includes(activity)) return;
    // The visible card is often nested inside a positioned tileSizer. Hiding
    // only its content leaves the native grid's empty cell behind.
    let slot = tile;
    while (
      slot.parentElement &&
      slot.parentElement !== document.body &&
      slot.parentElement.children.length === 1
    )
      slot = slot.parentElement;
    slot.setAttribute("data-dpe-pip-prompt", "");
    marked.add(slot);
    const grid = slot.parentElement;
    const cells =
      grid &&
      [...grid.children].filter(
        (node) => node !== slot && getComputedStyle(node).display !== "none",
      );
    const media = 'video,iframe,canvas,[data-participant-id],[data-user-id],[class*="tile" i]';
    if (
      !grid ||
      grid === document.body ||
      !cells.length ||
      cells.length > 16 ||
      !cells.every((node) => node.matches(media) || node.querySelector(media))
    )
      return;
    const rect = grid.getBoundingClientRect(),
      css = getComputedStyle(grid);
    if (!(rect.width > 0 && rect.height > 0)) return;
    const gapX = parseFloat(css.columnGap) || 0,
      gapY = parseFloat(css.rowGap) || 0;
    let columns = 1,
      best = 0;
    for (let count = 1; count <= cells.length; count++) {
      const rows = Math.ceil(cells.length / count);
      const size = Math.min(
        (rect.width - gapX * (count - 1)) / count,
        (((rect.height - gapY * (rows - 1)) / rows) * 16) / 9,
      );
      if (size > best) {
        best = size;
        columns = count;
      }
    }
    grid.setAttribute("data-dpe-pip-grid", "");
    grid.style.setProperty("--dpe-pip-columns", String(columns));
    grid.style.setProperty("--dpe-pip-rows", String(Math.ceil(cells.length / columns)));
    layouts.add(grid);
    for (const cell of cells) {
      cell.setAttribute("data-dpe-pip-cell", "");
      layouts.add(cell);
    }
    observedGrid = grid;
  }
  const observer = new MutationObserver(() => {
    schedule();
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  repair();
  window.addEventListener(
    "pagehide",
    () => {
      observer.disconnect();
      resize.disconnect();
      for (const node of marked) node.removeAttribute("data-dpe-pip-prompt");
      for (const node of layouts) {
        node.removeAttribute("data-dpe-pip-grid");
        node.removeAttribute("data-dpe-pip-cell");
      }
    },
    { once: true },
  );
};
