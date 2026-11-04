"use strict";
// The main process owns helpers and source identities. Only the separate DPE
// picker selects a source; renderer diagnostics never contain titles.
const fs = require("node:fs");
const path = require("node:path");
const { spawn, execFile } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { EventEmitter } = require("node:events");
const KMS_REASONS = [
  "helper-missing",
  "permission-required",
  "permission-blocked",
  "authorization-unavailable",
  "no-outputs",
  "unsupported",
];
const monitorMethod = (method) => ["wayland-direct", "kms"].includes(method);
function kmsError(value) {
  const reason = value?.reason || value?.errorCode;
  const error = new Error(
    {
      "helper-missing": "The bundled direct screen capture helper is unavailable.",
      "permission-required": "Allow direct screen capture in DPE Settings first.",
      "permission-blocked":
        "This launch environment blocks KMS permissions. Use the System picker or restart Discord outside the restricted environment.",
      "authorization-cancelled":
        "KMS authorization was cancelled. You can authorize and try again.",
      "authorization-unavailable":
        "KMS authorization is unavailable. Check that polkit and a desktop authentication agent are running, or use the System picker.",
      "no-outputs": "No supported monitors are available for KMS capture.",
      unsupported: "KMS capture is unavailable on this system. Choose another capture method.",
    }[reason] ||
      "KMS capture stopped before it was ready. Choose another monitor or capture method.",
  );
  error.code = [...KMS_REASONS, "authorization-cancelled"].includes(reason) ? reason : null;
  return error;
}

function worker(file, args, env = process.env) {
  const launch =
    path.basename(file) === "dpe-kms-source" ? require("./capture-host.cjs").spawn : spawn;
  const child = launch(file, args, { env, stdio: ["pipe", "pipe", "ignore"], detached: true });
  const events = new EventEmitter();
  let buffer = "",
    stopped = false,
    failure;
  const fail = () => {
    failure ||= new Error("Capture helper stopped. Choose the source again.");
    stop();
    events.emit("failure", failure);
  };
  child.on("error", fail);
  // Drain the helper's final structured error before reporting process failure.
  // exit may precede stdout delivery; close follows closure of its stdio pipes.
  child.on("close", fail);
  child.stdin.on("error", () => {});
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (data) => {
    buffer += data;
    // The trusted broker may return one GPU-downscaled BGRA thumbnail.
    if (buffer.length > 1024 * 1024) {
      fail();
      return;
    }
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      try {
        events.emit("message", JSON.parse(line));
      } catch {
        fail();
        return;
      }
    }
  });
  function stop() {
    if (stopped) return;
    stopped = true;
    child.stdin.end();
    child.stopHostCapture?.();
    const kill = (signal) => {
      // A dead group leader does not imply its producer children have exited.
      if (!child.pid) return;
      try {
        process.kill(-child.pid, signal);
      } catch {
        /* Already exited. */
      }
    };
    kill("SIGTERM");
    const timer = setTimeout(() => kill("SIGKILL"), 2000);
    timer.unref();
  }
  function wait(predicate, timeout = 10000, signal) {
    if (failure || stopped || signal?.aborted)
      return Promise.reject(failure || new Error("Capture request ended."));
    return new Promise((resolve, reject) => {
      const finish = (error, value) => {
        clearTimeout(timer);
        events.off("message", receive);
        events.off("failure", failed);
        signal?.removeEventListener("abort", aborted);
        if (error) reject(error);
        else resolve(value);
      };
      const receive = (value) => {
        if (predicate(value)) finish(null, value);
      };
      const failed = (error) => finish(error);
      const aborted = () => finish(new Error("Capture request ended."));
      const timer = setTimeout(
        () => finish(new Error("Capture helper did not become ready.")),
        timeout,
      );
      events.on("message", receive);
      events.on("failure", failed);
      signal?.addEventListener("abort", aborted, { once: true });
    });
  }
  return {
    stop,
    wait,
    events,
    send(value) {
      if (!stopped && !failure) child.stdin.write(JSON.stringify(value) + "\n");
    },
    async command(value, { signal } = {}) {
      const id = randomUUID();
      const reply = wait((message) => message?.id === id, 10000, signal);
      this.send({ ...value, id });
      const result = await reply;
      if (!result.ok) throw new Error("The selected game is unavailable. Refresh the source list.");
      return result;
    },
  };
}

