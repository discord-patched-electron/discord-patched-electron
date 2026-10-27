(() => {
  window.openConfigLink = async (name) => {
    const error = document.getElementById("external-link-error");
    error.hidden = true;
    try {
      await window.Native.openLink(name);
    } catch (failure) {
      error.textContent = failure.message.replace(
        /^Error invoking remote method '[^']+': Error: /,
        "",
      );
      error.hidden = false;
    }
  };
  document.getElementById("config-github").addEventListener("click", (event) => {
    event.preventDefault();
    void window.openConfigLink("github");
  });
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const container = document.getElementById("container");
  const panel = document.getElementById("flags-panel");
  const handlers = new Map(tabs.map((tab) => [tab.id, tab.onclick]));
  function select(tab) {
    const flags = tab.id === "t_flags";
    for (const item of tabs) {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
    }
    container.hidden = flags;
    panel.hidden = !flags;
    if (!flags) container.setAttribute("aria-labelledby", tab.id);
    handlers.get(tab.id)?.();
    // The upstream settings list rebuilds its controls when changing tabs.
    for (const input of container.querySelectorAll("input, select")) {
      const name = input.closest("#container > div")?.firstElementChild;
      if (name) {
        const copy = name.cloneNode(true);
        copy.querySelectorAll(".switch, select").forEach((control) => control.remove());
        input.setAttribute("aria-label", copy.textContent.trim());
      }
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.onclick = () => select(tab);
    tab.addEventListener("keydown", (event) => {
      const rtl = getComputedStyle(document.documentElement).direction === "rtl";
      let next;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (event.key === "ArrowRight") next = index + (rtl ? -1 : 1);
      if (event.key === "ArrowLeft") next = index + (rtl ? 1 : -1);
      if (next === undefined) return;
      event.preventDefault();
      const target = tabs[(next + tabs.length) % tabs.length];
      target.focus();
      select(target);
    });
  }
  window.Native.onTab((name) => {
    const tab = tabs.find((item) => item.id === "t_" + name);
    if (tab) select(tab);
  });
  select(tabs[0]);
})();
