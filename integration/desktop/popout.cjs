// Discord's channel-call and individual camera/screenshare popouts. Keep debug,
// activity, soundboard and out-of-process overlay windows on their own paths.
function isVideo(key) {
  return (
    key === "DISCORD_CHANNEL_CALL_POPOUT" ||
    (typeof key === "string" && key.startsWith("DISCORD_CALL_TILE_POPOUT_"))
  );
}

const closing = new WeakSet();
const sessions = new WeakMap();
function reusable(window, key) {
  return !isVideo(key) || (!window.isDestroyed() && !closing.has(window));
}

// Opt-in manual-test tracing: no window keys, URLs, channel IDs or media data.
function trace(stage, key, state = {}) {
  const filename = process.env.DPE_POPOUT_TRACE_FILE;
  if (!filename || !isVideo(key)) return;
  try {
    require("node:fs").appendFileSync(
      filename,
      JSON.stringify({
        time: new Date().toISOString(),
        stage,
        kind: key === "DISCORD_CHANNEL_CALL_POPOUT" ? "call" : "tile",
        ...state,
      }) + "\n",
      { mode: 0o600 },
    );
  } catch {}
}

function traceRequest(details) {
  trace("requested", details.frameName);
}

function focusExisting(window, key) {
  if (!isVideo(key)) {
    window.setAlwaysOnTop(true);
    window.focus();
    window.setAlwaysOnTop(false);
    return;
  }
  trace("existing", key, { visible: window.isVisible(), minimized: window.isMinimized() });
  // focus() alone does not show a hidden window. Keep the user's pin state.
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

const positive = (value) => Number.isFinite(value) && value > 0;
const clamp = (value, min, max) => Math.round(Math.max(min, Math.min(value, max)));

function mediaSize(display, media = { width: 1280, height: 720 }, chrome = {}) {
  const area = display.workArea;
  const scaleFactor = positive(display.scaleFactor) ? display.scaleFactor : 1;
  const extraWidth = Math.max(0, Math.min(chrome.width || 0, area.width / 3));
  const extraHeight = Math.max(0, Math.min(chrome.height || 0, area.height / 3));
  const width = positive(media.width) ? media.width : 1280;
  const height = positive(media.height) ? media.height : 720;
  // Work areas are logical pixels; only source video pixels need conversion.
  // Bound the default to a useful fraction of the display, including chrome.
  const maxWidth = Math.max(1, Math.min(854, area.width * 0.4, area.width - 32) - extraWidth);
  const maxHeight = Math.max(1, Math.min(640, area.height * 0.45, area.height - 32) - extraHeight);
  const factor = Math.min(1 / scaleFactor, maxWidth / width, maxHeight / height);
  return {
    width: Math.max(1, Math.round(width * factor + extraWidth)),
    height: Math.max(1, Math.round(height * factor + extraHeight)),
  };
}

function windowOptions(options, key, parent, screen, original = options, preferences = {}) {
  if (!isVideo(key) || options.outOfProcessOverlay) return options;
  trace("allowed", key);
  if (!parent || parent.isDestroyed()) return options;
  const displays = screen.getAllDisplays();
  const savedPosition = Number.isFinite(options.x) && Number.isFinite(options.y);
  const savedDisplay = savedPosition
    ? displays.find(
        ({ workArea: area }) =>
          options.x >= area.x &&
          options.x < area.x + area.width &&
          options.y >= area.y &&
          options.y < area.y + area.height,
      )
    : undefined;
  const display = savedDisplay || screen.getDisplayMatching(parent.getBounds());
  const area = display.workArea;
  // Electron screen geometry is already in device-independent pixels. Applying
  // scaleFactor again makes HiDPI popouts oversized. Leave space for WM borders.
  const maxWidth = Math.max(1, area.width - 32);
  const maxHeight = Math.max(1, area.height - 32);
  const fallback = mediaSize({ ...display, scaleFactor: 1 });
  const requestedWidth = savedDisplay && positive(options.width) ? options.width : fallback.width;
  // Stored geometry may belong to a different stream or a manual resize. Start
  // at 16:9, then measure the video currently mounted in this popout.
  const requestedHeight = (requestedWidth * 9) / 16;
  const scale = Math.min(1, maxWidth / requestedWidth, maxHeight / requestedHeight);
  const minWidth = Math.min(maxWidth, requestedWidth, original.minWidth || 1);
  const minHeight = Math.min(maxHeight, requestedHeight, original.minHeight || 1);
  const width = Math.max(minWidth, Math.round(requestedWidth * scale));
  const height = Math.max(minHeight, Math.round(requestedHeight * scale));
  const floating = preferences.popoutFloating !== false;
  const autoPosition = preferences.popoutAutoPosition !== false;
  const gnome = Boolean(
    process.env.WAYLAND_DISPLAY &&
    /GNOME/i.test(process.env.XDG_CURRENT_DESKTOP || process.env.XDG_SESSION_DESKTOP || ""),
  );
  const position = require("./popout-position.cjs").corner(
    area,
    width,
    height,
    preferences.popoutCorner,
  );
  return {
    ...options,
    // X11 WMs honor the transient/dialog hint. Electron does not yet forward
    // parent to Wayland, so also advertise a fixed initial size until the first
    // focus (the compositor has mapped the surface by then). ready-to-show is
    // too early: lifting constraints there can make niri tile the window.
    ...(floating
      ? {
          parent,
          type: "dialog",
          modal: false,
          resizable: gnome ? true : false,
          dpeResizable: true,
          minWidth: gnome ? Math.min(minWidth, 160) : width,
          minHeight: gnome ? Math.min(minHeight, 90) : height,
          maxWidth: gnome ? 0 : width,
          maxHeight: gnome ? 0 : height,
          dpeMinimum: gnome ? undefined : [Math.min(minWidth, 160), Math.min(minHeight, 90)],
          dpeMaximum: [0, 0],
        }
      : { minWidth, minHeight }),
    dpeVideo: true,
    dpeReadySizing: gnome,
    dpeAdjustMinimum: !!process.env.HYPRLAND_INSTANCE_SIGNATURE,
    dpeFloating: floating,
    dpeAutoSize: true,
    dpeInitialPlace: autoPosition,
    alwaysOnTop: floating && preferences.popoutAlwaysOnTop !== false,
    ...(floating && preferences.popoutHideTitlebar !== false
      ? { frame: false, titleBarStyle: "default", titleBarOverlay: false }
      : {}),
    dpeAutoPosition: autoPosition,
    dpeCorner: preferences.popoutCorner || "bottom-right",
    width,
    height,
    x:
      autoPosition || savedDisplay
        ? clamp(autoPosition ? position.x : options.x, area.x, area.x + area.width - width)
        : undefined,
    y:
      autoPosition || savedDisplay
        ? clamp(autoPosition ? position.y : options.y, area.y, area.y + area.height - height)
        : undefined,
    // The parent owns the React tree and media stream. Keep rendering the child
    // when another application is focused, just as Discord does for its overlay.
    webPreferences: { ...options.webPreferences, backgroundThrottling: false },
  };
}

function attach(window, options, onReady, screen) {
  if (!isVideo(window.windowKey) || options.outOfProcessOverlay || !options.dpeVideo) return false;
  trace("created", window.windowKey);
  const cancelledClose = () => {
    if (window.isDestroyed()) return;
    closing.delete(window);
    const restore = window.webContents?.executeJavaScript;
    if (restore)
      void window.webContents
        .executeJavaScript(
          `if(window.name === "") window.name = ${JSON.stringify(window.windowKey)};void 0;`,
        )
        .catch(() => {});
  };
  window.on("close", (event) => {
    closing.add(window);
    queueMicrotask(() => {
      if (event.defaultPrevented) cancelledClose();
    });
  });
  window.webContents?.on("will-prevent-unload", (event) => {
    // Electron closes anyway if a handler prevents will-prevent-unload.
    queueMicrotask(() => {
      if (!event.defaultPrevented) cancelledClose();
    });
  });
  window.once("ready-to-show", () => trace("ready", window.windowKey));
  window.once("closed", () => trace("closed", window.windowKey));
  window.webContents?.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
    if (mainFrame) trace("load-failed", window.windowKey, { code });
  });
  let mapped = false,
    started = false;
  let touched = false;
  let compositor;
  let placement = Promise.resolve();
  let baseline;
  let baselineClient;
  let lastNormal;
  let fullscreenNormal;
  let normalMinimum = options.dpeMinimum || [options.minWidth || 1, options.minHeight || 1];
  let targetCorner = options.dpeCorner || "bottom-right";
  const snapshot = async () => {
    if (window.isDestroyed() || window.isFullScreen() || fullscreenNormal) return;
    const value = compositor
      ? await compositor.snapshot?.(screen)
      : !process.env.WAYLAND_DISPLAY
        ? {
            bounds: window.getBounds(),
            area: screen.getDisplayMatching(window.getBounds()).workArea,
          }
        : undefined;
    if (value && !window.isFullScreen()) lastNormal = { ...value, client: window.getBounds() };
    else if (
      !compositor &&
      !window.isDestroyed() &&
      !window.isFullScreen() &&
      !window.isMaximized()
    ) {
      const bounds = window.getBounds();
      lastNormal = {
        bounds,
        client: bounds,
        area: screen.getDisplayMatching(bounds).workArea,
        unknownPosition: true,
      };
    }
    return window.isFullScreen() ? undefined : value;
  };
  const cornerState = async () => {
    const value = await snapshot();
    if (!value)
      return {
        available: false,
        size: window.isDestroyed()
          ? undefined
          : { width: window.getSize()[0], height: window.getSize()[1] },
      };
    const { bounds, area } = value;
    const sameSize =
      baseline &&
      Math.abs(bounds.width - baseline.width) <= 2 &&
      Math.abs(bounds.height - baseline.height) <= 2;
    const corners = ["top-left", "top-right", "bottom-left", "bottom-right"];
    const selected = sameSize
      ? corners.find((name) => {
          const point = require("./popout-position.cjs").corner(
            area,
            bounds.width,
            bounds.height,
            name,
          );
          return Math.abs(bounds.x - point.x) <= 2 && Math.abs(bounds.y - point.y) <= 2;
        })
      : undefined;
    return {
      available: true,
      corner: selected || null,
      large: bounds.width * bounds.height >= area.width * area.height * 0.7,
      size: { width: window.getSize()[0], height: window.getSize()[1] },
    };
  };
  let choices = Promise.resolve();
  const moveChoice = async (name, automatic = false) => {
    if (!["top-left", "top-right", "bottom-left", "bottom-right"].includes(name)) return;
    await placement;
    if (!automatic)
      await compositor?.prepare?.(name, () => !window.isDestroyed() && !window.isFullScreen());
    const before = await snapshot();
    if (!before || (automatic && (await cornerState()).corner !== targetCorner)) return;
    if (!automatic && baselineClient && baseline) {
      const current = window.getSize();
      if (
        Math.abs(current[0] - baselineClient.width) > 2 ||
        Math.abs(current[1] - baselineClient.height) > 2
      ) {
        const factor = Math.min(
          1,
          (before.area.width - 32 - Math.max(0, before.bounds.width - current[0])) /
            baselineClient.width,
          (before.area.height - 32 - Math.max(0, before.bounds.height - current[1])) /
            baselineClient.height,
        );
        const size = {
          width: Math.max(1, Math.floor(baselineClient.width * factor)),
          height: Math.max(1, Math.floor(baselineClient.height * factor)),
        };
        window.setSize(size.width, size.height);
        await compositor?.resizeTo?.(size, screen, before);
        for (let attempt = 0; attempt < 10; attempt++) {
          const value = await snapshot();
          if (
            value &&
            Math.abs(value.bounds.width - (size.width + before.bounds.width - current[0])) <= 2 &&
            Math.abs(value.bounds.height - (size.height + before.bounds.height - current[1])) <= 2
          )
            break;
          await new Promise((resolve) => setTimeout(resolve, 40));
        }
      }
    }
    const fresh = await snapshot();
    if (!fresh) return;
    if (compositor) await compositor.moveTo(name, screen, fresh);
    else
      window.setPosition(
        ...Object.values(
          require("./popout-position.cjs").corner(
            fresh.area,
            fresh.bounds.width,
            fresh.bounds.height,
            name,
          ),
        ),
      );
    if (!automatic) {
      const value = await snapshot();
      if (value) {
        baseline = value.bounds;
        baselineClient = { width: window.getSize()[0], height: window.getSize()[1] };
      }
    }
    targetCorner = name;
    options.dpeCorner = name;
    return cornerState();
  };
  const choose = (name, automatic = false) =>
    (choices = choices.catch(() => {}).then(() => moveChoice(name, automatic)));
  sessions.set(window, {
    state: cornerState,
    choose,
    setMinimum: (width, height) => {
      normalMinimum = [
        Math.max(1, Math.min(normalMinimum[0], 160, width)),
        Math.max(1, Math.min(normalMinimum[1], 90, height)),
      ];
      if (options.dpeMinimum) options.dpeMinimum = normalMinimum;
      if (mapped) {
        window.setMinimumSize(...normalMinimum);
        onReady({ ...options, minWidth: normalMinimum[0], minHeight: normalMinimum[1] });
      }
    },
    apply: async (values, previous) => {
      options.dpeAutoPosition = values.popoutAutoPosition !== false;
      if (values.popoutAlwaysOnTop !== previous.popoutAlwaysOnTop) {
        const enabled = options.dpeFloating && values.popoutAlwaysOnTop !== false;
        options.alwaysOnTop = enabled;
        window.setAlwaysOnTop?.(enabled);
        await compositor?.topmost?.(enabled);
      }
      if (values.popoutAutoPosition && values.popoutCorner !== previous.popoutCorner)
        await choose(values.popoutCorner, true);
    },
  });
  const enterFullscreen = () => {
    fullscreenNormal ||= lastNormal;
    trace("fullscreen-enter", window.windowKey, { normal: lastNormal?.bounds });
    touched = true;
  };
  const leaveFullscreen = () => {
    if (window.isDestroyed()) return;
    window.setResizable(true);
    window.setMaximumSize(0, 0);
    window.setMinimumSize(...normalMinimum);
    onReady({ ...options, minWidth: normalMinimum[0], minHeight: normalMinimum[1] });
    const saved = fullscreenNormal;
    trace("fullscreen-leave", window.windowKey, { normal: saved?.bounds });
    fullscreenNormal = undefined;
    if (!saved) return;
    if (saved.unknownPosition) {
      window.setSize(saved.client.width, saved.client.height);
      return;
    }
    void (async () => {
      let resumed = false;
      // Wait for the compositor's normal-state acknowledgement. Fullscreen
      // frames must never replace the normal geometry or automatic-size baseline.
      for (let attempt = 0; attempt < 20; attempt++) {
        if (window.isDestroyed() || fullscreenNormal) return;
        if (window.isFullScreen()) {
          await new Promise((resolve) => setTimeout(resolve, 40));
          continue;
        }
        if (!resumed) {
          await compositor?.resumeNormal?.();
          resumed = true;
        }
        const current = await snapshot();
        if (current) {
          window.setBounds({
            x: saved.bounds.x,
            y: saved.bounds.y,
            width: saved.client?.width || saved.bounds.width,
            height: saved.client?.height || saved.bounds.height,
          });
          await compositor?.restore?.(screen, saved);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 40));
      }
    })().catch(() => {});
  };
  window.on("enter-full-screen", enterFullscreen);
  window.on("enter-html-full-screen", enterFullscreen);
  window.on("leave-full-screen", leaveFullscreen);
  window.on("leave-html-full-screen", leaveFullscreen);
  window.once("closed", () => sessions.delete(window));
  let initialBounds;
  let initializing = false;
  let focused = !!window.isFocused?.();
  let normalTimer;
  const rememberNormal = () => {
    clearTimeout(normalTimer);
    if (!window.isDestroyed() && !window.isFullScreen())
      normalTimer = setTimeout(() => {
        void snapshot().catch(() => {});
      }, 40);
  };
  window.on("resize", rememberNormal);
  window.on("move", rememberNormal);
  window.once("closed", () => clearTimeout(normalTimer));
  const gainedFocus = () => {
    focused = true;
  };
  window.on("focus", gainedFocus);
  const cancel = () => {
    if (window.isDestroyed() || initializing) return;
    const bounds = window.getBounds();
    // X11 can send configure/move notifications without changing geometry
    // while mapping and restoring resize constraints.
    if (
      initialBounds &&
      (compositor || options.dpeReadySizing
        ? ["width", "height"]
        : ["x", "y", "width", "height"]
      ).some((key) => bounds[key] !== initialBounds[key])
    ) {
      touched = true;
      trace("geometry-changed", window.windowKey, { width: bounds.width, height: bounds.height });
    }
  };
  const stop = () => {
    trace("interaction-stopped", window.windowKey);
    touched = true;
  };
  const blurred = () => {
    if (focused) stop();
  };
  const start = async () => {
    const web = window.webContents;
    if (!mapped || started || !options.dpeAutoSize || !screen || !web.executeJavaScript) return;
    const address = web.getURL();
    if (!URL.canParse(address) || new URL(address).pathname !== "/popout") return;
    const url = new URL(address);
    if (
      url.protocol !== "https:" ||
      !["discord.com", "canary.discord.com", "ptb.discord.com"].includes(url.hostname)
    )
      return;
    started = true;
    try {
      const measurement = web.executeJavaScript(`(${require("./popout-media.cjs").toString()})()`);
      const [media] = await Promise.all([measurement, placement]);
      options.dpeCorner = targetCorner;
      trace("measured", window.windowKey, {
        media: !!media,
        touched,
        maximized: window.isMaximized(),
        fullscreen: window.isFullScreen(),
      });
      if (
        !media ||
        window.isDestroyed() ||
        touched ||
        (!compositor && window.isMaximized()) ||
        window.isFullScreen()
      )
        return;
      const bounds = window.getBounds();
      const display = (await compositor?.display(screen)) || screen.getDisplayMatching(bounds);
      // Hyprland advertises every Wayland surface as maximized to suppress
      // client shadows. The verified compositor state decides whether this
      // owned window is actually floating and normal.
      const unchanged = !compositor || (await compositor.unchanged());
      trace("sizing-check", window.windowKey, { unchanged });
      if (
        window.isDestroyed() ||
        touched ||
        !unchanged ||
        (!compositor && window.isMaximized()) ||
        window.isFullScreen()
      )
        return;
      window.removeListener("resize", cancel);
      window.removeListener("move", cancel);
      const content = window.getContentBounds();
      const zoom = web.getZoomFactor();
      const size = mediaSize(display, media, {
        width: bounds.width - content.width + media.chromeWidth * zoom,
        height: bounds.height - content.height + media.chromeHeight * zoom,
      });
      const minimum = options.dpeMinimum || [options.minWidth, options.minHeight];
      // Narrow portrait or ultrawide media may need a smaller axis minimum.
      const restored = {
        ...options,
        // Hyprland can include Chromium's surface inset in fixed-size hints.
        // Measure that initial excess rather than assuming a shadow width.
        minWidth: Math.max(
          1,
          Math.min(minimum[0], size.width) -
            (options.dpeAdjustMinimum ? Math.max(0, initialBounds.width - options.width) : 0),
        ),
        minHeight: Math.max(
          1,
          Math.min(minimum[1], size.height) -
            (options.dpeAdjustMinimum ? Math.max(0, initialBounds.height - options.height) : 0),
        ),
      };
      normalMinimum = [restored.minWidth, restored.minHeight];
      window.setMinimumSize(restored.minWidth, restored.minHeight);
      onReady(restored);
      const area = display.workArea;
      await compositor?.resize?.(size, () => !touched);
      if (window.isDestroyed() || touched) return;
      if (options.dpeAutoPosition !== false) {
        window.setBounds({
          ...size,
          ...require("./popout-position.cjs").corner(
            area,
            size.width,
            size.height,
            options.dpeCorner,
          ),
        });
        await compositor?.place(area, size, () => !touched);
      } else window.setSize(size.width, size.height);
      baseline = (await snapshot())?.bounds;
      if (baseline) baselineClient = { width: window.getSize()[0], height: window.getSize()[1] };
      trace("sized", window.windowKey, { width: size.width, height: size.height, media: !!media });
    } catch (error) {
      trace("sizing-failed", window.windowKey, { error: error.name });
      /* Unsupported/closing renderers retain their bounded default. */
    } finally {
      window.removeListener("resize", cancel);
      window.removeListener("move", cancel);
      window.removeListener("blur", blurred);
      window.removeListener("focus", gainedFocus);
      window.removeListener("closed", stop);
    }
  };
  window.webContents?.on("did-finish-load", () => void start());
  const mappedWindow = () => {
    if (mapped || window.isDestroyed()) return;
    window.removeListener("focus", mappedWindow);
    window.removeListener("ready-to-show", mappedWindow);
    trace("mapped", window.windowKey);
    if (options.dpeMinimum) {
      window.setResizable(options.dpeResizable !== false);
      window.setMinimumSize(...options.dpeMinimum);
      window.setMaximumSize(...options.dpeMaximum);
      onReady({ ...options, minWidth: options.dpeMinimum[0], minHeight: options.dpeMinimum[1] });
    }
    mapped = true;
    if (options.dpeAutoSize || options.dpeInitialPlace || options.dpeFloating) {
      compositor = options.dpeFloating
        ? require("./popout-position.cjs").identify(window, undefined, undefined, options.dpeCorner)
        : undefined;
      // Anchor the bounded default immediately; video discovery can take ten
      // seconds or be unavailable. Wayland still controls the initial map.
      if (compositor) {
        // Restore constraints and complete the initial compositor transaction
        // before treating native configure events as user resizes.
        initializing = true;
        placement = (async () => {
          if (options.dpeAutoPosition !== false)
            await compositor.prepare?.(targetCorner, () => !touched);
          const display = await compositor.display(screen);
          if (window.isDestroyed() || touched || !display) return;
          const fit = await compositor.fit(display.workArea);
          if (window.isDestroyed() || touched) return;
          if (fit) {
            // Wayland may have mapped on a smaller output than Electron's
            // initial parent-coordinate guess. Bound the default even when
            // there will never be media metadata to trigger another resize.
            const minimum = options.dpeMinimum || [options.minWidth || 1, options.minHeight || 1];
            const restored = {
              ...options,
              minWidth: Math.min(minimum[0], fit.width),
              minHeight: Math.min(minimum[1], fit.height),
            };
            normalMinimum = [restored.minWidth, restored.minHeight];
            window.setMinimumSize(restored.minWidth, restored.minHeight);
            onReady(restored);
            initialBounds = { ...window.getBounds(), ...fit };
            window.setSize(fit.width, fit.height);
            await compositor.resize?.(fit, () => !touched);
          }
          if (options.dpeAutoPosition !== false)
            await compositor.place(display.workArea, fit, () => !touched);
          else await compositor.checkpoint(fit);
        })().finally(async () => {
          if (!window.isDestroyed()) initialBounds = window.getBounds();
          initializing = false;
          baseline = (await snapshot())?.bounds;
          if (baseline)
            baselineClient = { width: window.getSize()[0], height: window.getSize()[1] };
          await compositor?.topmost?.(options.alwaysOnTop === true);
        });
      }
      initialBounds = window.getBounds();
      if (!compositor) {
        baseline = initialBounds;
        baselineClient = { width: initialBounds.width, height: initialBounds.height };
      }
      window.on("resize", cancel);
      window.on("move", cancel);
      window.on("blur", blurred);
      window.once("closed", stop);
      void start();
    }
  };
  window.once("focus", mappedWindow);
  if (options.dpeReadySizing) window.once("ready-to-show", mappedWindow);
  if (!options.dpeMinimum) onReady(options);
  return true;
}

module.exports = {
  session: (window) => sessions.get(window),
  reusable,
  isVideo,
  windowOptions,
  attach,
  focusExisting,
  traceRequest,
  mediaSize,
};
