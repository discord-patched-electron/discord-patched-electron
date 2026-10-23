'use strict';
// Small wrapper around Discord's existing native per-process PulseAudio path.
// No remote UI patches, browser capture fallback, or microphone-device changes.
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');

function processIdentity(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 1) return null;
  try {
    if (fs.statSync(`/proc/${pid}`).uid !== process.getuid()) return null;
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    if (fields[0] === 'Z') return null;
    return { pid, parent: Number(fields[1]), start: fields[19] };
  } catch { return null; }
}

function windowPid(source) {
  // A portal/PipeWire node ID is not an X11 window or an application PID.
  // A Wayland session can also export DISPLAY for Xwayland. Numeric portal
  // tokens must never accidentally resolve to an unrelated Xwayland window.
  if (process.env.WAYLAND_DISPLAY || process.env.XDG_SESSION_TYPE === 'wayland') return null;
  const match = /^window:([1-9][0-9]*):[0-9]+$/.exec(String(source.id));
  if (!match || !/^(?:unix)?:[0-9]+(?:\.[0-9]+)?$/.test(process.env.DISPLAY || '')) return null;
  try {
    const text = execFileSync('/usr/bin/xprop', ['-id', match[1], '-notype', '_NET_WM_PID'],
      { encoding: 'utf8', timeout: 500, maxBuffer: 4096, stdio: ['ignore', 'pipe', 'ignore'] });
    const value = /^_NET_WM_PID = ([0-9]+)\s*$/.exec(text);
    return value ? processIdentity(Number(value[1])) : null;
  } catch { return null; }
}

function descendant(pid, root) {
  const seen = new Set();
  for (let depth = 0; depth < 64 && !seen.has(pid); depth++) {
    const identity = processIdentity(pid);
    if (!identity) return false;
    if (pid === root.pid) return identity.start === root.start;
    seen.add(pid);
    pid = identity.parent;
  }
  return false;
}

function resolve(source, hint) {
  const localX11 = !process.env.WAYLAND_DISPLAY && /^(?:unix)?:[0-9]+(?:\.[0-9]+)?$/.test(process.env.DISPLAY || '');
  const root = windowPid(source) || (!localX11 ? processIdentity(hint) : null);
  if (!root || descendant(process.pid, root)) return null;
  if (source.owner && (source.owner.pid !== root.pid || source.owner.start !== root.start)) return null;
  source.owner ||= root;
  try {
    const streams = JSON.parse(execFileSync('/usr/bin/pactl', ['-f', 'json', 'list', 'sink-inputs'],
      { encoding: 'utf8', timeout: 1000, maxBuffer: 4 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] }));
    const pids = [...new Set(streams.map(s => Number(s.properties?.['application.process.id']))
      .filter(pid => pid !== process.pid && descendant(pid, root)))];
    // Native Discord can monitor all streams from ONE PID. Do not guess when
    // several descendants own audio, and never select a system-wide sentinel.
    if (pids.length > 1) return null;
    return processIdentity(pids[0] ?? root.pid);
  } catch { return null; }
}

function sourceKind(id, type) {
  if (String(id).startsWith('window:') || type === 'window') return 'window';
  if (String(id).startsWith('screen:') || type === 'screen') return 'screen';
  return 'unknown';
}

