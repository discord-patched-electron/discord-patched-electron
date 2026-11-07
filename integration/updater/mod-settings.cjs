// Client-mod operations are available only to the local settings window.
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { app } = require("electron");
const preferences = require("./profile.cjs");
let busy = false;
let restartRequired = false;
let onboarding;
let latestState;
let latestProgress;
const windows = new Set();

function needsOnboarding({ existingProfile = false } = {}) {
  const state = preferences.readObject(
    path.join(process.env.DPE_PROFILE_DIR, "dpe-updater/mods.json"),
  );
  if (state.onboardingPending === true) return true;
  if (state.setupComplete === true) return false;
  if (!existingProfile) return true;
  // Upgrades predate the Welcome marker. Inspect the active loader using the
  // same read-only detector as Mods, not a stale preference or backup archive.
  // Keep this before app readiness so the local window scheme can be registered.
  try {
    const channel = JSON.parse(
      fs.readFileSync(path.join(process.env.DPE_APP_ROOT, "resources/build_info.json"), "utf8"),
    ).releaseChannel;
    const result = spawnSync(
      "python3",
      [path.join(__dirname, "mod_manager.py"), "status", process.env.DPE_PROFILE_DIR, channel],
      {
        encoding: "utf8",
        timeout: 5000,
        maxBuffer: 64 * 1024,
        env: {
          ...process.env,
          LD_PRELOAD: "",
          PYTHONHOME: "",
          PYTHONPATH: "",
          PYTHONNOUSERSITE: "1",
          PYTHONDONTWRITEBYTECODE: "1",
        },
      },
    );
    if (result.error || result.status !== 0) return false;
    const status = JSON.parse(result.stdout).result;
    return !status?.error && status?.installed === "none";
  } catch {
    // An unreadable installation is not evidence that no mod is installed.
    return false;
  }
}

function remember(state) {
  if (state && !state.error && ["none", "vencord", "equicord"].includes(state.installed))
    latestState = state;
  return state;
}

function progress(state) {
  require("./diagnostic-log.cjs").record("mod-progress", state);
  if (!state || typeof state.label !== "string" || state.label.length > 500) return;
  latestProgress = {
    stage: typeof state.stage === "string" ? state.stage.slice(0, 40) : "install",
    label: state.label,
  };
  if (Number.isSafeInteger(state.current) && state.current >= 0)
    latestProgress.current = state.current;
  if (Number.isSafeInteger(state.total) && state.total > 0 && state.current <= state.total)
    latestProgress.total = state.total;
  for (const window of windows)
    if (!window.isDestroyed()) window.webContents.send("dpe-mod-progress", latestProgress);
}

