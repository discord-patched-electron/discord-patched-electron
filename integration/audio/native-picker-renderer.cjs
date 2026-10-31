"use strict";
// Page-world presentation only. No Node APIs or fixed Discord module IDs.
module.exports = function nativePicker(request, discover) {
  const sessions = (globalThis[Symbol.for("dpe.audioPicker.sessions")] ||= new Map());
  const cancelled = (globalThis[Symbol.for("dpe.audioPicker.cancelled")] ||= new Set());
  if (request.operation === "close") {
    cancelled.add(request.key);
    if (cancelled.size > 256) cancelled.delete(cancelled.values().next().value);
    return sessions.get(request.key)?.finish({ cancelled: true });
  }
  if (request.operation === "status") return { mounted: !!sessions.get(request.key)?.mounted };
  if (request.operation === "open" && cancelled.has(request.key)) return { cancelled: true };
  if (request.operation === "icon") {
    const session = sessions.get(request.key);
    if (session?.ids.has(request.id) && /^data:image\/(png|svg\+xml);base64,/.test(request.data)) {
      session.icons[request.id] = request.data;
      session.update?.({ ...session.icons });
    }
    return;
  }
  const unavailable = { unavailable: true };
  if (!["open", "probe"].includes(request.operation)) return unavailable;
  const discovery = discover(request.key);
  if (request.operation === "probe")
    return { ...discovery.found, reason: discovery.reason, ambiguous: discovery.ambiguous };
  if (discovery.reason !== "ready") return { ...unavailable, reason: discovery.reason };
  const { React, api, ModalRoot, Button, SearchBar, SegmentedControl, Clickable, Scroller } =
    discovery.components;
  // A refreshed glyph must not disable otherwise compatible native controls.
  function fallbackIcon(path) {
    return (props) =>
      React.createElement(
        "svg",
        {
          width: props.width || 20,
          height: props.height || 20,
          viewBox: "0 0 24 24",
          className: props.className,
          "aria-hidden": true,
        },
        React.createElement("path", { fill: "currentColor", d: path }),
      );
  }
  const AppIcon =
    discovery.components.AppIcon ||
    fallbackIcon(
      "M5 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h14a3 3 0 0 0 3-3V6a3 3 0 0 0-3-3H5Zm0 2h1v2H5V5Zm3 0h1v2H8V5Z",
    );
  const ScreenIcon =
    discovery.components.ScreenIcon ||
    fallbackIcon(
      "M5 2a3 3 0 0 0-3 3v10a3 3 0 0 0 3 3h6v2H8v2h8v-2h-3v-2h6a3 3 0 0 0 3-3V5a3 3 0 0 0-3-3H5Z",
    );
  const RefreshIcon =
    discovery.components.RefreshIcon ||
    fallbackIcon("M19 5V2h2v7h-7V7h3.7A7 7 0 1 0 19 14h2a9 9 0 1 1-2-9Z");
  const h = React.createElement;
  return new Promise((resolve) => {
    let finished = false,
      mountTimeout,
      observer;
    const session = {
      ids: new Set(request.options.map((o) => o.id)),
      icons: {},
      update: null,
      finish(result) {
        if (finished) return;
        if (result.selection && !session.mounted)
          result = { ...unavailable, reason: "premature-reply" };
        finished = true;
        clearTimeout(mountTimeout);
        observer?.disconnect();
        sessions.delete(request.key);
        session.update = null;
        try {
          api.closeModal(request.key);
        } catch {
          /* Tearing-down host. */
        } finally {
          resolve(result);
        }
      },
    };
    sessions.set(request.key, session);
    mountTimeout = setTimeout(
      () => session.finish({ ...unavailable, reason: "mount-timeout" }),
      2500,
    );
    class Boundary extends React.Component {
      constructor(props) {
        super(props);
        this.state = { failed: false };
      }
      static getDerivedStateFromError() {
        return { failed: true };
      }
      componentDidCatch() {
        session.finish(unavailable);
      }
      render() {
        return this.state.failed ? null : this.props.children;
      }
    }
    function Picker(props) {
      const [tab, setTab] = React.useState("apps"),
        [query, setQuery] = React.useState(""),
        [selected, setSelected] = React.useState(null),
        [icons, setIcons] = React.useState(session.icons);
      const scrollerRef = React.useRef(null),
        controlsRef = React.useRef(null),
        layoutRef = React.useRef(null);
      React.useEffect(() => {
        function escape(event) {
          if (
            event.key === "Escape" &&
            !event.defaultPrevented &&
            controlsRef.current?.closest('[data-mana-component="modal"]')?.contains(event.target)
          ) {
            event.preventDefault();
            session.finish({ selection: "none" });
          }
        }
        document.addEventListener("keydown", escape);
        return () => document.removeEventListener("keydown", escape);
      }, []);
      React.useEffect(() => {
        session.update = setIcons;
        setIcons({ ...session.icons });
        return () => {
          session.update = null;
        };
      }, []);
      const system = tab === "system",
        normalized = query.trim().toLocaleLowerCase();
      const apps = request.options.filter(
        (o) =>
          o.id !== "system" && `${o.label} ${o.detail}`.toLocaleLowerCase().includes(normalized),
      );
      React.useLayoutEffect(() => {
        let frame;
        function validate() {
          if (finished) return;
          const layout = layoutRef.current,
            root = layout?.closest('[data-mana-component="modal"]');
          const tabs = layout?.querySelectorAll('[role="tab"]'),
            action = layout?.querySelector(".dpe-native-primary button");
          const input = layout?.querySelector(".dpe-native-audio-search input");
          const sources = layout?.querySelectorAll('[role="radio"]');
          const valid =
            root?.isConnected &&
            tabs?.length === 2 &&
            action &&
            action.disabled === (!system && selected === null) &&
            tabs[system ? 1 : 0].getAttribute("aria-selected") === "true" &&
            (system ? !input : input?.value === query) &&
            sources?.length === (system ? 0 : apps.length) &&
            Array.from(sources).every(
              (node) =>
                node.getAttribute("aria-checked") === String(node.dataset.sourceId === selected),
            );
          if (!valid) {
            session.finish({ ...unavailable, reason: "render-contract" });
            return;
          }
          clearTimeout(mountTimeout);
          session.mounted = true;
          if (!observer) {
            observer = new MutationObserver(() => {
              if (!layout.isConnected) session.finish({ ...unavailable, reason: "host-removed" });
            });
            observer.observe(document.body, { childList: true, subtree: true });
          }
        }
        // Let native child effects complete before checking the actual DOM.
        frame = requestAnimationFrame(() => {
          frame = requestAnimationFrame(validate);
        });
        return () => cancelAnimationFrame(frame);
      }, [tab, query, selected]);
      React.useEffect(
        () => () => {
          Promise.resolve().then(() => {
            if (!finished && !layoutRef.current?.isConnected)
              session.finish({ ...unavailable, reason: "host-unmounted" });
          });
        },
        [],
      );
      function chooseTab(value) {
        value = typeof value === "string" ? value : value?.value;
        if (!["apps", "system"].includes(value)) {
          session.finish({ ...unavailable, reason: "tab-contract" });
          return;
        }

        if (value === tab) return;
        setSelected(null);
        setTab(value);
        const scroller = scrollerRef.current?.getScrollerNode?.() ?? scrollerRef.current;
        scroller?.scrollTo?.({ top: 0 });
      }
      function search(value) {
        value =
          typeof value === "string"
            ? value
            : (value?.currentTarget?.value ?? value?.target?.value ?? value?.value);
        if (typeof value !== "string") {
          session.finish({ ...unavailable, reason: "search-contract" });
          return;
        }
        setQuery(value);
        const scroller = scrollerRef.current?.getScrollerNode?.() ?? scrollerRef.current;
        scroller?.scrollTo?.({ top: 0 });
        const match = request.options.find((o) => o.id === selected);
        if (
          match &&
          !`${match.label} ${match.detail}`
            .toLocaleLowerCase()
            .includes(value.trim().toLocaleLowerCase())
        )
          setSelected(null);
      }
      const controls = h(
        "header",
        { className: "dpe-native-audio-controls", ref: controlsRef },
        h(SegmentedControl, {
          role: "tablist",
          look: "pill",
          className: "dpe-native-audio-tabs",
          optionClassName: "dpe-native-audio-tab",
          value: tab,
          onChange: chooseTab,
          options: [
            {
              name: "Applications",
              value: "apps",
              icon: AppIcon,
              "aria-controls": `${request.key}-sources`,
            },
            {
              name: "System Audio",
              value: "system",
              icon: ScreenIcon,
              "aria-controls": `${request.key}-sources`,
            },
          ],
        }),
      );
      const hint = system
        ? "Includes all applications except Discord."
        : "Includes windows and tabs from the same app.";
      const selection = system ? "system" : selected;
      const title = system ? "Share your system audio" : "Share audio from an application";
      const body = system
        ? h(
            "div",
            { className: "dpe-native-audio-source dpe-native-system" },
            h(ScreenIcon, { size: "custom", width: 48, height: 48, color: "currentColor" }),
            h("strong", null, "System audio"),
            h("small", null, "All applications except Discord"),
          )
        : h(
            "div",
            {
              className: "dpe-native-audio-sources",
              role: "radiogroup",
              "aria-label": "Audio source",
            },
            apps.length
              ? apps.map((o) =>
                  h(
                    Clickable,
                    {
                      className: "dpe-native-audio-source",
                      key: o.id,
                      role: "radio",
                      "aria-checked": selected === o.id,
                      "data-source-id": o.id,
                      "aria-label": `${o.label} — ${o.detail}`,
                      title: `${o.label} — ${o.detail}`,
                      tabIndex:
                        selected === o.id || (selected === null && apps[0]?.id === o.id) ? 0 : -1,
                      onClick: () => setSelected(o.id),
                      onKeyDown: (event) => {
                        if (
                          !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
                        )
                          return;
                        event.preventDefault();
                        const delta = ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1;
                        const index = (apps.indexOf(o) + delta + apps.length) % apps.length;
                        setSelected(apps[index].id);
                        event.currentTarget.parentElement
                          .querySelectorAll('[role="radio"]')
                          [index]?.focus();
                      },
                    },
                    h(
                      "span",
                      { className: "dpe-native-source-visual", "aria-hidden": true },
                      icons[o.id]
                        ? h("img", {
                            src: icons[o.id],
                            alt: "",
                            width: 48,
                            height: 48,
                            onError: () => setIcons((current) => ({ ...current, [o.id]: null })),
                          })
                        : h(AppIcon, {
                            size: "custom",
                            width: 36,
                            height: 36,
                            color: "currentColor",
                          }),
                    ),
                    h(
                      "span",
                      { className: "dpe-native-source-text" },
                      h("strong", null, o.label),
                      h("small", null, o.detail),
                    ),
                  ),
                )
              : h(
                  "p",
                  { className: "dpe-native-empty", role: "status" },
                  normalized
                    ? "No applications found. Try another search."
                    : "No applications found. Play some audio, then select Refresh.",
                ),
          );
      return h(
        ModalRoot,
        { ...props, size: "xl", "aria-label": title, fullScreenOnMobile: false },
        h("style", null, request.css),
        h(
          "div",
          {
            className: "dpe-native-audio-layout",
            ref: layoutRef,
          },
          controls,
          h(
            Scroller,
            {
              className: "dpe-native-audio-content",
              ref: scrollerRef,
              role: "tabpanel",
              id: `${request.key}-sources`,
              "aria-label": system ? "System Audio" : "Applications",
            },
            h("h2", null, title),
            !system &&
              h(
                "div",
                { className: "dpe-native-audio-search" },
                h(SearchBar, {
                  query,
                  onChange: search,
                  onClear: () => search(""),
                  placeholder: "Search",
                  "aria-label": "Search applications",
                }),
              ),
            body,
          ),
          h(
            "footer",
            { className: "dpe-native-audio-footer" },
            h(
              "div",
              { className: "dpe-native-audio-summary" },
              h("strong", null, "Choose audio source"),
              h("p", null, hint),
            ),
            h(
              "div",
              { className: "dpe-native-audio-actions" },
              h(
                "span",
                { className: "dpe-native-skip" },
                h(Button, {
                  text: "Continue without audio",
                  variant: "secondary",
                  onClick: () => session.finish({ selection: "none" }),
                }),
              ),
              h(
                "span",
                { className: "dpe-native-primary" },
                h(Button, {
                  text: system ? "Share Audio" : "Make Selection",
                  variant: "primary",
                  size: "md",
                  disabled: selection === null,
                  onClick: () => session.finish({ selection }),
                }),
              ),
              !system &&
                h(
                  "span",
                  { className: "dpe-native-refresh" },
                  h(Button, {
                    icon: RefreshIcon,
                    variant: "secondary",
                    size: "md",
                    "aria-label": "Refresh applications",
                    onClick: () => session.finish({ selection: "refresh" }),
                  }),
                ),
            ),
          ),
        ),
      );
    }
    try {
      api.openModal((props) => h(Boundary, null, h(Picker, props)), {
        modalKey: request.key,
        dismissable: true,
        onCloseRequest: () => session.finish({ selection: "none" }),
        onCloseCallback: () => session.finish({ selection: "none" }),
      });
    } catch {
      session.finish(unavailable);
    }
  });
};
