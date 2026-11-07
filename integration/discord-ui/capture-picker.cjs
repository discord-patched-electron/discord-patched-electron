"use strict";
// Extend the native modal's returned element tree, not its factory source or
// private control exports. Discord retains its lifecycle, quality/audio controls
// and start action. Unknown shapes keep the original picker intact.
module.exports = function capturePicker(request, ui, makeView) {
  if (
    request?.platform !== "linux" ||
    typeof makeView !== "function" ||
    typeof window === "undefined" ||
    typeof window.addEventListener !== "function" ||
    typeof document === "undefined"
  )
    return;
  const key = Symbol.for("dpe.capturePickerUI");
  if (globalThis[key]) {
    globalThis[key].configure(request);
    return;
  }
  const listeners = new Set(),
    pending = new Map(),
    installed = new WeakSet();
  const state = {
    enabled: request.experimentalCapture === true,
    configure(value) {
      state.enabled = value.experimentalCapture === true;
      for (const listener of listeners) listener();
    },
  };
  globalThis[key] = state;
  const tabKey = "dpe.capture-picker.last-tab";
  let lastTab = "dpe-optimized";
  try {
    const stored = window.localStorage?.getItem(tabKey);
    if (["dpe-optimized", "window", "screen", "camera"].includes(stored)) lastTab = stored;
  } catch {
    /* Storage may be unavailable in a temporary profile. */
  }
  function remember(value) {
    lastTab = value;
    try {
      window.localStorage?.setItem(tabKey, value);
    } catch {
      /* Memory still retains the choice. */
    }
  }
  const vanilla = {
    token: "dpe-vanilla-window",
    kind: "portal",
    name: "Other window (Vanilla)",
    available: true,
  };
  const icon = (name) =>
    globalThis[Symbol.for("dpe.discordPresentation")]?.icons?.[name] || request.icons?.[name] || "";
  function rpc(op, token, size) {
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(Error("Capture selection is unavailable. Try Vanilla or reopen the picker."));
      }, 15000);
      pending.set(id, (reply) => {
        clearTimeout(timer);
        if (reply.error) reject(Error(reply.error));
        else resolve(reply.value);
      });
      window.dispatchEvent(
        new CustomEvent("dpe-capture-picker-request", {
          detail: JSON.stringify({ id, op, token, ...size }),
        }),
      );
    });
  }
  window.addEventListener("dpe-capture-picker-reply", (event) => {
    if (typeof event.detail !== "string" || event.detail.length > 240000) return;
    try {
      const reply = JSON.parse(event.detail),
        receive = pending.get(reply.id);
      if (!receive) return;
      pending.delete(reply.id);
      receive(reply);
    } catch {
      /* Ignore unrelated page events. */
    }
  });
  function stylesheet() {
    if (!document.getElementById("dpe-capture-picker-style")) {
      const style = document.createElement("style");
      style.id = "dpe-capture-picker-style";
      style.textContent = request.styles || "";
      (document.head || document.documentElement).append(style);
    }
  }
  function walk(node, visit, depth = 0) {
    if (depth > 14) return;
    if (Array.isArray(node)) {
      for (const child of node) walk(child, visit, depth + 1);
      return;
    }
    if (!node || typeof node !== "object" || !node.props) return;
    visit(node);
    walk(node.props.children, visit, depth + 1);
  }
  function contract(tree) {
    const tabs = [],
      panes = [],
      starts = [],
      cogs = [];
    walk(tree, (node) => {
      const p = node.props;
      if (
        p.role === "tablist" &&
        typeof p.onChange === "function" &&
        Array.isArray(p.options) &&
        ["window", "camera"].every((value) => p.options.some((item) => item.value === value))
      )
        tabs.push(node);
      if (typeof p.onSourceSelect === "function") panes.push(node);
      if (p.align === "right" && typeof node.type === "function") {
        const signature = ui.signature(node.type);
        if (
          signature &&
          ui.has(signature, "targetElementRef", "renderPopout", "closePopout", "aria-label")
        )
          cogs.push(node);
      }
      if (typeof p.onClick === "function") {
        const signature = ui.signature(p.onClick);
        if (signature && ui.has(signature, "nativeSourceType", "selectedChannel", "selectedSource"))
          starts.push(node);
      }
    });
    if (
      tabs.length !== 1 ||
      panes.length > 1 ||
      starts.length > 1 ||
      typeof tree?.props?.dispatch !== "function" ||
      typeof tree.props.state?.sourceType !== "string"
    )
      return null;
    // Devices remains native. A changed non-native capture layout is untouched.
    if (tree.props.state.sourceType !== "camera" && (panes.length !== 1 || starts.length !== 1))
      return null;
    return {
      tabs: tabs[0],
      pane: panes[0],
      start: starts[0],
      cog: cogs.length === 1 ? cogs[0] : null,
      dispatch: tree.props.dispatch,
    };
  }
  function components(React) {
    const h = React.createElement,
      Sources = makeView(React, icon);
    function Bolt({ className, width, height, size, color = "currentColor" }) {
      const pixels = typeof size === "number" ? size : size === "xxs" ? 16 : 20;
      return h("span", {
        className,
        "aria-hidden": true,
        style: {
          display: "inline-flex",
          width: width || (className ? undefined : pixels),
          height: height || (className ? undefined : pixels),
          color,
          flexShrink: 0,
        },
        dangerouslySetInnerHTML: {
          __html: icon("bolt").replace(
            /<svg /,
            '<svg style="width:100%;height:100%;display:block" ',
          ),
        },
      });
    }
    const cogWrappers = new WeakMap(),
      menuWrappers = new WeakMap();
    function menuWrapper(original) {
      if (!menuWrappers.has(original))
        menuWrappers.set(original, function Menu(props) {
          const tree = original(props);
          let advanced;
          walk(tree, (node) => {
            if (node.props.id === "advanced-items") advanced = node;
          });
          if (!advanced) return tree;
          function insert(node) {
            if (node === advanced)
              return h(
                React.Fragment,
                null,
                h(advanced.type, {
                  id: "dpe-open-screensharing",
                  label: "Open DPE settings",
                  action: () => {
                    props.onClose?.();
                    void rpc("open-settings").catch(() => {});
                  },
                }),
                node,
              );
            if (Array.isArray(node)) return node.map(insert);
            if (!node?.props?.children) return node;
            return React.cloneElement(node, undefined, insert(node.props.children));
          }
          return insert(tree);
        });
      return menuWrappers.get(original);
    }
    function cogWrapper(original) {
      if (!cogWrappers.has(original))
        cogWrappers.set(original, function Cog(props) {
          const tree = original(props);
          if (typeof tree?.props?.renderPopout !== "function") return tree;
          return React.cloneElement(tree, {
            renderPopout: (...args) => {
              const menu = tree.props.renderPopout(...args);
              return typeof menu?.type === "function"
                ? h(menuWrapper(menu.type), menu.props)
                : menu;
            },
          });
        });
      return cogWrappers.get(original);
    }
    class Boundary extends React.Component {
      constructor(props) {
        super(props);
        this.state = { failed: false };
      }
      static getDerivedStateFromError() {
        return { failed: true };
      }
      render() {
        return this.state.failed ? this.props.tree : h(Enhance, this.props);
      }
    }
    function Enhance({ tree }) {
      const [enabled, setEnabled] = React.useState(state.enabled);
      const [optimized, setOptimized] = React.useState(lastTab === "dpe-optimized");
      const [sources, setSources] = React.useState([]);
      const [selected, setSelected] = React.useState("");
      const [loading, setLoading] = React.useState(false);
      const [busy, setBusy] = React.useState(false);
      const [error, setError] = React.useState("");
      const live = React.useRef(true),
        flight = React.useRef(false),
        choosing = React.useRef(false),
        handingOff = React.useRef(false);
      const generation = React.useRef(0);
      const shape = contract(tree),
        nativePane = Boolean(shape?.pane);
      const active = enabled && optimized && nativePane;
      React.useEffect(() => {
        live.current = true;
        const update = () => {
          setEnabled(state.enabled);
          if (state.enabled) setOptimized(lastTab === "dpe-optimized");
          else {
            generation.current++;
            choosing.current = false;
            setBusy(false);
            void rpc("disarm").catch(() => {});
          }
        };
        listeners.add(update);
        return () => {
          live.current = false;
          generation.current++;
          listeners.delete(update);
          if (!handingOff.current) void rpc("disarm").catch(() => {});
          void rpc("picker-close", undefined, { handoff: handingOff.current }).catch(() => {});
        };
      }, []);
      React.useEffect(() => {
        if (!enabled || !shape) return;
        const desired = lastTab === "dpe-optimized" ? "window" : lastTab;
        if (
          shape.tabs.props.options.some((option) => option.value === desired) &&
          shape.tabs.props.value !== desired
        )
          shape.tabs.props.onChange({ value: desired });
      }, [enabled]);
      const refresh = React.useCallback(async () => {
        if (flight.current || choosing.current) return;
        flight.current = true;
        setLoading(true);
        try {
          const value = await rpc("list");
          if (!live.current) return;
          if (!value?.enabled || !Array.isArray(value.sources))
            throw Error("Experimental capture is unavailable. Try Vanilla or check DPE Settings.");
          const next = value.sources.some((source) => source.available)
            ? [...value.sources, vanilla]
            : [];
          setSources(next);
          setSelected((current) =>
            next.some((source) => source.token === current && source.available) ? current : "",
          );
          setError("");
        } catch (failure) {
          if (live.current) setError(failure.message);
        } finally {
          flight.current = false;
          if (live.current) setLoading(false);
        }
      }, []);
      React.useEffect(() => {
        if (!active) {
          if (!handingOff.current) void rpc("disarm").catch(() => {});
          return;
        }
        stylesheet();
        void refresh();
        const timer = setInterval(() => {
          if (!choosing.current) void refresh();
        }, 5000);
        return () => {
          clearInterval(timer);
          void rpc("picker-close", undefined, { handoff: handingOff.current }).catch(() => {});
        };
      }, [active, refresh]);
      React.useEffect(() => {
        const result = (event) => {
          if (!choosing.current || typeof event.detail !== "string") return;
          try {
            const value = JSON.parse(event.detail);
            if (!["error", "cancel", "native"].includes(value.action)) return;
            choosing.current = false;
            setBusy(false);
            if (value.action !== "cancel")
              setError(
                value.error || "Capture could not start. Choose another source or try Vanilla.",
              );
          } catch {
            /* Invalid bridge event. */
          }
        };
        window.addEventListener("dpe-capture-picker-result", result);
        return () => window.removeEventListener("dpe-capture-picker-result", result);
      }, []);
      if (!enabled || !shape) return tree;
      const choose = (token) => {
        const source = sources.find((item) => item.token === token && item.available);
        if (!source || busy) return;
        setSelected(token);
        setError("");
        shape.dispatch({
          type: "set_native_source_type",
          sourceType: source.kind === "screen" ? "screen" : "window",
        });
      };
      const start = async () => {
        if (
          choosing.current ||
          !sources.some((source) => source.token === selected && source.available)
        )
          return;
        choosing.current = true;
        const attempt = ++generation.current;
        setBusy(true);
        setError("");
        try {
          await rpc("preview-stop");
          await rpc(selected === vanilla.token ? "disarm" : "arm", selected);
          if (!live.current || !state.enabled || attempt !== generation.current) {
            if (attempt === generation.current) {
              choosing.current = false;
              if (live.current) setBusy(false);
              await rpc("disarm");
            }
            return;
          }
          // This is Discord's original callback: release previous native picker,
          // present the new one, and dispatch its Present store event. Its hidden
          // source pane will handle Update with the current quality/audio state.
          handingOff.current = selected !== vanilla.token;
          await shape.start.props.onClick();
        } catch (failure) {
          handingOff.current = false;
          if (live.current) {
            setError(failure.message);
            setBusy(false);
          }
          choosing.current = false;
          void rpc("disarm").catch(() => {});
        }
      };
      const replacements = new Map();
      const cog = shape.cog && h(cogWrapper(shape.cog.type), shape.cog.props);
      if (cog) replacements.set(shape.cog, cog);
      replacements.set(
        shape.tabs,
        React.cloneElement(shape.tabs, {
          value: active ? "dpe-optimized" : shape.tabs.props.value,
          options: [
            { name: "Optimized", value: "dpe-optimized", icon: Bolt },
            ...shape.tabs.props.options.map((option) =>
              option.value === "window" ? { ...option, name: "Vanilla" } : option,
            ),
          ],
          onChange(value) {
            if (busy) return;
            const optimized = value?.value === "dpe-optimized";
            remember(value.value);
            setOptimized(optimized);
            setError("");
            void rpc("disarm").catch(() => {});
            shape.tabs.props.onChange(optimized ? { ...value, value: "window" } : value);
          },
        }),
      );
      if (shape.pane) {
        replacements.set(
          shape.pane,
          h(
            React.Fragment,
            null,
            h(
              "div",
              {
                hidden: active,
                inert: active ? true : undefined,
                style: { display: active ? "none" : "contents" },
              },
              shape.pane,
            ),
            active &&
              h(Sources, {
                sources,
                selected,
                loading,
                error,
                busy,
                onSelect: choose,
                onPreview: (token, width, height) => rpc("preview", token, { width, height }),
                onPreviewStop: () => {
                  void rpc("preview-stop").catch(() => {});
                },
              }),
          ),
        );
      }
      if (active) {
        const refreshButton = h(
          "span",
          { className: "dpe-ui dpe-capture-refresh-host" },
          h(
            "button",
            {
              type: "button",
              className: "dpe-button dpe-button--secondary dpe-capture-refresh",
              title: "Refresh sources",
              "aria-label": "Refresh sources",
              disabled: loading || busy,
              onClick: refresh,
            },
            h("span", {
              className: "dpe-icon",
              "aria-hidden": true,
              dangerouslySetInnerHTML: { __html: icon("refresh") },
            }),
          ),
        );
        if (shape.cog) replacements.set(shape.cog, h(React.Fragment, null, refreshButton, cog));
        else if (shape.start) {
          // If Discord moves its cog, keep refresh beside the native footer
          // action instead of guessing a private component or copying it.
          const original = replacements.get(shape.start) || shape.start;
          replacements.set(shape.start, h(React.Fragment, null, original, refreshButton));
        }
      }
      if (active && shape.start) {
        const startNode = React.cloneElement(shape.start, {
          disabled:
            busy || !sources.some((source) => source.token === selected && source.available),
          onClick: start,
        });
        const existing = replacements.get(shape.start);
        replacements.set(
          shape.start,
          existing
            ? React.cloneElement(existing, undefined, startNode, existing.props.children[1])
            : startNode,
        );
      }
      function replace(node) {
        if (replacements.has(node)) return replacements.get(node);
        if (Array.isArray(node)) return node.map(replace);
        if (!node || typeof node !== "object" || !node.props || node.props.children == null)
          return node;
        return React.cloneElement(node, undefined, replace(node.props.children));
      }
      return replace(tree);
    }
    return Boundary;
  }
  ui.watch(
    "dpe-optimized-picker",
    (source) =>
      ["useCreateGoLiveModalState", "GO_LIVE_MODAL_V2", "set_source_type", "onSourceSelect"].every(
        (word) => source.includes(word),
      ),
    (exports, runtime) => {
      const scan = ui.inspect({ c: { picker: { exports } } });
      const choices = scan.candidates.filter(
        ({ sig, value }) =>
          typeof value === "function" &&
          ui.has(
            sig,
            "useCreateGoLiveModalState",
            "GO_LIVE_MODAL_V2",
            "set_source_type",
            "onSourceSelect",
          ),
      );
      if (choices.length !== 1 || installed.has(choices[0].value)) return;
      const original = choices[0].value;
      const React = ui.inspect(runtime).React;
      if (!React || typeof React.cloneElement !== "function") return;
      const entries = Object.values(runtime.c || {}).filter((entry) => entry?.exports === exports);
      const keys = Object.keys(exports).filter((name) => ui.safe(exports, name) === original);
      if (entries.length !== 1 || keys.length !== 1) return;
      const Boundary = components(React);
      const wrapped = function (...args) {
        const tree = Reflect.apply(original, this, args);
        return React.createElement(Boundary, { tree });
      };
      wrapped.toString = () => original.toString();
      // Webpack's non-configurable export getters cannot be assigned. A proxy
      // preserves every other export without evaluating private presentation modules.
      entries[0].exports = new Proxy(exports, {
        get(target, name, receiver) {
          return name === keys[0] ? wrapped : Reflect.get(target, name, receiver);
        },
      });
      installed.add(original);
      installed.add(wrapped);
    },
  );
};