function run(action, choice, progress = () => {}) {
  const root = process.env.DPE_APP_ROOT;
  const channel = JSON.parse(
    fs.readFileSync(path.join(root, "resources/build_info.json"), "utf8"),
  ).releaseChannel;
  return new Promise((resolve, reject) => {
    const args = [
      path.join(__dirname, "mod_manager.py"),
      action,
      process.env.DPE_PROFILE_DIR,
      channel,
    ];
    if (choice !== undefined) args.push(choice);
    require("./diagnostic-log.cjs").record("mod-start", { action, choice });
    const child = spawn("python3", args, {
      env: {
        ...process.env,
        LD_PRELOAD: "",
        PYTHONHOME: "",
        PYTHONPATH: "",
        PYTHONNOUSERSITE: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        DPE_UPDATE_PROGRESS: "1",
      },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    require("./diagnostic-log.cjs").capture(child.stderr, "mod-helper-stderr", { action });
    let pending = "";
    let result;
    const stop = () => {
      if (child.pid)
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          // Already exited.
        }
    };
    const timeout = setTimeout(stop, 180_000);
    app.once("will-quit", stop);
    const cleanup = () => {
      clearTimeout(timeout);
      app.removeListener("will-quit", stop);
    };
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (data) => {
      pending += data;
      if (pending.length > 64 * 1024) return stop();
      let end;
      while ((end = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        try {
          const message = JSON.parse(line);
          if (message.progress) progress(message.progress);
          else if (message.type === "progress") progress(message);
          if (message.result) result = message.result;
        } catch {
          stop();
        }
      }
    });
    child.once("error", () => {
      cleanup();
      reject(new Error("Could not start mod setup. Check that Python 3 is installed."));
    });
    child.once("close", (code) => {
      require("./diagnostic-log.cjs").record("mod-finish", { action, code, error: result?.error });
      cleanup();
      if (code !== 0 || !result)
        reject(new Error("Mod setup stopped before finishing. Reopen Mods and try again."));
      else resolve(result);
    });
  });
}

function attach(window) {
  windows.add(window);
  window.once("closed", () => windows.delete(window));
  window.on("close", (event) => {
    if (busy) event.preventDefault();
  });
  const handle = (name, callback) =>
    window.webContents.ipc.handle(name, async (event, ...args) => {
      if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame)
        throw new Error("Mods are available only in the local settings window.");
      try {
        return {
          ...remember(await callback(...args)),
          busy,
          restartRequired: onboarding ? false : restartRequired,
          ...(busy && latestProgress ? { progress: latestProgress } : {}),
        };
      } catch (error) {
        return {
          error: error.message,
          busy,
          restartRequired: onboarding ? false : restartRequired,
        };
      }
    });
  handle("dpe-mod-load", () => run("status"));
  handle("dpe-mod-complete", async () => {
    if (busy) throw new Error("Wait for mod setup to finish.");
    return run(onboarding ? "status" : "complete");
  });
  handle("dpe-mod-apply", async (choice) => {
    if (!["none", "vencord", "equicord"].includes(choice))
      throw new Error("Choose Vencord, Equicord, or no mod.");
    if (busy) throw new Error("Wait for mod setup to finish.");
    busy = true;
    progress({ stage: "prepare", label: "Preparing mod setup…" });
    try {
      const before = await run("status");
      if (before.error) return before;
      const result = await run("apply", choice, progress);
      if (!result.error && before.revision !== result.revision) restartRequired = true;
      return result;
    } finally {
      busy = false;
      latestProgress = undefined;
    }
  });
}

async function onboard({ completeLaunch = () => require("./relaunch.cjs").restart(app) } = {}) {
  if (onboarding) return onboarding.window;
  const session = {
    completeLaunch,
    finishing: false,
    cancelled: false,
    phase: "welcome",
    window: null,
  };
  onboarding = session;
  try {
    const state = remember(await run("begin"));
    if (state.error) throw new Error(state.error);
    const window = require("./config.cjs").open("setup");
    session.window = window;
    window.on("close", (event) => {
      if (onboarding !== session || session.finishing || session.phase !== "welcome") return;
      event.preventDefault();
      if (busy) return;
      // Keep a native window alive until saving and relaunch finish. Destroying
      // the last window first lets Electron quit and kill the completion worker.
      session.cancelConfigure?.();
      void completeOnboarding(session).catch((error) => {
        if (!window.isDestroyed()) window.webContents.send("dpe-setup-reminder", error.message);
      });
    });
    window.once("show", () => {
      // A fully rendered Welcome is healthy startup during a first-install trial.
      if (process.env.DPE_APPLICATION_READY)
        try {
          fs.writeFileSync(process.env.DPE_APPLICATION_READY, "ready\n", { mode: 0o600 });
        } catch (error) {
          console.error("Could not confirm Welcome startup:", error.message);
        }
    });
    window.once("closed", () => {
      if (onboarding === session && !session.finishing && session.phase === "welcome") {
        session.cancelled = true;
        session.cancelConfigure?.();
        onboarding = undefined;
        app.quit();
      }
    });
    return window;
  } catch (error) {
    onboarding = undefined;
    throw error;
  }
}

