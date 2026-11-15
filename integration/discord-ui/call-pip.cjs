"use strict";
// Reuse Discord's actual call-control component and native open/confirmation
// callback. Only the explicit PiP entry opts its next call-window request in.
module.exports = function callPiP(request, ui) {
  const key = Symbol.for("dpe.callPiP");
  if (globalThis[key]) return;
  const state = { pending: false, timer: undefined };
  const clear = () => {
    state.pending = false;
    clearTimeout(state.timer);
  };
  state.consume = (name) => {
    if (name !== "DISCORD_CHANNEL_CALL_POPOUT" || !state.pending) return false;
    clear();
    return true;
  };
  globalThis[key] = state;
  window.addEventListener("pagehide", clear, { once: true });
  const installed = new WeakSet();
  const overlays = new WeakMap();
  let React;
  const unavailable = () =>
    globalThis[Symbol.for("dpe.pipWindow")] === true ||
    (state.activeWindow && !state.activeWindow.closed);
  const openPiP = (open, args) => {
    clear();
    state.pending = true;
    state.timer = setTimeout(clear, 60000);
    try {
      const result = open(...args);
      if (result?.catch)
        return result.catch((error) => {
          clear();
          throw error;
        });
      return result;
    } catch (error) {
      clear();
      throw error;
    }
  };
  const pipCallback =
    (open) =>
    (...args) =>
      openPiP(open, args);
  ui.watch(
    "call-pip-button",
    (source) =>
      ["popoutOpen", "onClosePopout", "onOpenPopout", "WindowLaunchIcon"].every((word) =>
        source.includes(word),
      ) ||
      ["popoutWindowAlwaysOnTop", "onToggleStayOnTop"].every((word) => source.includes(word)) ||
      ["renderBottomRightControls", "renderSettingsIcon", "renderPopoutIcon", "VIDEO_PIP"].every(
        (word) => source.includes(word),
      ),
    (exports, runtime) => {
      React ||= ui.inspect(runtime).React;
      if (!React) return;
      const matches = Object.entries(exports || {}).filter(([, value]) => {
        const fn = ui.unwrap(value),
          sig = fn && ui.signature(fn);
        return (
          typeof value === "function" &&
          sig &&
          (ui.has(sig, "popoutOpen", "onClosePopout", "onOpenPopout", "iconComponent", "label") ||
            ui.has(sig, "popoutWindowAlwaysOnTop", "onToggleStayOnTop") ||
            ui.has(sig, "participantOnScreen", "isMainWindowFocused", "activeSelfStream", "width"))
        );
      });
      const entries = Object.values(runtime.c || {}).filter((entry) => entry?.exports === exports);
      if (matches.length !== 1 || entries.length !== 1 || installed.has(matches[0][1])) return;
      const [name, original] = matches[0];
      function PiPIcon(props) {
        const svg =
          globalThis[Symbol.for("dpe.discordPresentation")]?.icons?.pip || request.icons?.pip || "";
        return React.createElement("span", {
          className: props.className,
          "aria-hidden": true,
          style: {
            display: "inline-flex",
            width: props.width || 24,
            height: props.height || 24,
            // Overlay controls already carry Discord's light-on-video icon
            // classes. Let those resolve their color just like the native cog.
            ...(props.nativeColor
              ? {}
              : {
                  color: "var(--interactive-normal, var(--interactive-text-default, currentColor))",
                }),
          },
          dangerouslySetInnerHTML: {
            __html: svg.replace(/<svg /, '<svg style="width:100%;height:100%" '),
          },
        });
      }
      function OverlayIcon(props) {
        return React.createElement(PiPIcon, { ...props, nativeColor: true });
      }
      const contexts = (ui.inspect(runtime).objects || []).filter(
        (value) =>
          value?.$$typeof === Symbol.for("react.context") &&
          value._currentValue?.renderWindow &&
          value._currentValue.windowDispatch &&
          typeof value._currentValue.appContext === "string",
      );
      let Wrapper;
      if (ui.has(ui.signature(original), "popoutWindowAlwaysOnTop", "onToggleStayOnTop")) {
        const contexts = (ui.inspect(runtime).objects || []).filter(
          (value) =>
            value?.$$typeof === Symbol.for("react.context") &&
            value._currentValue?.renderWindow &&
            value._currentValue.windowDispatch &&
            typeof value._currentValue.appContext === "string",
        );
        if (contexts.length !== 1) return;
        Wrapper = function (props) {
          const context = React.useContext(contexts[0]);
          if (
            context?.appContext === "POPOUT" &&
            context.renderWindow?.[Symbol.for("dpe.pipWindow")] === true
          )
            return null;
          return React.createElement(original, props);
        };
      } else if (
        ui.has(
          ui.signature(original),
          "participantOnScreen",
          "isMainWindowFocused",
          "activeSelfStream",
          "width",
        )
      ) {
        Wrapper = function (props) {
          const tree = Reflect.apply(original, this, [props]);
          const sig = typeof tree?.type === "function" && ui.signature(tree.type);
          if (
            !sig ||
            !ui.has(
              sig,
              "renderBottomRightControls",
              "renderSettingsIcon",
              "renderPopoutIcon",
              "handleOpenPopout",
            )
          )
            return tree;
          let Overlay = overlays.get(tree.type);
          if (!Overlay) {
            Overlay = class extends tree.type {
              constructor(...args) {
                super(...args);
                const render = this.renderBottomRightControls,
                  settings = this.renderSettingsIcon;
                if (
                  typeof render !== "function" ||
                  typeof settings !== "function" ||
                  typeof this.renderPopoutIcon !== "function" ||
                  typeof this.handleOpenPopout !== "function"
                )
                  return;
                let settingsNode;
                this.renderSettingsIcon = (...args) => {
                  settingsNode = Reflect.apply(settings, this, args);
                  return settingsNode;
                };
                this.renderBottomRightControls = (...args) => {
                  settingsNode = null;
                  const controls = Reflect.apply(render, this, args);
                  if (unavailable() || !Array.isArray(controls?.props?.children)) return controls;
                  const children = [...controls.props.children],
                    template = this.renderPopoutIcon();
                  if (
                    !template?.props ||
                    template.props.onOpenPopout !== this.handleOpenPopout ||
                    template.props.popoutOpen !== false
                  )
                    return controls;
                  const pip = React.cloneElement(template, {
                    key: "dpe-overlay-pip",
                    label: "Picture-in-picture",
                    iconComponent: OverlayIcon,
                    iconClassName: undefined,
                    onOpenPopout: pipCallback(this.handleOpenPopout),
                  });
                  const index = settingsNode ? children.indexOf(settingsNode) : -1;
                  // Place it after the actual cog when present, otherwise next
                  // to the native popout; retain all native controls and nulls.
                  const popout = children.findIndex(
                    (child) => child?.props?.onOpenPopout === this.handleOpenPopout,
                  );
                  if (settingsNode && index < 0) return controls;
                  children.splice(index >= 0 ? index + 1 : popout >= 0 ? popout : 0, 0, pip);
                  return React.cloneElement(controls, { children });
                };
              }
            };
            overlays.set(tree.type, Overlay);
          }
          return React.createElement(Overlay, { ...tree.props, key: tree.key, ref: tree.ref });
        };
      } else
        Wrapper = function (props) {
          const tree = Reflect.apply(original, this, [props]);
          const context = contexts.length === 1 ? React.useContext(contexts[0]) : null;
          if (context?.renderWindow?.[Symbol.for("dpe.pipWindow")] === true || unavailable())
            return tree;
          const open = ui.signature(props.onOpenPopout);
          // Activity/debug controls share this component. Add PiP only to the
          // native whole-call callback, never to those other popout types.
          if (
            !tree?.props ||
            !open ||
            !ui.has(open, "handleOpenPopout") ||
            typeof tree.props.onClick !== "function"
          )
            return tree;
          const stock = React.cloneElement(tree, {
            onClick: (...args) => {
              clear();
              return tree.props.onClick(...args);
            },
          });
          const pip = React.cloneElement(tree, {
            key: "dpe-call-pip",
            label: "Picture-in-picture",
            iconComponent: PiPIcon,
            iconClassName: undefined,
            disabled: props.popoutOpen === true,
            onClick: (...args) => {
              if (props.popoutOpen) return;
              return openPiP(props.onOpenPopout, args);
            },
          });
          return React.createElement(React.Fragment, null, pip, stock);
        };
      installed.add(original);
      installed.add(Wrapper);
      entries[0].exports = new Proxy(Object.create(null), {
        get: (_target, property, receiver) =>
          property === name ? Wrapper : Reflect.get(exports, property, receiver),
        ownKeys: () => Reflect.ownKeys(exports),
        has: (_target, property) => Reflect.has(exports, property),
        getOwnPropertyDescriptor: (_target, property) => {
          const d = Reflect.getOwnPropertyDescriptor(exports, property);
          return d ? { ...d, configurable: true } : undefined;
        },
      });
    },
  );
};
