"use strict";
// Serializable presentation factory. Uses the host's React instance, local DPE
// controls and sanitized shared SVG assets; never discovers Discord components.
module.exports = function makeView(React, iconLookup) {
  const h = React.createElement;
  function Icon({ name, className = "" }) {
    const svg = typeof iconLookup === "function" ? iconLookup(name) : "";
    return svg
      ? h("span", {
          className: `dpe-icon ${className}`,
          "data-dpe-icon": name,
          "aria-hidden": true,
          dangerouslySetInnerHTML: { __html: svg },
        })
      : null;
  }
  function State({ kind, title, description, children }) {
    return h(
      "div",
      {
        className: "dpe-capture-empty-state",
        "data-dpe-capture-state": kind,
        role: kind === "error" ? "alert" : "status",
      },
      h(Icon, {
        name: kind === "error" ? "warning" : "screen",
        className: "dpe-capture-empty-icon",
      }),
      h("h3", null, title),
      description && h("p", null, description),
      children,
    );
  }
  return function Sources({
    sources = [],
    loading,
    error,
    selected,
    busy,
    onSelect,
    onPreview,
    onPreviewStop,
  }) {
    const host = React.useRef(null);
    const latest = React.useRef({ sources, busy, onPreview });
    latest.current = { sources, busy, onPreview };
    const [previews, setPreviews] = React.useState({});
    React.useEffect(() => {
      let stopped = false,
        timer,
        cursor = 0;
      const inFlight = new Set();
      let suspended = false;
      const tick = async () => {
        try {
          if (document.visibilityState === "hidden" || latest.current.busy) {
            if (!suspended) onPreviewStop?.();
            suspended = true;
            return;
          }
          suspended = false;
          const cards = Array.from(
            host.current?.querySelectorAll("[data-dpe-capture-source]") || [],
          );
          const visible = cards.filter((card) => {
            const token = card.getAttribute("data-dpe-capture-source");
            const source = latest.current.sources.find((item) => item.token === token);
            if (!source?.available || source.kind === "portal") return false;
            const bounds = card.getBoundingClientRect();
            let top = 0,
              bottom = window.innerHeight;
            for (let parent = card.parentElement; parent; parent = parent.parentElement) {
              if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) {
                const clip = parent.getBoundingClientRect();
                top = Math.max(top, clip.top);
                bottom = Math.min(bottom, clip.bottom);
              }
            }
            return bounds.width > 0 && bounds.bottom > top && bounds.top < bottom;
          });
          if (!visible.length || typeof latest.current.onPreview !== "function") return;
          // Independent sources progress independently. Slow acquisition never queues
          // another frame for that source or stalls all the other cards.
          for (let i = 0; i < visible.length && inFlight.size < 4; i++) {
            const card = visible[(cursor + i) % visible.length];
            const token = card.getAttribute("data-dpe-capture-source");
            if (inFlight.has(token)) continue;
            const bounds = card.querySelector(".dpe-capture-preview").getBoundingClientRect();
            const ratio = window.devicePixelRatio || 1;
            const scale = Math.min(1, 480 / (bounds.width * ratio), 270 / (bounds.height * ratio));
            const width = Math.max(16, Math.round(bounds.width * ratio * scale));
            const height = Math.max(16, Math.round(bounds.height * ratio * scale));
            inFlight.add(token);
            Promise.resolve(latest.current.onPreview(token, width, height))
              .then((result) => {
                if (stopped) return;
                if (
                  typeof result?.url !== "string" ||
                  result.url.length >= 200000 ||
                  !/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(result.url)
                )
                  return;
                setPreviews((previous) => {
                  const next = {};
                  for (const source of latest.current.sources)
                    if (previous[source.token]) next[source.token] = previous[source.token];
                  next[token] = result.url;
                  return next;
                });
              })
              .catch(() => {})
              .finally(() => inFlight.delete(token));
          }
          cursor = (cursor + 1) % visible.length;
        } catch {
          // A preview failure never disables capture. Keep the neutral fallback.
        } finally {
          if (!stopped) timer = setTimeout(tick, Math.ceil(1000 / 30));
        }
      };
      void tick();
      return () => {
        stopped = true;
        clearTimeout(timer);
        onPreviewStop?.();
      };
    }, []);
    const list = Array.isArray(sources)
      ? sources.filter(
          (source) => typeof source?.token === "string" && typeof source.name === "string",
        )
      : [];
    const optimized = list.filter((source) => source.kind !== "portal" && source.available);
    const ordered = optimized.length
      ? [
          ...list.filter((source) => source.kind === "screen"),
          ...list.filter((source) => source.kind !== "screen"),
        ]
      : [];
    const available = ordered.filter((source) => source.available === true);
    const tabStop = available.some((source) => source.token === selected)
      ? selected
      : available[0]?.token;
    const choose = (source) => {
      if (!busy && source.available === true) onSelect?.(source.token);
    };
    const navigate = (event, source) => {
      if (busy || event.altKey || event.ctrlKey || event.metaKey) return;
      const current = available.findIndex((item) => item.token === source.token);
      let next;
      if (["ArrowDown", "ArrowRight"].includes(event.key)) next = (current + 1) % available.length;
      else if (["ArrowUp", "ArrowLeft"].includes(event.key))
        next = (current + available.length - 1) % available.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = available.length - 1;
      else return;
      if (!available[next]) return;
      event.preventDefault();
      event.stopPropagation();
      const target = available[next];
      const host = event.currentTarget.closest(".dpe-capture-sources");
      const button = Array.from(host?.querySelectorAll("[data-dpe-capture-source]") || []).find(
        (element) => element.getAttribute("data-dpe-capture-source") === target.token,
      );
      button?.focus({ preventScroll: true });
      button?.scrollIntoView({ block: "nearest", inline: "nearest" });
      choose(target);
    };
    return h(
      "div",
      { ref: host, className: "dpe-ui dpe-capture-sources", "aria-busy": Boolean(loading || busy) },
      error
        ? State({ kind: "error", title: "Couldn’t load capture sources", description: error })
        : loading && !ordered.length
          ? State({ kind: "loading", title: "Finding screens and games…" })
          : !ordered.length &&
            State({
              kind: "empty",
              title: "You don’t have any Optimized capture sources available.",
              description:
                "Allow direct screen capture in DPE settings, or launch a game with vkcapture.",
              children: h(
                "a",
                {
                  href: "https://github.com/discord-patched-electron/discord-patched-electron/blob/main/docs/screensharing.md#choose-a-capture-method",
                  target: "_blank",
                  rel: "noreferrer",
                },
                "Learn More",
              ),
            }),
      ordered.length > 0 &&
        h(
          "div",
          {
            className: "dpe-capture-list",
            role: "listbox",
            "aria-label": "Optimized capture sources",
          },
          ordered.map((source) => {
            const isSelected = source.token === selected;
            const isScreen = source.kind === "screen";
            const disabled = Boolean(busy) || source.available !== true;
            return h(
              "button",
              {
                key: source.token,
                type: "button",
                role: "option",
                className: "dpe-capture-source",
                "data-dpe-capture-source": source.token,
                "aria-selected": isSelected,
                "aria-disabled": disabled,
                "aria-label": `${source.name}, ${isScreen ? "Screen" : "Application"}${source.reason ? `, ${source.reason}` : ""}`,
                disabled,
                tabIndex: !disabled && source.token === tabStop ? 0 : -1,
                onClick: () => choose(source),
                onKeyDown: (event) => navigate(event, source),
              },
              h(
                "span",
                { className: "dpe-capture-preview" },
                previews[source.token] &&
                  h("img", {
                    src: previews[source.token],
                    alt: "",
                    draggable: false,
                    className: "dpe-capture-preview-image",
                  }),
                h(
                  "span",
                  { className: "dpe-capture-preview-hover", "aria-hidden": true },
                  h("span", { className: "dpe-capture-preview-select" }, "Select"),
                ),
              ),
              h(
                "span",
                { className: "dpe-capture-source-label" },
                h(Icon, {
                  name: isScreen ? "screen" : "applications",
                  className: "dpe-capture-source-icon",
                }),
                h(
                  "span",
                  { className: "dpe-capture-source-name", title: source.name },
                  source.name,
                ),
              ),
              source.available !== true &&
                source.reason &&
                h("span", { className: "dpe-capture-source-reason" }, source.reason),
            );
          }),
        ),
    );
  };
};