async function setupAction(action) {
  if (!onboarding || onboarding.cancelled) throw new Error("Welcome is not active.");
  if (busy || onboarding.finishing) throw new Error("Wait for mod setup to finish.");
  const session = onboarding;
  if (action === "configure") {
    if (session.phase === "settings") return { configuring: true };
    if (session.configurePromise) return session.configurePromise;
    const welcome = session.window;
    const window = require("./config.cjs").open("settings", undefined, {
      independent: true,
      onboarding: true,
    });
    session.configurePromise = new Promise((resolve, reject) => {
      const cleanup = () => {
        window.removeListener("show", shown);
        window.removeListener("closed", closed);
        window.webContents.removeListener("did-fail-load", failed);
        session.configurePromise = undefined;
        session.cancelConfigure = undefined;
      };
      const closed = () => {
        cleanup();
        reject(new Error("Settings closed before it was ready. Try Configure more again."));
      };
      const failed = (_event, _code, _description, _url, mainFrame) => {
        if (mainFrame === false) return;
        cleanup();
        if (!window.isDestroyed()) window.destroy();
        reject(new Error("Settings could not load. Try Configure more again."));
      };
      const shown = () => {
        cleanup();
        if (onboarding !== session || session.cancelled) {
          if (!window.isDestroyed()) window.destroy();
          resolve({ cancelled: true });
          return;
        }
        // Settings owns the rest of onboarding and was created independently
        // from the Welcome window that can now close.
        session.phase = "settings";
        session.window = window;
        window.on("close", (event) => {
          if (!isOnboardingSettings(window)) return;
          event.preventDefault();
          if (!busy && !session.finishing) void finishSettings(window).catch(() => {});
        });
        window.once("closed", () => {
          if (onboarding === session && !session.finishing) {
            session.cancelled = true;
            onboarding = undefined;
            app.quit();
          }
        });
        welcome.close();
        resolve({ configuring: true });
      };
      session.cancelConfigure = () => {
        cleanup();
        if (!window.isDestroyed()) window.destroy();
        resolve({ cancelled: true });
      };
      window.once("show", shown);
      window.once("closed", closed);
      window.webContents.on("did-fail-load", failed);
      if (window.isVisible()) shown();
    });
    return session.configurePromise;
  }
  if (action !== "done") throw new Error("Unknown Welcome action.");
  if (session.phase === "settings") return finishSettings(session.window);
  return completeOnboarding();
}

async function completeOnboarding(session = onboarding) {
  if (!session || onboarding !== session || session.cancelled)
    throw new Error("Welcome is not active.");
  if (busy || session.finishing) throw new Error("Wait for mod setup to finish.");
  if (session.phase === "welcome" && require("./config.cjs").settingsOpen())
    throw new Error("Close Settings to finish setup and restart Discord.");
  session.finishing = true;
  busy = true;
  progress({ stage: "complete", label: "Finishing setup…" });
  try {
    const result = remember(await run("complete"));
    if (result.error) throw new Error(result.error);
    progress({ stage: "restart", label: "Restarting Discord…" });
    busy = false;
    onboarding = undefined;
    await session.completeLaunch();
    if (session.phase === "settings" && !session.window.isDestroyed()) session.window.close();
    return { ...result, completed: true };
  } catch (error) {
    onboarding = session;
    session.finishing = false;
    busy = false;
    throw error;
  } finally {
    latestProgress = undefined;
  }
}

function isOnboardingSettings(window) {
  return onboarding?.phase === "settings" && onboarding.window === window && !onboarding.cancelled;
}

async function finishSettings(window) {
  if (!isOnboardingSettings(window))
    throw new Error("This Settings window is not finishing setup.");
  if (busy || onboarding.finishing) throw new Error("Wait for mod setup to finish.");
  if (!require("./launch-settings.cjs").confirmClose(window)) return { cancelled: true };
  window.webContents.send("dpe-setup-error", "");
  try {
    return await completeOnboarding(onboarding);
  } catch (error) {
    require("./launch-settings.cjs").resetCloseApproval(window);
    if (!window.isDestroyed()) window.webContents.send("dpe-setup-error", error.message);
    throw error;
  }
}
module.exports = {
  attach,
  needsOnboarding,
  onboard,
  setupAction,
  isOnboarding: () => Boolean(onboarding),
  activeWindow: () =>
    onboarding?.window && !onboarding.window.isDestroyed() ? onboarding.window : undefined,
  installedInfo: () => latestState,
  isOnboardingSettings,
  finishSettings,
};
