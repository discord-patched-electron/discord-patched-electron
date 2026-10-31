// Real controls shared by Settings and its isolated component report.
(() => {
  const icons = new Map();
  const selects = new WeakMap();
  function icon(name, label) {
    const span = document.createElement("span");
    span.className = "dpe-icon";
    span.dataset.dpeIcon = name;
    if (label) {
      span.setAttribute("role", "img");
      span.setAttribute("aria-label", label);
      span.title = label;
    } else span.setAttribute("aria-hidden", "true");
    renderIcon(span);
    return span;
  }
  function renderIcon(slot) {
    const svg = icons.get(slot.dataset.dpeIcon) || window.DPEFallbackIcons?.[slot.dataset.dpeIcon];
    if (svg) slot.innerHTML = svg; // Only the main-process sanitized icon registry reaches here.
    else slot.replaceChildren();
  }
  function updateIcons(next = {}) {
    for (const [name, svg] of Object.entries(next)) icons.set(name, svg);
    document.querySelectorAll("[data-dpe-icon]").forEach(renderIcon);
  }
  function experimental(knownCrash = false) {
    const node = icon("experimental", knownCrash ? "Known to crash" : "Experimental");
    node.classList.add("risk-icon");
    node.classList.toggle("danger", knownCrash);
    return node;
  }
  function switchControl(input) {
    if (input.parentElement?.classList.contains("dpe-switch-control")) return input.parentElement;
    const control = document.createElement("span");
    control.className = "dpe-switch-control";
    input.classList.add("dpe-switch");
    input.setAttribute("role", "switch");
    const track = document.createElement("span");
    track.className = "dpe-switch-track";
    track.setAttribute("aria-hidden", "true");
    const thumb = document.createElement("span");
    thumb.className = "dpe-switch-thumb";

    track.append(thumb);
    if (input.parentNode) input.replaceWith(control);
    control.append(input, track);
    // Discord's current switch interpolates one continuous state, including its
    // pressed shape. Retarget that state so reversing during a click never jumps.
    const osMotion = matchMedia("(prefers-reduced-motion: reduce)");
    let state = +input.checked,
      frame = 0;
    const interpolate = (value, stops, values) => {
      const index = Math.max(0, stops.findIndex((stop, i) => i && value <= stop) - 1);
      if (value >= stops.at(-1)) return values.at(-1);
      const fraction = (value - stops[index]) / (stops[index + 1] - stops[index]);
      return values[index] + fraction * (values[index + 1] - values[index]);
    };
    const clearShape = () => {
      thumb.removeAttribute("style");
    };
    function move(target, changed = false) {
      if (!frame) state = changed ? +!input.checked : +input.checked;
      cancelAnimationFrame(frame);
      const reduced = osMotion.matches || document.body.dataset.reducedMotion === "true";
      if (reduced) {
        state = target;
        frame = 0;
        clearShape();
        return;
      }
      const css = getComputedStyle(control);
      const size = Number.parseFloat(css.getPropertyValue("--dpe-switch-thumb-size")) || 16;
      const rawInset = Number.parseFloat(css.getPropertyValue("--dpe-switch-thumb-inset"));
      const inset = Number.isFinite(rawInset) ? rawInset : 4;
      const travel = Math.max(0, (Number.parseFloat(css.width) || 48) - size - inset * 2);
      const scale = size / 16;
      const from = state,
        started = performance.now();
      const shapeStops = input.checked ? [0, 0.3, 0.5, 0.8, 0.9, 1] : [0, 0.1, 0.2, 0.5, 0.7, 1];
      const shape = (values) =>
        interpolate(state, shapeStops, input.checked ? [...values].reverse() : values);
      function tick(now) {
        if (osMotion.matches || document.body.dataset.reducedMotion === "true") {
          state = target;
          frame = 0;
          clearShape();
          return;
        }
        const elapsed = Math.min(1, (now - started) / 300);
        state = from + (target - from) * elapsed;
        const offset = interpolate(
          state,
          [0, 0.1, 0.4, 0.6, 0.9, 1],
          [0, 0, 0, travel, travel, travel],
        );
        thumb.style.insetInlineStart =
          offset + inset + (shape([4, 4, 2.5, 1, 2.5, 4]) - 4) * scale + "px";
        thumb.style.top = inset + (shape([4, 7, 2.5, 7, 2.5, 4]) - 4) * scale + "px";
        thumb.style.width = shape([16, 16, 19, 22, 19, 16]) * scale + "px";
        thumb.style.height = shape([16, 10, 19, 10, 19, 16]) * scale + "px";
        thumb.style.borderRadius = shape([8, 5, 9.5, 5, 9.5, 8]) * scale + "px";
        if (elapsed < 1 && control.isConnected) frame = requestAnimationFrame(tick);
        else {
          frame = 0;
          if (target === 0 || target === 1) clearShape();
        }
      }
      frame = requestAnimationFrame(tick);
    }
    const press = () => {
      if (!input.disabled) move(input.checked ? 0.9 : 0.1);
    };
    input.addEventListener("pointerdown", press);
    input.addEventListener("pointerup", () => move(+input.checked));
    input.addEventListener("pointercancel", () => move(+input.checked));
    input.addEventListener("blur", () => {
      if (frame) move(+input.checked);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === " " && !event.repeat) press();
    });
    input.addEventListener("keyup", (event) => {
      if (event.key === " ") move(+input.checked);
    });
    input.addEventListener("change", () => move(+input.checked, true));
    return control;
  }
  function settingRow({
    id,
    label,
    description,
    checked = false,
    disabled = false,
    experimental: isExperimental = false,
    onChange,
  }) {
    const row = document.createElement("div");
    row.className = "setting-row";
    const copy = document.createElement("div");
    copy.className = "setting-copy";
    const name = document.createElement("label");
    name.htmlFor = id;
    if (isExperimental) name.append(experimental());
    name.append(label);
    const hint = document.createElement("p");
    hint.id = id + "-description";
    hint.className = "hint";
    hint.textContent = description;
    const input = document.createElement("input");
    input.id = id;
    input.setAttribute("aria-label", label);
    input.type = "checkbox";
    input.checked = checked;
    input.disabled = disabled;
    input.setAttribute("aria-describedby", hint.id);
    input.addEventListener("change", () => onChange?.(input.checked));
    copy.append(name, hint);
    row.append(copy, switchControl(input));
    return row;
  }
  function upgradeSelect(select) {
    if (selects.has(select)) return selects.get(select);
    const wrapper = document.createElement("div");
    wrapper.className = "dpe-select";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "dpe-select-trigger";
    trigger.id = select.id + "-trigger";
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const label = document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
    if (label) {
      label.id ||= select.id + "-label";
      trigger.setAttribute("aria-labelledby", label.id);
      label.htmlFor = trigger.id;
    } else
      trigger.setAttribute(
        "aria-label",
        select.getAttribute("aria-label") || select.name || "Choose an option",
      );
    const value = document.createElement("span");
    value.className = "dpe-select-value";
    trigger.append(value, icon("chevron"));
    const popup = document.createElement("div");
    popup.className = "dpe-select-menu";
    popup.id = select.id + "-listbox";
    popup.setAttribute("role", "listbox");
    popup.setAttribute("popover", "manual");
    popup.setAttribute("aria-label", label?.textContent || select.name || "Options");
    trigger.setAttribute("aria-controls", popup.id);
    select.before(wrapper);
    select.hidden = true;
    select.tabIndex = -1;
    wrapper.append(select, trigger, popup);
    let active = -1,
      opened = false,
      search = "",
      searchTimer;
    const options = () => [...select.options];
    const enabled = () =>
      options()
        .map((option, index) => (!option.disabled ? index : -1))
        .filter((index) => index !== -1);
    function mark() {
      for (const [index, item] of [...popup.children].entries()) {
        item.dataset.highlighted = String(index === active);
        item.setAttribute("aria-selected", String(index === select.selectedIndex));
      }
      const current = popup.children[active];
      if (current && opened) {
        trigger.setAttribute("aria-activedescendant", current.id);
        if (opened && popup.matches(":popover-open")) current.scrollIntoView({ block: "nearest" });
      } else trigger.removeAttribute("aria-activedescendant");
    }
    function position() {
      const rect = trigger.getBoundingClientRect();
      popup.style.width = rect.width + "px";
      popup.style.left = Math.max(8, Math.min(rect.left, innerWidth - rect.width - 8)) + "px";
      const below = innerHeight - rect.bottom - 8;
      const above = rect.top - 8;
      const height = Math.min(240, Math.max(below, above));
      popup.style.maxHeight = height + "px";
      const opensBelow = below >= Math.min(popup.scrollHeight, 240) || below >= above;
      popup.style.transformOrigin = opensBelow ? "center top" : "center bottom";
      popup.style.top = opensBelow
        ? rect.bottom + 4 + "px"
        : Math.max(8, rect.top - Math.min(popup.scrollHeight, height) - 4) + "px";
    }
    function close() {
      if (!opened) return;
      opened = false;
      popup.hidePopover();
      trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("scroll", position, true);
      window.removeEventListener("resize", position);
    }
    function outside(event) {
      if (!wrapper.contains(event.target)) close();
    }
    function sync() {
      value.textContent = select.selectedOptions[0]?.textContent || "Choose an option";
      trigger.disabled = select.disabled || !options().length;
      if (select.disabled) close();
      popup.replaceChildren(
        ...options().map((option, index) => {
          const item = document.createElement("div");
          item.id = popup.id + "-" + index;
          item.className = "dpe-select-option";
          item.setAttribute("role", "option");
          item.setAttribute("aria-disabled", String(option.disabled));
          const text = document.createElement("span");
          text.textContent = option.textContent;
          item.append(text, icon("check"));
          item.onpointermove = () => {
            if (!option.disabled) {
              active = index;
              mark();
            }
          };
          item.onpointerdown = (event) => event.preventDefault();
          item.onclick = () => choose(index);
          return item;
        }),
      );
      mark();
      if (opened) position();
    }
    function open() {
      if (trigger.disabled || opened || !enabled().length) return;
      opened = true;
      active = select.selectedIndex >= 0 ? select.selectedIndex : enabled()[0];
      sync();
      popup.showPopover();
      position();
      trigger.setAttribute("aria-expanded", "true");
      mark();
      document.addEventListener("pointerdown", outside, true);
      document.addEventListener("scroll", position, true);
      window.addEventListener("resize", position);
    }
    function choose(index) {
      if (index === undefined || index < 0 || !options()[index] || options()[index].disabled)
        return;
      select.selectedIndex = index;
      sync();
      close();
      trigger.focus();
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    trigger.onclick = () => (opened ? close() : open());
    trigger.onkeydown = (event) => {
      const items = enabled();
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        open();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        active =
          event.key === "Home"
            ? items[0]
            : event.key === "End"
              ? items.at(-1)
              : items[(items.indexOf(active) + offset + items.length) % items.length];
        mark();
      } else if (event.key === "Escape" && opened) {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if ((event.key === "Enter" || event.key === " ") && opened) {
        event.preventDefault();
        choose(active);
      } else if (event.key === "Tab") close();
      else if (
        event.key.length === 1 &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        event.key !== " "
      ) {
        event.preventDefault();
        open();
        clearTimeout(searchTimer);
        search += event.key.toLocaleLowerCase();
        const found = items.find((index) =>
          options()[index].textContent.toLocaleLowerCase().startsWith(search),
        );
        if (found !== undefined) active = found;
        mark();
        searchTimer = setTimeout(() => {
          search = "";
        }, 700);
      }
    };
    select.addEventListener("change", sync);
    new MutationObserver(sync).observe(select, {
      attributes: true,
      childList: true,
      subtree: true,
    });
    const api = { sync, close, trigger };
    selects.set(select, api);
    sync();
    return api;
  }
  function sync(root = document) {
    root.querySelectorAll("select").forEach((select) => upgradeSelect(select).sync());
  }
  function appearanceField() {
    const node = document.createElement("div");
    node.className = "appearance-page";
    node.innerHTML =
      '<div class="page-heading"><h2>Appearance</h2></div><div class="appearance-heading"><label for="theme-css" class="section-label">Custom CSS</label><p id="theme-help" class="hint">Changes save automatically. Restart Discord to apply them.</p><p id="theme-status" class="sr-only" role="status"></p></div><div class="theme-editor-frame"><textarea id="theme-css" class="dpe-input theme-css" spellcheck="false" aria-describedby="theme-help"></textarea></div>';
    return node;
  }
  function projectMenuControl(trigger, popup) {
    function position() {
      const rect = trigger.getBoundingClientRect();
      popup.style.left = rect.left + "px";
      popup.style.top = rect.bottom + 4 + "px";
      popup.style.minWidth = rect.width + "px";
    }
    trigger.onclick = () => {
      position();
      popup.togglePopover();
      if (popup.matches(":popover-open")) popup.querySelector("a").focus();
    };
    popup.addEventListener("toggle", () =>
      trigger.setAttribute("aria-expanded", String(popup.matches(":popover-open"))),
    );
    popup.addEventListener("keydown", (event) => {
      const items = [...popup.querySelectorAll("a")];
      let next;
      if (event.key === "ArrowDown")
        next = (items.indexOf(document.activeElement) + 1) % items.length;
      if (event.key === "ArrowUp")
        next = (items.indexOf(document.activeElement) + items.length - 1) % items.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = items.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        items[next].focus();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        popup.hidePopover();
        trigger.focus();
      }
    });
  }
  function copyButton(button) {
    let timer;
    const original = icon("copy"),
      copied = icon("check");
    original.classList.add("copy-original");
    copied.classList.add("copy-success");
    button.replaceChildren(original, copied);
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      button.setAttribute("aria-label", "Copying command");
      button.title = "Copying command";
      const status = button.parentElement.querySelector('[role="status"]');
      if (status) status.textContent = "";
      try {
        const result = await window.Native.copyRecovery();
        if (result?.error) throw new Error(result.error);
        button.dataset.copied = "true";
        button.setAttribute("aria-label", "Copied command");
        button.title = "Copied";
        const status = button.parentElement.querySelector('[role="status"]');
        if (status) status.textContent = "Command copied";
      } catch {
        button.dataset.copied = "false";
        const status = button.parentElement.querySelector('[role="status"]');
        if (status)
          status.textContent = "Could not copy command. Select the command and copy it manually.";
        button.setAttribute("aria-label", "Could not copy command. Try again");
        button.title = "Could not copy. Try again";
      } finally {
        button.disabled = false;
        clearTimeout(timer);
        timer = setTimeout(() => {
          delete button.dataset.copied;
          button.setAttribute("aria-label", "Copy command");
          button.title = "Copy command";
        }, 2000);
      }
    });
  }
  window.DPEControls = {
    icon,
    updateIcons,
    experimental,
    settingRow,
    switchControl,
    upgradeSelect,
    sync,
    copyButton,
    appearanceField,
    projectMenuControl,
  };
})();