function createController({
  root = process.env.DPE_APP_ROOT,
  env = process.env,
  platform = process.platform,
  openSettings,
  run = worker,
  list = (file) =>
    new Promise((resolve, reject) => {
      execFile(file, ["--list"], { timeout: 3000, maxBuffer: 65536, env }, (error, output) => {
        if (error) return reject(error);
        try {
          resolve(JSON.parse(output));
        } catch (failure) {
          reject(failure);
        }
      });
    }),
  exists = fs.existsSync,
  experimental = () => require("../video/settings.cjs").read().experimentalCapture,
  permission = () => require("./capture-permission.cjs").status(),
  thumbnail = require("./capture-preview.cjs").screen,
  encodeThumbnail = require("./capture-preview.cjs").jpeg,
  log = (message) => console.warn(message),
} = {}) {
  const direct = path.join(root || "", "capture/dpe-wayland-source");
  const kms = path.join(root || "", "capture/dpe-kms-source");
  const bundle = path.join(root || "", "capture/vkcapture");
  const socketPath = path.isAbsolute(env.PIPEWIRE_REMOTE || "")
    ? env.PIPEWIRE_REMOTE
    : path.join(env.XDG_RUNTIME_DIR || "/nonexistent", env.PIPEWIRE_REMOTE || "pipewire-0");
  const gameSocket = path.join(env.XDG_RUNTIME_DIR || "/nonexistent", "dpe-gamecapture/hook.sock");
  const sessions = new Map();
  const catalogs = new WeakMap();
  const armed = new WeakMap();
  const browsers = new Set();
  const browserCleanup = new WeakMap();
  const browserVersions = new WeakMap();
  async function closePicker(owner, handoff = false) {
    browsers.delete(owner);
    catalogs.delete(owner);
    browserVersions.set(owner, (browserVersions.get(owner) || 0) + 1);
    browserCleanup.get(owner)?.();
    browserCleanup.delete(owner);
    // A committed native start closes the React picker before the async
    // native request consumes its one-shot selection. Ordinary close revokes it.
    if (!handoff) armed.delete(owner);
    await stopPreview(owner);
    if (!handoff && !browsers.size && broker)
      await broker.command({ op: "suspend" }).catch(() => {});
    return {};
  }
  const previewJobs = new Map();
  const previewFailures = new Map();
  async function stopJob(token, job) {
    if (previewJobs.get(token) !== job) return;
    previewJobs.delete(token);
    clearTimeout(job.idle);
    job.abort.abort();
    if (job.game && broker)
      await broker.command({ op: "cancel-preview", token: job.source }).catch(() => {});
  }
  async function stopPreview(owner) {
    await Promise.all(
      [...previewJobs]
        .filter(([, job]) => !owner || job.owner === owner)
        .map(([token, job]) => stopJob(token, job)),
    );
    return {};
  }
  async function preview(owner, value) {
    const inventory = catalogs.get(owner),
      source = inventory?.entries.get(value?.token);
    if (
      closed ||
      !experimental() ||
      !browsers.has(owner) ||
      owner.isDestroyed?.() ||
      !source?.available ||
      inventory.expires < Date.now() ||
      !Number.isInteger(value.width) ||
      !Number.isInteger(value.height) ||
      value.width < 16 ||
      value.height < 16 ||
      value.width > 480 ||
      value.height > 270 ||
      armed.has(owner) ||
      sessions.size ||
      pending
    )
      return {};
    if ((previewFailures.get(source.identity) || 0) > Date.now()) return {};
    for (const [identity, until] of previewFailures)
      if (until <= Date.now()) previewFailures.delete(identity);
    let job = previewJobs.get(value.token);
    if (job && (job.owner !== owner || job.width !== value.width || job.height !== value.height)) {
      await stopJob(value.token, job);
      job = undefined;
    }
    if (!job) {
      if (
        previewJobs.size >= 4 ||
        [...previewJobs.values()].some(
          (item) => item.source === source.source && item.method === source.method,
        )
      )
        return {};
      job = {
        owner,
        abort: new AbortController(),
        game: source.method === "vkcapture",
        source: source.source,
        method: source.method,
        width: value.width,
        height: value.height,
        last: 0,
        flight: false,
      };
      previewJobs.set(value.token, job);
    }
    if (job.flight || Date.now() - job.last < 1000 / 30) return {};
    job.flight = true;
    job.last = Date.now();
    clearTimeout(job.idle);
    try {
      // Permission status may involve pkcheck; bound that overhead independently
      // of frame rate. Revocation stops a session within this short lease.
      if (
        monitorMethod(source.method) &&
        (!job.permissionUntil || Date.now() > job.permissionUntil)
      ) {
        if (!(await permission()).allowed) {
          await stopJob(value.token, job);
          return {};
        }
        job.permissionUntil = Date.now() + 1000;
      }
      if (job.abort.signal.aborted) return {};
      const frame = job.game
        ? await gameBroker().command(
            { op: "preview", token: source.source, width: value.width, height: value.height },
            { signal: job.abort.signal },
          )
        : await thumbnail(
            path.join(root || "", "capture/dpe-screen-preview"),
            [
              "--method",
              source.method,
              "--output",
              source.source,
              "--width",
              String(value.width),
              "--height",
              String(value.height),
            ],
            env,
            job.abort.signal,
          );
      if (job.abort.signal.aborted || !experimental() || owner.isDestroyed?.()) return {};
      const result = encodeThumbnail(frame);
      if (!result?.url) {
        previewFailures.set(source.identity, Date.now() + 2000);
        await stopJob(value.token, job);
      }
      return result;
    } catch {
      previewFailures.set(source.identity, Date.now() + 2000);
      await stopJob(value.token, job);
      return {};
    } finally {
      job.flight = false;
      if (previewJobs.get(value.token) === job) {
        job.idle = setTimeout(() => {
          void stopJob(value.token, job);
        }, 750);
        job.idle.unref?.();
      }
    }
  }
  let pending,
    broker,
    gameOwner,
    closed = false,
    requestGeneration = 0;
  const methodReasons = {};
  let gameWarnings = new Set();
  function noteHiddenGames(reasons) {
    const messages = {
      occupied: "Stock vkcapture sources hidden: another client owns the capture socket.",
      unavailable: "Unavailable vkcapture sources hidden.",
      failed: "Vkcapture discovery failed; game sources are hidden.",
    };
    const next = new Set(reasons);
    for (const reason of next)
      if (!gameWarnings.has(reason)) log(`[DPE capture] ${messages[reason]}`);
    // Log transitions, not every periodic picker refresh. Never log source
    // names, process IDs, socket paths or thumbnail contents.
    gameWarnings = next;
  }
  // Wayland specifies positions, not a canonical monitor number. Use desktop
  // layout order only when every connector can be identified unambiguously.
  function orderedMonitors(outputs, layout) {
    const monitors = outputs.slice(0, 32);
    const positions = new Map();
    const duplicates = new Set();
    for (const output of layout || []) {
      if (positions.has(output.name)) duplicates.add(output.name);
      positions.set(output.name, output.position);
    }
    const names = new Set();
    const valid = (position) =>
      position &&
      [position.x, position.y].every(
        (value) => Number.isInteger(value) && value >= -2147483648 && value <= 2147483647,
      );
    const positioned = monitors.map((output) => {
      const duplicate = names.has(output.name) || duplicates.has(output.name);
      names.add(output.name);
      const position = output.position || positions.get(output.name);
      return { output, position, valid: !duplicate && valid(position) };
    });
    if (!positioned.every((entry) => entry.valid)) return monitors;
    return positioned
      .sort(
        (a, b) =>
          a.position.x - b.position.x ||
          a.position.y - b.position.y ||
          a.output.name.localeCompare(b.output.name, "en", { numeric: true }),
      )
      .map((entry) => entry.output);
  }
  const wayland = Boolean(env.WAYLAND_DISPLAY || env.XDG_SESSION_TYPE === "wayland");
  async function catalog(owner) {
    if (closed || owner.isDestroyed?.() || !experimental()) return { sources: [], enabled: false };
    if (!browsers.has(owner)) {
      browsers.add(owner);
      const ended = () => {
        void closePicker(owner);
      };
      owner.once?.("destroyed", ended);
      browserCleanup.set(owner, () => owner.removeListener?.("destroyed", ended));
    }
    const version = browserVersions.get(owner) || 0;
    const current = () =>
      !closed &&
      !owner.isDestroyed?.() &&
      experimental() &&
      browsers.has(owner) &&
      (browserVersions.get(owner) || 0) === version;
    const inventories = {};
    const methods = await capabilities(inventories);
    if (!current()) return { sources: [], enabled: false };
    const previous = catalogs.get(owner);
    const entries = new Map();
    const sources = [];
    const add = (method, source, kind, name, available, reason = "") => {
      if (typeof source !== "string" || !source || source.length > 256 || /\p{Cc}/u.test(source))
        return;
      const identity = `${method}:${source}`;
      const token = previous?.identities.get(identity) || randomUUID();
      entries.set(token, { method, source, kind, identity, name, available });
      sources.push({
        token,
        kind,
        name: String(name)
          .replace(/\p{Cc}/gu, "")
          .slice(0, 256),
        available,
        reason,
      });
    };
    let monitors;
    let method = "kms";
    const directAllowed = (await permission()).allowed === true;
    if (!directAllowed)
      await Promise.all(
        [...previewJobs]
          .filter(([, job]) => job.owner === owner && monitorMethod(job.method))
          .map(([token, job]) => stopJob(token, job)),
      );
    if (directAllowed && exists(kms)) monitors = inventories.kms;
    if (
      directAllowed &&
      (!monitors?.available || !methods.includes("kms")) &&
      methods.includes("wayland-direct")
    ) {
      monitors = inventories.direct;
      method = "wayland-direct";
    }
    const ordered = orderedMonitors(monitors?.outputs || [], inventories.direct?.outputs);
    for (const [index, output] of ordered.entries()) {
      const needsPermission =
        method === "kms" && (!directAllowed || monitors.authorizationRequired === true);
      const available =
        methods.includes(method) &&
        monitors.available === true &&
        output.supported === true &&
        !needsPermission;
      add(
        method,
        output.id,
        "screen",
        `Screen ${index + 1} (${output.name})`,
        available,
        needsPermission
          ? "Allow direct screen capture in DPE Settings."
          : available
            ? ""
            : "This screen is unavailable for optimized capture.",
      );
    }
    if (current() && methods.includes("vkcapture")) {
      try {
        const listing = await gameBroker().command({ op: "list" });
        if (!current()) return { sources: [], enabled: false };
        const occupied = listing.compatibility === "in-use";
        const reasons = occupied ? ["occupied"] : [];
        for (const source of (listing.sources || []).slice(0, 32)) {
          if (occupied && source.protocol === "obs") continue;
          if (source.available !== true) {
            reasons.push("unavailable");
            continue;
          }
          // Bundled hooks use an independent endpoint and remain usable while
          // another application owns the stock OBS endpoint.
          add("vkcapture", source.token, "window", source.name, true);
        }
        noteHiddenGames(reasons);
      } catch {
        /* A disconnected hook must not hide monitors. */
        if (current()) noteHiddenGames(["failed"]);
      }
    }
    if (!current()) return { sources: [], enabled: false };
    catalogs.set(owner, {
      entries,
      identities: new Map([...entries].map(([token, source]) => [source.identity, token])),
      expires: Date.now() + 120000,
    });
    return { sources, enabled: true };
  }
  function arm(owner, token) {
    if (!experimental() || typeof token !== "string")
      throw new Error("Experimental capture is disabled.");
    const inventory = catalogs.get(owner);
    const source = inventory?.entries.get(token);
    if (!source?.available || inventory.expires < Date.now())
      throw new Error("Refresh and choose an available source.");
    void stopPreview(owner);
    armed.set(owner, { ...source, expires: Date.now() + 15000 });
    return { armed: true };
  }
  async function requestArmed(owner, value) {
    const source = armed.get(owner);
    armed.delete(owner);
    if (!source) return { action: "native" };
    if (!experimental() || source.expires < Date.now()) return { action: "cancel" };
    if (monitorMethod(source.method) && !(await permission()).allowed)
      return { action: "error", error: "Allow direct screen capture in DPE Settings first." };
    return request(
      owner,
      {
        ...value,
        method: source.method,
        sourceType: source.kind === "screen" ? 0 : value.sourceType,
      },
      source.source,
    );
  }
  async function capabilities(inventories = {}) {
    const methods = [];
    if (wayland && exists(direct)) {
      try {
        inventories.direct = await list(direct);
        if (inventories.direct.available) methods.push("wayland-direct");
      } catch {
        /* Unavailable compositor. */
      }
    }
    if (
      wayland &&
      ["vkcapture-broker.py", "dpe-vkcapture-source"].every((name) =>
        exists(path.join(bundle, name)),
      )
    )
      methods.push("vkcapture");
    methodReasons.kms = platform === "linux" ? "helper-missing" : "unsupported";
    if (platform === "linux" && exists(kms)) {
      try {
        const result = await list(kms);
        inventories.kms = result;
        if (result?.version === 1 && result.backend === "kms") {
          methodReasons.kms = KMS_REASONS.includes(result.reason) ? result.reason : "unsupported";
          if (result.available === true) {
            methods.push("kms");
            delete methodReasons.kms;
          }
        } else methodReasons.kms = "unsupported";
      } catch {
        methodReasons.kms = "unsupported";
      }
    }
    return methods;
  }
  function gameBroker() {
    if (broker) return broker;
    // The packaged Python sidecar is data, not an ELF helper. Invoke its system
    // interpreter explicitly so staging modes and Python environment cannot break discovery.
    broker = run(
      "/usr/bin/python3",
      [
        "-I",
        path.join(bundle, "vkcapture-broker.py"),
        "--socket",
        gameSocket,
        "--producer",
        path.join(bundle, "dpe-vkcapture-source"),
        "--render-node",
        "auto",
      ],
      env,
    );
    const current = broker;
    current.events.on("failure", (error) => {
      if (broker === current) broker = undefined;
      if (gameOwner?.helper === current) {
        const session = gameOwner;
        session.startFailure = error || new Error("Game capture stopped before it was ready.");
        gameOwner = undefined;
        if (session.phase !== "starting") end(session);
      }
    });
    current.events.on("message", (message) => {
      if (
        ["ended", "stopped"].includes(message?.event) &&
        gameOwner?.helper === current &&
        message.token === gameOwner.sourceId
      ) {
        const session = gameOwner;
        if (message.event === "stopped") gameOwner = undefined;
        if (session.phase === "starting") {
          session.startFailure = gameStartError(message);
          return;
        }
        end(session);
      }
    });
    return current;
  }
  function gameStartError(message) {
    return new Error(
      message?.errorCode === "unsupported-buffer"
        ? "This game’s buffer format or GPU cannot be imported. Try SDR mode or the System picker."
        : "Game capture stopped before it was ready. Refresh and choose the source again.",
    );
  }
  function key(owner, id) {
    return `${owner.id}:${id}`;
  }
  function end(session) {
    if (!session || session.closed) return;
    session.closed = true;
    try {
      session.owner.send?.("dpe-capture-ended", { requestId: session.requestId, reason: "ended" });
    } catch {
      /* The owning renderer may already have gone away. */
    }
    session.abort.abort();
    for (const remove of session.cleanup) remove();
    session.cleanup.length = 0;
    if (session.timer) clearTimeout(session.timer);
    if (session.method === "vkcapture") {
      if (gameOwner === session) session.helper?.command({ op: "stop" }).catch(() => {});
    } else session.helper?.stop();
    sessions.delete(key(session.owner, session.requestId));
    if (pending === session) pending = undefined;
    session.resolve?.({ action: "cancel" });
    session.resolve = undefined;
  }
  async function refresh() {
    const session = pending;
    if (!session || session.phase === "starting") return state();
    const revision = (session.refreshId = (session.refreshId || 0) + 1);
    try {
      let sources;
      if (monitorMethod(session.method)) {
        const result = await list(session.method === "kms" ? kms : direct);
        if (
          session.method === "kms" &&
          result?.version === 1 &&
          result.backend === "kms" &&
          pending === session &&
          revision === session.refreshId
        ) {
          session.authorizationRequired =
            result.reason !== "permission-blocked" &&
            (result.authorizationRequired === true || session.permissionFailed === true);
        }
        if (
          session.method === "kms" &&
          (result?.version !== 1 || result.backend !== "kms" || result.available !== true)
        )
          throw kmsError(result);
        if (!result.available)
          throw new Error(
            "Direct capture is unavailable on this compositor. Choose System picker instead.",
          );
        sources = (result.outputs || []).slice(0, 64).map((source) => ({
          id: source.id,
          name: `${source.name}${source.description ? ` — ${source.description}` : ""}`,
          available: source.supported === true,
        }));
      } else {
        session.helper = gameBroker();
        const result = await session.helper.command(
          { op: "list" },
          { signal: session.abort.signal },
        );
        sources = (result.sources || []).slice(0, 32).map((source) => ({
          id: source.token,
          name: source.name,
          available: source.available === true,
        }));
      }
      if (pending !== session || session.closed || revision !== session.refreshId) return state();
      session.sources = sources
        .filter(
          (source) =>
            typeof source.id === "string" &&
            source.id.length > 0 &&
            source.id.length <= 256 &&
            !/\p{Cc}/u.test(source.id) &&
            typeof source.name === "string",
        )
        .map((source) => ({ ...source, name: source.name.replace(/\p{Cc}/gu, "").slice(0, 256) }));
      session.error = "";
    } catch (error) {
      if (!session.closed && revision === session.refreshId) session.error = error.message;
    }
    return state();
  }
  function state() {
    if (!pending) return null;
    const { id, method, phase, sources, error } = pending;
    return {
      id,
      method,
      phase,
      sources,
      error,
      authorizationRequired: pending.authorizationRequired === true,
      authorizationPending: pending.authorizationPending === true,
      launcher: method === "vkcapture" ? path.join(bundle, "gamecapture-launch.py") : null,
    };
  }
  async function request(owner, value, inlineSource) {
    if (
      !Number.isSafeInteger(value?.requestId) ||
      value.requestId <= 0 ||
      value.requestId > 0xffffffff ||
      ![0, 1, 2].includes(value.sourceType) ||
      !["wayland-direct", "vkcapture", "kms"].includes(value.method)
    )
      throw new Error("Invalid capture request.");
    const generation = ++requestGeneration;
    if (closed || owner.isDestroyed?.()) return { action: "cancel" };
    const methods = await capabilities();
    if (closed || owner.isDestroyed?.() || generation !== requestGeneration)
      return { action: "cancel" };
    if (!methods.includes(value.method)) return { action: "error" };
    if (value.method === "wayland-direct" && !(await permission()).allowed)
      return { action: "error", error: "Allow direct screen capture in DPE Settings first." };
    if (closed || owner.isDestroyed?.() || generation !== requestGeneration)
      return { action: "cancel" };
    if (monitorMethod(value.method) && value.sourceType === 1) return { action: "error" };
    // Opening or cancelling a replacement picker must preserve the active share.
    // Retire it only when the native consumer starts the replacement source.
    if (pending) end(pending);
    for (const session of sessions.values())
      if (session.owner === owner && session.phase !== "active") end(session);
    const session = {
      id: randomUUID(),
      owner,
      requestId: value.requestId,
      method: value.method,
      fps: Number.isInteger(value.fps) && value.fps > 0 && value.fps <= 240 ? value.fps : 60,
      phase: "choosing",
      sources: [],
      error: "",
      closed: false,
      abort: new AbortController(),
      cleanup: [],
    };
    pending = session;
    sessions.set(key(owner, value.requestId), session);
    const response = new Promise((resolve) => {
      session.resolve = resolve;
    });
    const destroyed = () => end(session);
    owner.once("destroyed", destroyed);
    const navigated = (details, _url, inPlace, mainFrame) => {
      // Discord changes channels/routes with the History API. Electron emits
      // this event for those same-document changes as well as actual reloads.
      const main = details?.isMainFrame ?? mainFrame;
      const sameDocument = details?.isSameDocument ?? inPlace;
      if (main && !sameDocument) end(session);
    };
    owner.on("did-start-navigation", navigated);
    session.cleanup.push(
      () => owner.off("destroyed", destroyed),
      () => owner.off("did-start-navigation", navigated),
    );
    try {
      await stopPreview();
      if (session.closed) return response;
      if (inlineSource) {
        await refresh();
        if (session.closed) return response;
        if (session.error) throw new Error(session.error);
        if (session.authorizationRequired)
          throw new Error("Allow direct screen capture in DPE Settings first.");
        await choose(session.id, inlineSource);
        if (!session.closed && session.phase === "choosing") {
          session.resolve?.({ action: "error", error: session.error });
          session.resolve = undefined;
          end(session);
        }
        return response;
      }
      const window = openSettings(owner);
      const windowClosed = () => {
        if (pending === session) end(session);
      };
      window.once("closed", windowClosed);
      session.cleanup.push(() => window.off("closed", windowClosed));
      await refresh();
    } catch (error) {
      if (inlineSource) {
        session.resolve?.({ action: "error", error: error.message });
        session.resolve = undefined;
      }
      end(session);
    }
    return response;
  }
  async function choose(id, sourceId, authorize) {
    const session = pending;
    if (!session || session.id !== id || session.phase !== "choosing")
      throw new Error("This source selection has ended.");
    if (!session.sources.some((source) => source.id === sourceId && source.available))
      throw new Error("Choose an available source.");
    const authorization =
      session.method === "kms" && session.authorizationRequired === true && authorize === true;
    if (session.method === "kms" && session.authorizationRequired && !authorization)
      throw kmsError({ reason: "permission-required" });
    if (
      session.method === "vkcapture" &&
      gameOwner &&
      gameOwner !== session &&
      (gameOwner.owner !== session.owner || gameOwner.phase !== "active")
    )
      throw new Error(
        "Another game capture is active or stopping. Stop it before choosing a new game.",
      );
    session.phase = "starting";
    session.error = "";
    session.startFailure = null;
    session.authorizationPending = authorization;
    try {
      if (session.method === "wayland-direct" && !(await permission()).allowed)
        throw kmsError({ reason: "permission-required" });
      if (session.closed) return state();
      let ready;
      if (monitorMethod(session.method)) {
        session.helper = run(
          session.method === "kms" ? kms : direct,
          [
            "--output",
            sourceId,
            "--fps",
            String(session.fps),
            ...(session.method === "kms" ? ["--render-node", "auto"] : []),
            ...(authorization ? ["--authorize", "true"] : []),
          ],
          env,
        );
        const current = session.helper;
        const failed = (error) => {
          if (session.helper !== current) return;
          session.startFailure ||=
            error || new Error("Capture helper stopped before it was ready.");
          if (["selected", "active"].includes(session.phase)) end(session);
        };
        const ended = (message) => {
          if (session.helper === current && ["ended", "stopped", "error"].includes(message?.event))
            failed(
              session.method === "kms"
                ? kmsError(message)
                : new Error("Capture helper stopped before it was ready."),
            );
        };
        current.events.on("failure", failed);
        current.events.on("message", ended);
        session.cleanup.push(
          () => current.events.off("failure", failed),
          () => current.events.off("message", ended),
        );
        ready = await session.helper.wait(
          (value) =>
            value?.event === "ready" ||
            (session.method === "kms" && ["ended", "stopped", "error"].includes(value?.event)),
          authorization ? 115000 : 10000,
          session.abort.signal,
        );
        if (ready.event !== "ready") throw kmsError(ready);
      } else {
        // The game broker owns one producer. Commit its replacement only after
        // the user chooses an available game, then wait for the old child to
        // release the hook socket before starting the next producer.
        if (gameOwner && gameOwner !== session) {
          const previous = gameOwner;
          const stopped = session.helper.wait(
            (value) => value?.event === "stopped" && value.token === previous.sourceId,
            10000,
            session.abort.signal,
          );
          end(previous);
          await stopped;
          if (session.closed || pending !== session) return state();
        }
        session.sourceId = sourceId;
        gameOwner = session;
        const readyPromise = session.helper.wait(
          (value) =>
            ["ready", "ended", "stopped"].includes(value?.event) && value.token === sourceId,
          10000,
          session.abort.signal,
        );
        // Both promises are observed even when start fails before a ready event.
        // Observe the start reply even after UI cancellation. A rejected start
        // has no producer and may never emit stopped; it must release ownership.
        const command = session.helper
          .command({ op: "start", token: sourceId, fps: session.fps })
          .catch((error) => {
            if (session.closed && gameOwner === session) gameOwner = undefined;
            throw error;
          });
        const result = await Promise.all([command, readyPromise]);
        ready = result[1];
        if (ready.event !== "ready") throw gameStartError(ready);
      }
      if (session.closed || pending !== session) return state();
      if (session.startFailure) throw session.startFailure;
      if (!Number.isInteger(ready.nodeId) || ready.nodeId < 0 || ready.nodeId >= 0xffffffff)
        throw new Error("Capture helper returned an invalid stream.");
      pending = undefined;
      session.phase = "selected";
      session.resolve({
        action: "selected",
        socketPath,
        nodeId: ready.nodeId,
        method: session.method,
        sourceType: monitorMethod(session.method) ? "screen" : "window",
        ...(["relay", "copy"].includes(ready.handoff)
          ? {
              handoff: ready.handoff,
              handoffFallback: [
                "export-unavailable",
                "synchronization-unavailable",
                "upstream-implicit-sync",
                "cursor-composition",
              ].includes(ready.handoffFallback)
                ? ready.handoffFallback
                : null,
            }
          : {}),
      });
      session.resolve = undefined;
      session.timer = setTimeout(() => end(session), 30000);
      session.timer.unref();
    } catch (error) {
      if (session.closed) return state();
      if (monitorMethod(session.method)) session.helper?.stop();
      else if (gameOwner === session) {
        // A start error may have no child and therefore no stopped event. End
        // this owned broker group rather than leaving a producer or stale lock.
        gameOwner = undefined;
        session.helper?.stop();
        if (broker === session.helper) broker = undefined;
      }
      session.phase = "choosing";
      session.error = error.message;
      if (session.method === "kms") {
        if (error.code === "permission-required") {
          session.permissionFailed = true;
          session.authorizationRequired = true;
        } else if (error.code === "permission-blocked") {
          session.permissionFailed = false;
          session.authorizationRequired = false;
        }
      }
    } finally {
      session.authorizationPending = false;
    }
    return state();
  }
  function event(owner, value) {
    const session = sessions.get(key(owner, value?.requestId));
    if (!session) return;
    if (value.event === "started" && session.phase === "selected") {
      clearTimeout(session.timer);
      session.phase = "active";
      for (const previous of sessions.values())
        if (previous !== session && previous.owner === owner && previous.phase === "active")
          end(previous);
    } else if (["stopped", "cancelled"].includes(value.event)) end(session);
  }
  function rate(owner, fps) {
    if (!Number.isInteger(fps) || fps < 1 || fps > 240) return;
    for (const session of sessions.values())
      if (session.owner === owner && !session.closed && session.fps !== fps) {
        session.fps = fps;
        if (session.method === "vkcapture") {
          if (gameOwner === session) session.helper?.command({ op: "rate", fps }).catch(() => {});
        } else session.helper?.send({ op: "rate", fps });
      }
  }
  return {
    catalog,
    preview,
    stopPreview,
    closePicker,
    arm,
    disarm(owner) {
      armed.delete(owner);
    },
    requestArmed,
    capabilities,
    async capabilityStatus() {
      return { methods: await capabilities(), methodReasons: { ...methodReasons } };
    },
    request,
    state,
    refresh,
    choose,
    event,
    rate,
    cancel(id) {
      if (pending?.id === id) end(pending);
      return state();
    },
    close() {
      closed = true;
      void stopPreview();
      for (const owner of browsers) browserCleanup.get(owner)?.();
      browsers.clear();
      requestGeneration++;
      for (const session of sessions.values()) end(session);
      broker?.stop();
      broker = undefined;
    },
  };
}
async function rendererCapabilities(controller, owner, env = process.env) {
  const status = await controller.capabilityStatus();
  const wayland = Boolean(env.WAYLAND_DISPLAY || env.XDG_SESSION_TYPE === "wayland");
  if (wayland || !status.methods.includes("kms")) return status;
  let supported = false;
  try {
    supported = await owner.executeJavaScript(
      'Boolean(globalThis[Symbol.for("dpe.capturePolicy")]?.supported?.())',
    );
  } catch {
    /* An uninitialized or changed store cannot establish native support. */
  }
  return supported === true
    ? status
    : {
        methods: status.methods.filter((method) => method !== "kms"),
        methodReasons: { ...status.methodReasons, kms: "native-picker-unavailable" },
      };
}
let controller;
function register() {
  if (controller) return controller;
  const { ipcMain, app } = require("electron");
  const trusted = (event) =>
    event.senderFrame === event.sender.mainFrame &&
    require("./presentation.cjs").trusted(event.sender);
  controller = createController({
    openSettings(owner) {
      const window = require("./config.cjs").open("screensharing", owner);
      window.show();
      window.focus();
      return window;
    },
  });
  ipcMain.handle("dpe-capture-methods", (event) =>
    trusted(event)
      ? rendererCapabilities(controller, event.sender)
      : { methods: [], methodReasons: {} },
  );
  ipcMain.handle("dpe-capture-request", (event, value) => {
    if (!trusted(event)) throw new Error("Invalid capture owner.");
    return controller.request(event.sender, value);
  });
  ipcMain.handle("dpe-capture-picker", (event, value) => {
    if (!trusted(event)) throw new Error("Invalid capture owner.");
    if (value?.op === "open-settings") {
      const window = require("./config.cjs").open("screensharing", event.sender);
      window.show();
      window.focus();
      return {};
    }
    if (value?.op === "list") return controller.catalog(event.sender);
    if (value?.op === "preview") return controller.preview(event.sender, value);
    if (value?.op === "preview-stop") return controller.stopPreview(event.sender);
    if (value?.op === "picker-close")
      return controller.closePicker(event.sender, value.handoff === true);
    if (value?.op === "arm") return controller.arm(event.sender, value.token);
    if (value?.op === "disarm") {
      controller.disarm(event.sender);
      return {};
    }
    throw new Error("Invalid capture picker action.");
  });
  ipcMain.handle("dpe-capture-inline-request", (event, value) => {
    if (!trusted(event)) throw new Error("Invalid capture owner.");
    return controller.requestArmed(event.sender, value);
  });
  ipcMain.on("dpe-capture-event", (event, value) => {
    if (trusted(event)) controller.event(event.sender, value);
  });
  ipcMain.on("dpe-capture-rate", (event, value) => {
    if (trusted(event)) controller.rate(event.sender, value);
  });
  app.once("before-quit", () => controller.close());
  return controller;
}
module.exports = {
  worker,
  createController,
  rendererCapabilities,
  register,
  state: () => controller?.state() ?? null,
  refresh: () => controller?.refresh() ?? null,
  choose: (id, source, authorize) => controller?.choose(id, source, authorize === true),
  cancel: (id) => controller?.cancel(id),
};
