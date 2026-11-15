"use strict";
// Discord renders call popouts as portals in the opener. Scope changes using
// its native WindowContext at the participant-view/menu boundary, never by
// changing the shared ChannelRTCStore or its persisted regular-call toggle.
module.exports = function pipParticipants(request, ui) {
  const key = Symbol.for("dpe.pipParticipants");
  if (globalThis[key]) {
    globalThis[key].configure(request);
    return;
  }
  const listeners = new Set(),
    stores = new Set(),
    users = new Set(),
    actions = new Set(),
    contexts = new Set();
  const wrapped = new WeakSet(),
    windows = new WeakMap(),
    callViews = new WeakMap();
  let React;
  let revision = 0;
  let hideMembers = request.hideMembers === true;
  let saved = request.showNonVideoParticipants === true;
  const state = {
    configure(next) {
      const show = next.showNonVideoParticipants === true;
      const hide = next.hideMembers === true;
      if (show === saved && hide === hideMembers) return;
      hideMembers = hide;
      if (show !== saved) {
        saved = show;
        revision++;
      }
      for (const update of listeners) update();
    },
  };
  globalThis[key] = state;
  function pip(context) {
    try {
      return (
        context?.appContext === "POPOUT" &&
        context.renderWindow?.[Symbol.for("dpe.pipWindow")] === true &&
        [context.renderWindow?.windowKey, context.renderWindow?.name].includes(
          "DISCORD_CHANNEL_CALL_POPOUT",
        ) &&
        !context.renderWindow.closed
      );
    } catch {
      return false;
    }
  }
  function settings(target) {
    let value = windows.get(target);
    if (!value) {
      value = {
        show: saved,
        revision,
        attempted: new Set(),
        stopped: false,
        restore: [],
        memberSelections: new Map(),
        restoreMemberChannels: new Set(),
      };
      windows.set(target, value);
      const stop = () => {
        value.stopped = true;
      };
      target.addEventListener("pointerdown", stop, { once: true, capture: true });
      target.addEventListener("keydown", stop, { once: true, capture: true });
      target.addEventListener(
        "pagehide",
        () => {
          stop();
          for (const restore of value.restore) {
            try {
              restore();
            } catch {
              /* Closing preserves changed native state. */
            }
          }
        },
        { once: true },
      );
    }
    if (value.revision !== revision) {
      value.revision = revision;
      value.show = saved;
    }
    return value;
  }
  function media(store, channel) {
    return [
      ...store.getStreamParticipants(channel),
      ...store.getVideoParticipants(channel),
      ...store.getActivityParticipants(channel),
    ];
  }
  function minimizeMembers(target, channel) {
    const local = settings(target);
    if (!hideMembers) {
      local.memberSelections.delete(channel);
      return;
    }
    if (target.closed || stores.size !== 1 || actions.size !== 1) return;
    const store = [...stores][0],
      action = [...actions][0];
    if (
      typeof store.getSelectedParticipant !== "function" ||
      typeof store.getParticipantsOpen !== "function"
    )
      return;
    const selected = JSON.stringify([
      store.getSelectedParticipant(channel)?.id ?? null,
      store.getLayout?.(channel, "POPOUT"),
    ]);
    if (local.memberSelections.has(channel) && local.memberSelections.get(channel) === selected)
      return;
    const sig = ui.signature(action.toggleParticipants);
    if (!sig || !ui.has(sig, "CHANNEL_RTC_UPDATE_PARTICIPANTS_OPEN", "channelId")) return;
    local.memberSelections.set(channel, selected);
    if (!store.getParticipantsOpen(channel)) return;
    if (!local.restoreMemberChannels.has(channel)) {
      local.restoreMemberChannels.add(channel);
      local.restore.push(() => {
        if (store.getParticipantsOpen(channel) === false) action.toggleParticipants(channel, true);
      });
    }
    action.toggleParticipants(channel, false);
  }
  function focus(target, channel) {
    const local = settings(target);
    if (
      local.stopped ||
      local.attempted.has(channel) ||
      target.closed ||
      stores.size !== 1 ||
      actions.size !== 1
    )
      return;
    const store = [...stores][0],
      action = [...actions][0];
    const previousLayout = store.getLayout?.(channel, "POPOUT");
    const previousSelected = store.getSelectedParticipant?.(channel);
    const streams = store.getStreamParticipants(channel),
      me = users.size === 1 ? [...users][0].getCurrentUser()?.id : undefined;
    if (streams.length && typeof me !== "string") return;
    if (streams.some((p) => typeof (p.stream?.ownerId || p.user?.id) !== "string")) return;
    const eligible = [
      ...streams.filter((p) => (p.stream?.ownerId || p.user?.id) !== me),
      ...store.getVideoParticipants(channel),
      ...store.getActivityParticipants(channel),
    ];
    const ids = new Set(eligible.filter((p) => !p.isPoppedOut).map((p) => p.id));
    const entry = globalThis[Symbol.for("dpe.callPiP")];
    const origin =
      entry?.activeWindow === target && entry.origin?.channelId === channel
        ? entry.origin.participantId
        : null;
    const preferred = typeof origin === "string" && ids.has(origin) ? origin : null;
    if (
      previousLayout === "full-screen" &&
      previousSelected?.id &&
      (!preferred || preferred === previousSelected.id)
    ) {
      local.attempted.add(channel);
      return;
    }
    const selected = preferred || (ids.size === 1 ? [...ids][0] : null);
    if (typeof selected !== "string") return;
    const sig = ui.signature(action.updateLayout);
    if (!sig || !ui.has(sig, "CHANNEL_RTC_UPDATE_LAYOUT", "channelId", "appContext")) return;
    local.attempted.add(channel);
    action.selectParticipant(channel, selected);
    if (previousLayout !== "full-screen") action.updateLayout(channel, "full-screen", "POPOUT");
    if (typeof store.getSelectedParticipant === "function")
      local.restore.push(() => {
        if (store.getSelectedParticipant(channel)?.id === selected)
          action.selectParticipant(channel, previousSelected?.id ?? null);
      });
    if (typeof previousLayout === "string" && previousLayout !== "full-screen")
      local.restore.push(() => {
        if (store.getLayout?.(channel, "POPOUT") === "full-screen")
          action.updateLayout(channel, previousLayout, "POPOUT");
      });
  }
  ui.watch(
    "pip-participants",
    (source) =>
      [
        "ChannelRTCStore",
        "UserStore",
        "CHANNEL_RTC_SELECT_PARTICIPANT",
        "renderWindow",
        "getVoiceParticipantsHidden",
      ].some((word) => source.includes(word)) ||
      ["getSelectedParticipant", "getLayout", "guildRoomVisible", "callContainerDimensions"].every(
        (word) => source.includes(word),
      ),
    (exports, runtime) => {
      React ||= ui.inspect(runtime).React;
      const entries = Object.entries(runtime.c || {}).filter(
        ([, entry]) => entry?.exports === exports,
      );
      for (const value of [exports, ...Object.values(exports || {})]) {
        if (!value || !["object", "function"].includes(typeof value)) continue;
        if (
          [
            "getParticipants",
            "getStreamParticipants",
            "getVideoParticipants",
            "getActivityParticipants",
            "getVoiceParticipantsHidden",
          ].every((name) => typeof value[name] === "function")
        )
          stores.add(value);
        if (typeof value.getCurrentUser === "function" && typeof value.getUser === "function")
          users.add(value);
        if (typeof value.selectParticipant === "function") {
          const select = ui.signature(value.selectParticipant),
            toggle = ui.signature(value.toggleParticipants);
          // The legacy member-bar adapter may already wrap selectParticipant.
          if (
            (select && ui.has(select, "CHANNEL_RTC_SELECT_PARTICIPANT", "channelId")) ||
            (toggle && ui.has(toggle, "CHANNEL_RTC_UPDATE_PARTICIPANTS_OPEN", "channelId"))
          )
            actions.add(value);
        }
        if (
          value.$$typeof === Symbol.for("react.context") &&
          value._currentValue?.renderWindow &&
          typeof value._currentValue.appContext === "string" &&
          value._currentValue.windowDispatch
        )
          contexts.add(value);
        // Preserve Discord’s sizing wrapper and hooks, then adjust only the
        // validated participant element it returns inside the PiP portal.
        if (!React || contexts.size !== 1 || wrapped.has(value)) continue;
        const original = ui.unwrap(value),
          sig = original && ui.signature(original);
        if (!sig) continue;
        const nativeView = ui.has(
          sig,
          "filteredParticipants",
          "participantsVersion",
          "totalNumberOfParticipants",
          "getVoiceParticipantsHidden",
        );
        const factory = entries.length === 1 ? String(runtime.m?.[entries[0][0]] || "") : "";
        const view =
          nativeView ||
          [
            "filteredParticipants",
            "participantsVersion",
            "totalNumberOfParticipants",
            "getVoiceParticipantsHidden",
          ].every((word) => factory.includes(word));
        const menu = ui.has(
          sig,
          "no-video-hide",
          "getVoiceParticipantsHidden",
          "toggleVoiceParticipantsHidden",
        );
        const call = ui.has(
          sig,
          "getSelectedParticipant",
          "getLayout",
          "getMode",
          "guildRoomVisible",
          "callContainerDimensions",
          "popoutType",
        );
        if (!view && !menu && !call) continue;

        const Context = [...contexts][0];
        const Wrapper = function (props) {
          const context = React.useContext(Context),
            enabled = pip(context);
          const [, update] = React.useState(0);
          React.useEffect(() => {
            const refresh = () => update((n) => n + 1);
            listeners.add(refresh);
            return () => listeners.delete(refresh);
          }, []);
          const local = enabled ? settings(context.renderWindow) : null;
          React.useEffect(() => {
            if ((!view && !call) || !enabled || !props.channel?.id) return;
            let timer;
            const run = () => {
              clearTimeout(timer);
              timer = setTimeout(() => {
                try {
                  focus(context.renderWindow, props.channel.id);
                  minimizeMembers(context.renderWindow, props.channel.id);
                } catch {
                  /* Changed native contracts keep their selection. */
                }
              }, 0);
            };
            const store = stores.size === 1 ? [...stores][0] : null;
            const subscribable =
              typeof store?.addChangeListener === "function" &&
              typeof store.removeChangeListener === "function";
            run();
            if (subscribable) store.addChangeListener(run);
            return () => {
              clearTimeout(timer);
              if (subscribable) store.removeChangeListener(run);
            };
          }, [
            enabled,
            hideMembers,
            props.channel?.id,
            props.participantsVersion,
            props.participants,
            props.selectedParticipant?.id,
          ]);
          if (call) {
            const tree = Reflect.apply(original, this, [props]);
            if (!enabled) return tree;
            return ui.controls.tree(React, tree, (node) => {
              // Guild room state is shared with the app and can bypass the tile
              // view entirely when PiP opens from Discord's in-app overlay.
              if (
                node.props.appContext !== "POPOUT" ||
                node.props.channel?.id !== props.channel?.id ||
                !Array.isArray(node.props.participants) ||
                typeof node.props.guildRoomVisible !== "boolean"
              )
                return node;
              const getter =
                typeof node.type === "function" &&
                node.type.prototype &&
                Object.getOwnPropertyDescriptor(node.type.prototype, "viewProperties")?.get;
              // The portal can mount before WindowStore publishes its handle.
              // Its trusted WindowContext already identifies the live guest.
              const popoutWindow =
                !node.props.popoutWindow || node.props.popoutWindow.closed
                  ? context.renderWindow
                  : node.props.popoutWindow;
              const sig = getter && ui.signature(getter);
              if (sig && ui.has(sig, "canPopout", "canFullscreen", "canStayOnTop")) {
                let View = callViews.get(node.type);
                if (!View) {
                  View = class extends node.type {
                    get viewProperties() {
                      const native = Reflect.apply(getter, this, []);
                      return this.inPopout && this.popoutOpen
                        ? { ...native, canPopout: true }
                        : native;
                    }
                  };
                  callViews.set(node.type, View);
                }
                return React.createElement(View, {
                  ...node.props,
                  guildRoomVisible: false,
                  popoutWindow,
                  key: node.key,
                  ref: node.ref,
                });
              }
              return React.cloneElement(node, { guildRoomVisible: false, popoutWindow });
            });
          }
          if (menu) {
            const tree = Reflect.apply(original, this, [props]);
            if (!enabled || tree?.props?.id !== "no-video-hide") return tree;
            if (!saved) return null;
            return React.cloneElement(tree, {
              checked: local.show,
              action: () => {
                if (!saved) return;
                local.show = !local.show;
                for (const refresh of listeners) refresh();
              },
            });
          }
          const adjust = (props) => {
            const channel = props.channel?.id;
            if (
              !enabled ||
              stores.size !== 1 ||
              typeof channel !== "string" ||
              !Array.isArray(props.participants)
            )
              return props;
            const ids = new Set(media([...stores][0], channel).map((p) => p.id));
            const participants = props.participants.filter(
              (p) => !p.isPoppedOut && ((saved && local.show) || ids.has(p.id)),
            );
            const sole = participants.length === 1 ? participants[0] : null;
            const stream =
              sole && [...stores][0].getStreamParticipants(channel).find((p) => p.id === sole.id);
            const me = users.size === 1 ? [...users][0].getCurrentUser()?.id : undefined;
            const owner = stream?.stream?.ownerId || stream?.user?.id;
            const focusSole =
              !local.stopped &&
              sole &&
              (!stream || (typeof me === "string" && typeof owner === "string" && owner !== me));
            const callbacks = {};
            for (const name of ["onSelectParticipant", "onFullscreenParticipant"]) {
              if (typeof props[name] !== "function") continue;
              callbacks[name] = (...args) => {
                local.stopped = true;
                const result = props[name](...args);
                if (hideMembers) {
                  local.memberSelections.delete(channel);
                  setTimeout(() => {
                    try {
                      minimizeMembers(context.renderWindow, channel);
                    } catch {
                      /* Native selection remains intact. */
                    }
                  }, 0);
                }
                return result;
              };
            }
            return {
              ...props,
              ...callbacks,
              participants,
              filteredParticipants: participants,
              ...(focusSole
                ? { selectedParticipant: sole, ...(hideMembers ? { showParticipants: false } : {}) }
                : {}),
            };
          };
          if (nativeView) return React.createElement(original, adjust(props));
          let tree = Reflect.apply(original, this, [props]);
          if (!enabled || stores.size !== 1) return tree;
          // Discord's measured flex wrapper otherwise keeps the media's
          // minimum content height, overflowing the space below the call header.
          if (
            tree?.type === "div" &&
            tree.props?.style?.display === "flex" &&
            tree.props.style.height === "100%" &&
            tree.props.style.width === "100%"
          )
            tree = React.cloneElement(tree, {
              style: { ...tree.props.style, minWidth: 0, minHeight: 0 },
            });
          return ui.controls.tree(
            React,
            tree,
            (node) => {
              const inner = ui.unwrap(node.type),
                signature = inner && ui.signature(inner);
              if (
                signature &&
                ui.has(
                  signature,
                  "filteredParticipants",
                  "participantsVersion",
                  "totalNumberOfParticipants",
                  "getVoiceParticipantsHidden",
                )
              )
                return React.cloneElement(node, adjust(node.props));
              return node;
            },
            8,
          );
        };
        if (value.$$typeof === Symbol.for("react.memo") && typeof value.type === "function")
          value.type = Wrapper;
        else {
          const keys = Object.keys(exports).filter((name) => exports[name] === value);
          if (entries.length !== 1 || keys.length !== 1) continue;
          if (!ui.controls.replaceExport(runtime, exports, keys[0], Wrapper)) continue;
        }
        wrapped.add(value);
        wrapped.add(Wrapper);
      }
    },
  );
};
