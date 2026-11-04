// Discord's channel-call and individual camera/screenshare popouts. Keep debug,
// activity, soundboard and out-of-process overlay windows on their own paths.
function isVideo(key) {
  return (
    key === "DISCORD_CHANNEL_CALL_POPOUT" ||
    (typeof key === "string" && key.startsWith("DISCORD_CALL_TILE_POPOUT_"))
  );
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

function mediaSize(display, media = { width: 854, height: 480 }, chrome = {}) {
  const area = display.workArea;
  const scaleFactor = positive(display.scaleFactor) ? display.scaleFactor : 1;
  const extraWidth = Math.max(0, Math.min(chrome.width || 0, area.width / 3));
  const extraHeight = Math.max(0, Math.min(chrome.height || 0, area.height / 3));
  const width = positive(media.width) ? media.width : 854;
  const height = positive(media.height) ? media.height : 480;
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
  const requestedHeight =
    savedDisplay && positive(options.height) ? options.height : fallback.height;
  const scale = Math.min(1, maxWidth / requestedWidth, maxHeight / requestedHeight);
  const minWidth = Math.min(maxWidth, requestedWidth, original.minWidth || 1);
  const minHeight = Math.min(maxHeight, requestedHeight, original.minHeight || 1);
  const width = Math.max(minWidth, Math.round(requestedWidth * scale));
  const height = Math.max(minHeight, Math.round(requestedHeight * scale));
  const floating = preferences.popoutFloating !== false;
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
          resizable: false,
          dpeResizable: options.resizable !== false,
          minWidth: width,
          minHeight: height,
          maxWidth: width,
          maxHeight: height,
          dpeMinimum: [minWidth, minHeight],
          dpeMaximum: [options.maxWidth || 0, options.maxHeight || 0],
        }
      : { minWidth, minHeight }),
    dpeVideo: true,
    dpeFloating: floating,
    dpeAutoSize: !savedDisplay,
    width,
    height,
    x: clamp(
      savedDisplay ? options.x : area.x + area.width - width - 16,
      area.x,
      area.x + area.width - width,
    ),
    y: clamp(
      savedDisplay ? options.y : area.y + area.height - height - 16,
      area.y,
      area.y + area.height - height,
    ),
    // The parent owns the React tree and media stream. Keep rendering the child
    // when another application is focused, just as Discord does for its overlay.
    webPreferences: { ...options.webPreferences, backgroundThrottling: false },
  };
}

function attach(window, options, onReady, screen) {
  if (!isVideo(window.windowKey) || options.outOfProcessOverlay || !options.dpeVideo) return false;
  trace("created", window.windowKey);
  window.once("ready-to-show", () => trace("ready", window.windowKey));
  window.once("closed", () => trace("closed", window.windowKey));
  window.webContents?.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
    if (mainFrame) trace("load-failed", window.windowKey, { code });
  });
  let mapped = false,
    started = false;
  let touched = false;
  let compositor;
  const cancel = () => {
    touched = true;
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
      const media = await web.executeJavaScript(`(${require("./popout-media.cjs").toString()})()`);
      if (window.isDestroyed() || touched || window.isMaximized() || window.isFullScreen()) return;
      const bounds = window.getBounds();
      const display = (await compositor?.display(screen)) || screen.getDisplayMatching(bounds);
      if (window.isDestroyed() || touched) return;
      window.removeListener("resize", cancel);
      window.removeListener("move", cancel);
      const content = window.getContentBounds();
      const zoom = web.getZoomFactor();
      const size = media
        ? mediaSize(display, media, {
            width: bounds.width - content.width + media.chromeWidth * zoom,
            height: bounds.height - content.height + media.chromeHeight * zoom,
          })
        : { width: bounds.width, height: bounds.height };
      const minimum = options.dpeMinimum || [options.minWidth, options.minHeight];
      // Narrow portrait or ultrawide media may need a smaller axis minimum.
      const restored = {
        ...options,
        minWidth: Math.min(minimum[0], size.width),
        minHeight: Math.min(minimum[1], size.height),
      };
      window.setMinimumSize(restored.minWidth, restored.minHeight);
      onReady(restored);
      const area = display.workArea;
      window.setBounds({
        ...size,
        x: area.x + area.width - size.width - 16,
        y: area.y + area.height - size.height - 16,
      });
      await compositor?.place(area);
      trace("sized", window.windowKey, { width: size.width, height: size.height, media: !!media });
    } catch {
      /* Unsupported/closing renderers retain their bounded default. */
    } finally {
      window.removeListener("resize", cancel);
      window.removeListener("move", cancel);
    }
  };
  window.webContents?.on("did-finish-load", () => void start());
  window.once("focus", () => {
    trace("focused", window.windowKey);
    if (options.dpeMinimum) {
      window.setResizable(options.dpeResizable !== false);
      window.setMinimumSize(...options.dpeMinimum);
      window.setMaximumSize(...options.dpeMaximum);
      onReady({ ...options, minWidth: options.dpeMinimum[0], minHeight: options.dpeMinimum[1] });
    }
    mapped = true;
    if (options.dpeAutoSize) {
      compositor = options.dpeFloating
        ? require("./popout-position.cjs").identify(window)
        : undefined;
      window.on("resize", cancel);
      window.on("move", cancel);
      void start();
    }
  });
  if (!options.dpeMinimum) onReady(options);
  return true;
}

module.exports = { isVideo, windowOptions, attach, focusExisting, traceRequest, mediaSize };