function wrapCapture(instance, { enabled = process.env.DPE_APP_AUDIO === '1', resolvePid = resolve,
  identity = processIdentity, interval = 1000, warn = message => console.warn('[dpe-app-audio]', message) } = {}) {
  if (!enabled) return instance;
  let source = null, requested = 0, hint = 0, loopback = false, selected = null, timer = null, disposed = false, warned = false;
  const transport = instance.setTransportOptions.bind(instance);
  let endedCallback;
  instance.setOnDesktopSourceEnded((...args) => {
    if (disposed) return;
    stop(); source = null; requested = 0; hint = 0;
    if (typeof endedCallback === 'function') endedCallback(...args);
  });
  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    selected = null;
    transport({ soundsharePid: 0, soundshareLoopback: false });
  }
  function update(extra = {}) {
    if (disposed) return;
    if (!requested || !source) {
      if (!Object.keys(extra).length) return;
      transport({ ...extra, soundsharePid: 0, soundshareLoopback: false });
      return;
    }
    if (source.kind === 'screen') {
      transport({ ...extra, soundsharePid: requested, soundshareLoopback: loopback });
      return;
    }
    // A hint belongs to this source generation only; source switches erase it.
    const candidate = source.kind === 'window' ? resolvePid(source, hint) : null;
    const previous = selected;
    selected = candidate && candidate.pid > 1 ? candidate : null;
    if (!Object.keys(extra).length && previous?.pid === selected?.pid && previous?.start === selected?.start) return;
    transport({ ...extra, soundsharePid: selected?.pid ?? 0, soundshareLoopback: false });
    if (!selected && !warned) {
      warned = true;
      warn('Application audio unavailable: no unambiguous process for the selected window.');
    }
  }
  function select(id, type) {
    const same = source?.id === id && source?.type === type;
    if (same) return;
    const first = source === null;
    stop();
    source = { id, type, kind: sourceKind(id, type) };
    if (!first) hint = 0;
    warned = false;
    update({ soundsharePid: requested });
    if (source.kind !== 'screen') {
      timer = setInterval(() => {
        // Stop the previous stream before resolving a disappeared/reused PID.
        const now = selected && identity(selected.pid);
        if (selected && (!now || now.start !== selected.start)) {
          selected = null;
          transport({ soundsharePid: 0, soundshareLoopback: false });
        }
        update();
      }, interval);
      timer.unref?.();
    }
  }
  const methods = {
    setTransportOptions(options) {
      if (Object.hasOwn(options, 'soundshareLoopback')) loopback = options.soundshareLoopback === true;
      if (Object.hasOwn(options, 'soundsharePid')) {
        requested = Number.isSafeInteger(options.soundsharePid) && options.soundsharePid > 0 ? options.soundsharePid : 0;
        hint = requested > 1 ? requested : 0;
      }
      if (Object.hasOwn(options, 'soundsharePid') || Object.hasOwn(options, 'soundshareLoopback') || (source && source.kind !== 'screen')) {
        update(options);
      } else transport(options);
    },
    setDesktopSource(id, hook, type) {
      if (disposed) return;
      select(id, type);
      return instance.setDesktopSource(id, hook, type);
    },
    setDesktopSourceWithOptions(options) {
      if (disposed) return;
      select(options.sourceId, options.type);
      return instance.setDesktopSourceWithOptions(options);
    },
    clearDesktopSource() {
      if (disposed) return;
      stop(); source = null; requested = 0; hint = 0;
      return instance.clearDesktopSource();
    },
    setOnDesktopSourceEnded(callback) { endedCallback = callback; },
    destroy() {
      if (disposed) return;
      stop(); disposed = true;
      return instance.destroy();
    },
  };
  return new Proxy(instance, { get(target, name) {
    if (Object.hasOwn(methods, name)) return methods[name];
    const value = Reflect.get(target, name, target);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
}

let traceConnection = 0;
function traceBoundary(instance, { trace = process.env.DPE_APP_AUDIO_TRACE === '1',
  traceWrite = line => console.warn(line) } = {}) {
  if (!trace) return instance;
  const connection = ++traceConnection;
  let sequence = 0;
  function emit(event, fields = {}) {
    // Never pass entire options/callback objects to a logger: transport options
    // can contain encryption keys, endpoint addresses and other client state.
    try { traceWrite('[dpe-app-audio-trace] ' + JSON.stringify({ connection, sequence: ++sequence, event, ...fields })); }
    catch { /* Diagnostic output must not interrupt native calls. */ }
  }
  function scalar(value, allowed = []) {
    if (value === undefined) return undefined;
    if (Number.isSafeInteger(value) || typeof value === 'boolean' || allowed.includes(value)) return value;
    return 'redacted';
  }
  function source(id, type) {
    return {
      sourceId: typeof id === 'string' && /^(?:(?:window|screen):)?[0-9]+(?::[0-9]+)?$/.test(id) && id.length <= 64
        ? id : scalar(id),
      type: scalar(type, ['window', 'screen']),
    };
  }
  const inspect = {
    setTransportOptions: options => {
      const fields = {};
      for (const name of ['soundsharePid', 'soundshareLoopback', 'soundshareId', 'soundshareDiscardRearChannels']) {
        if (options && Object.hasOwn(options, name)) fields[name] = scalar(options[name]);
      }
      return Object.keys(fields).length ? fields : null;
    },
    setDesktopSource: (id, hook, type) => ({ ...source(id, type), videoHook: scalar(hook) }),
    setDesktopSourceWithOptions: options => source(options?.sourceId, options?.type),
    presentDesktopSourcePicker: style => ({ style: scalar(style) }),
    setGoLiveDevices: options => ({ audioDeviceSupplied: options?.audioInputDeviceId != null,
      videoDeviceSupplied: options?.videoInputDeviceId != null }),
    clearGoLiveDevices: () => ({}), clearDesktopSource: () => ({}), destroy: () => ({}),
  };
  const callbacks = new Set(['setOnDesktopSourceEnded', 'setOnSoundshare', 'setOnSoundshareEnded',
    'setOnSoundshareFailed', 'setDesktopSourceStatusCallback']);
  emit('connection', { wayland: Boolean(process.env.WAYLAND_DISPLAY || process.env.XDG_SESSION_TYPE === 'wayland'),
    xDisplayAvailable: Boolean(process.env.DISPLAY) });
  return new Proxy(instance, { get(target, name) {
    const value = Reflect.get(target, name, target);
    if (typeof value !== 'function') return value;
    if (callbacks.has(name)) return function (callback, ...rest) {
      return value.call(target, typeof callback === 'function' ? function (...args) {
        emit(name + ':callback', { values: args.slice(0, 4).map(arg =>
          arg && typeof arg === 'object' ? source(arg.sourceId, arg.type) : scalar(arg)) });
        return callback.apply(this, args);
      } : callback, ...rest);
    };
    if (Object.hasOwn(inspect, name)) return (...args) => {
      const fields = inspect[name](...args);
      if (fields) emit(name, fields);
      return value.apply(target, args);
    };
    return value.bind(target);
  } });
}

function wrap(instance, options = {}) {
  // Trace the caller's original arguments, before any experimental correction.
  // TRACE alone leaves audio selection unchanged and can inspect Wayland first.
  const enabled = options.enabled ?? process.env.DPE_APP_AUDIO === '1';
  const wayland = options.wayland ?? Boolean(process.env.WAYLAND_DISPLAY || process.env.XDG_SESSION_TYPE === 'wayland');
  const wrapped = enabled && wayland
    ? require('./dpe-wayland-audio.cjs').wrap(instance, options)
    : wrapCapture(instance, options);
  return traceBoundary(wrapped, options);
}

function installVoice(voice) {
  if (process.env.WAYLAND_DISPLAY || process.env.XDG_SESSION_TYPE === 'wayland')
    require('./dpe-wayland-audio.cjs').installVoice(voice);
}

module.exports = { wrap, installVoice, resolve, processIdentity, windowPid, descendant, sourceKind };
