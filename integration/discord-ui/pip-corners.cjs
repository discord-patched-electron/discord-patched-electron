"use strict";
// One DOM adapter in the PiP document. No React factories or stream-store hooks.
module.exports = function pipCorners(request = {}) {
  if (!window.DPEPiP || document.getElementById("dpe-pip-corners")) return;
  const host = document.createElement("div");
  host.id = "dpe-pip-corners";
  host.className = "dpe-ui";
  host.setAttribute("role", "group");
  host.setAttribute("aria-label", "PiP position");
  let moving = false;
  const buttons = ["top-left", "top-right", "bottom-left", "bottom-right"].map((corner) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dpe-pip-corner";
    button.title = button.ariaLabel = `Restore PiP to ${corner.replace("-", " ")}`;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", async () => {
      try {
        render(await window.DPEPiP.choose(corner));
      } catch {
        host.hidden = true;
      }
    });
    host.append(button);
    return [corner, button];
  });
  const monitorButtons = ["left", "right"].map((direction) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dpe-pip-monitor";
    button.dataset.direction = direction;
    button.title = button.ariaLabel = `Move PiP to the monitor on the ${direction}`;
    const icon = document.createElement("span");
    icon.className = "dpe-icon";
    icon.dataset.dpeIcon = "chevron";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML =
      globalThis[Symbol.for("dpe.discordPresentation")]?.icons?.chevron ||
      request.icons?.chevron ||
      "";
    button.append(icon);
    button.disabled = true;
    button.addEventListener("click", async () => {
      if (moving || button.disabled) return;
      moving = true;
      render(lastState);
      try {
        render(await window.DPEPiP.monitor(direction));
      } catch {
        void refresh();
      } finally {
        moving = false;
        render(lastState);
      }
    });
    host.append(button);
    return [direction, button];
  });
  let anchor;
  let lastState;
  const visible = (node) => {
    for (let item = node; item && item !== document.body; item = item.parentElement) {
      const css = getComputedStyle(item);
      if (css.display === "none" || css.visibility === "hidden") return false;
      if (Number(css.opacity) === 0 && !item.getAnimations().some((a) => a.playState === "running"))
        return false;
    }
    return true;
  };
  function render(state) {
    lastState = state;
    for (const [direction, button] of monitorButtons)
      button.disabled = moving || state?.monitors?.[direction] !== true;
    host.hidden = !state?.available || state.large;
    const hasMonitors = state?.monitors?.left === true || state?.monitors?.right === true;
    for (const [, button] of monitorButtons) button.hidden = !hasMonitors;
    for (const [corner, button] of buttons)
      button.setAttribute("aria-pressed", String(state?.corner === corner));
    // Discord's stream status is optional. Only use a unique visible status
    // near the upper trailing edge; otherwise retain the bounded default inset.
    const statuses = [
      ...document.querySelectorAll(
        '[class*="streamerMode"],[class*="streamQuality"],[class*="streamStatus"]',
      ),
    ].filter((node) => {
      const r = node.getBoundingClientRect();
      return r.width > 0 && r.top < 80 && r.right > innerWidth / 2;
    });
    const parent = statuses.length === 1 ? statuses[0].parentElement : undefined;
    const candidate =
      parent &&
      visible(parent) &&
      parent !== document.body &&
      !parent.closest('button,a,[role="button"]')
        ? parent
        : anchor?.isConnected && visible(anchor)
          ? anchor
          : undefined;
    if (anchor !== candidate || !host.isConnected) {
      anchor?.removeAttribute("data-dpe-pip-anchor");
      anchor = candidate;
      if (anchor && getComputedStyle(anchor).position === "static")
        anchor.setAttribute("data-dpe-pip-anchor", "");
      (anchor || document.body).append(host);
      host.dataset.anchored = String(!!anchor);
    }
    if (anchor) {
      let node = anchor;
      let fade = false;
      for (
        let depth = 0;
        node && node !== document.body && depth < 4;
        depth++, node = node.parentElement
      ) {
        const css = getComputedStyle(node);
        if (
          css.transitionProperty.split(",").some((name) => name.trim() === "opacity") ||
          css.animationName !== "none" ||
          node.style.opacity !== ""
        )
          fade = true;
      }
      host.dataset.nativeFade = String(fade);
    } else host.dataset.nativeFade = "false";
    // Sharing the status container also shares Discord's own opacity/transform
    // animation. Never chase its animated viewport rectangle with IPC polling.
    if (anchor && statuses.length === 1)
      host.style.setProperty(
        "--dpe-pip-status-bottom",
        `${statuses[0].offsetTop + statuses[0].offsetHeight + 8}px`,
      );
    else if (!anchor) host.style.removeProperty("--dpe-pip-status-bottom");
  }
  let busy = false;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      render(await window.DPEPiP.state());
    } catch {
      host.hidden = true;
    } finally {
      busy = false;
    }
  }
  host.hidden = true;
  document.body.append(host);
  // Poll only while hovered/focused; compositor geometry is authoritative on Wayland.
  const timer = setInterval(() => {
    if (document.body.matches(":hover") || document.hasFocus() || !host.isConnected) void refresh();
  }, 750);
  document.addEventListener("pointerover", refresh);
  document.addEventListener("focusin", refresh);
  window.addEventListener("resize", refresh);
  window.addEventListener("focus", refresh);
  document.addEventListener("visibilitychange", refresh);
  let repairPending = false;
  const observer = new MutationObserver((changes) => {
    if (
      repairPending ||
      !changes.some(
        (change) =>
          change.type === "childList" ||
          (anchor && (change.target === anchor || change.target.contains(anchor))),
      )
    )
      return;
    repairPending = true;
    queueMicrotask(() => {
      repairPending = false;
      if (lastState) render(lastState);
    });
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  window.addEventListener(
    "pagehide",
    () => {
      clearInterval(timer);
      observer.disconnect();
      anchor?.removeAttribute("data-dpe-pip-anchor");
      document.removeEventListener("pointerover", refresh);
      document.removeEventListener("focusin", refresh);
      window.removeEventListener("resize", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    },
    { once: true },
  );
  void refresh();
};
