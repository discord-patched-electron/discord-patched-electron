const { contextBridge, ipcRenderer, discord } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
if (typeof discord?.install !== 'function') throw new Error('Missing electron.discord.install');
discord.install('createDiscordStream');
const addon = require(process.env.DPE_PROBE_ADDON);

contextBridge.exposeInMainWorld('fixtureAPI', {
  push: id => addon.push(id),
  counts: () => addon.counts(),
  progress: step => ipcRenderer.send('fixture-progress', { step }),
  report: result => ipcRenderer.send('fixture-result', result),
  collect: () => global.gc?.(),
  loadModules: async () => {
    // Load modules without a connection; optional batch work uses fake devices.
    const modules = process.env.DPE_PROBE_MODULES;
    const voice = require(path.join(modules, 'discord_voice/discord_voice.node'));
    const logs = path.join(process.env.DPE_PROBE_PROFILE, 'krisp-logs');
    fs.mkdirSync(logs, { recursive: true });
    globalThis.window.DiscordNative = { fileManager: { getAndCreateLogDirectorySync: () => logs, logLevelSync: () => 1 } };
    const krisp = require(path.join(modules, 'discord_krisp'));
    const models = await krisp.getNcModels();
    const result = {
      voice: { loaded: true, exports: Object.keys(voice).sort() },
      krisp: { loaded: true, initialization_call_completed: true, models, exports: Object.keys(krisp).sort() },
    };
    if (process.env.DPE_PROBE_AUDIO_INPUT) {
      const errors = [];
      const voiceData = path.join(process.env.DPE_PROBE_PROFILE, 'voice');
      fs.mkdirSync(voiceData, { recursive: true });
      voice.initialize({ audioSubsystem: 'standard', logLevel: 1, dataDirectory: voiceData,
        logDirectory: voiceData, maxLogBytes: 1000000, useFakeAudioCapture: true,
        useFakeVideoCapture: true, offloadAdmControls: false, asyncVideoInputDeviceInit: false });
      voice.setVoiceProcessingErrorCallback((...args) => errors.push(args));
      voice.setKrispPath(path.join(modules, 'discord_krisp'));
      const batches = [];
      for (const enabled of [false, true, false]) {
        voice.setTransportOptions({ echoCancellation: false, echoCancellationPreEcho: false,
          noiseSuppression: false, automaticGainControl: false, noiseCancellation: enabled,
          noiseCancellationDuringProcessing: enabled, skipNoiseCancellationIfMuted: false });
        const output = path.join(process.env.DPE_PROBE_PROFILE, 'audio-' + batches.length);
        fs.mkdirSync(output);
        ipcRenderer.send('fixture-progress', { step: 'audio-batch', enabled });
        const files = [];
        const completion = await new Promise(resolve => {
          voice.processBatchAudioFiles(process.env.DPE_PROBE_AUDIO_INPUT, output,
            (...args) => files.push(args), (processed, failed) => resolve({ processed, failed }));
        });
        batches.push({ enabled, output, files, ...completion });
      }
      result.audio_batches = { batches, errors };
    }
    return result;
  },
});
