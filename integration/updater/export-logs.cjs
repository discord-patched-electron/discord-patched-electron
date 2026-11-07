const { execFile } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
let busy = false;

async function exportLogs(window) {
  if (busy) throw new Error("A log export is already running.");
  busy = true;
  let temporary;
  try {
    const { app, dialog } = require("electron");
    let downloads;
    try {
      downloads = app.getPath("downloads");
    } catch {
      downloads = os.homedir();
    }
    const selection = await dialog.showSaveDialog(window, {
      title: "Export logs for a bug report",
      defaultPath: path.join(
        downloads,
        `dpe-logs-${new Date().toISOString().replace(/[:.]/g, "-")}.zip`,
      ),
      filters: [{ name: "ZIP archive", extensions: ["zip"] }],
    });
    if (selection.canceled || !selection.filePath) return { canceled: true };
    let fileOnly = false;
    try {
      temporary = await fs.mkdtemp(path.join(path.dirname(selection.filePath), ".dpe-logs-"));
    } catch (error) {
      if (!["EACCES", "EPERM", "EROFS", "ENOENT", "ENOTDIR"].includes(error.code)) throw error;
      // Portal grants can allow writing the chosen file without allowing sibling files.
      temporary = await fs.mkdtemp(path.join(os.tmpdir(), "dpe-logs-"));
      fileOnly = true;
    }
    const output = path.join(temporary, "logs.zip");
    await fs.writeFile(output, "", { mode: 0o600 });
    const profile = process.env.DPE_PROFILE_DIR || app.getPath("userData");
    const crashPaths = [path.join(profile, "Crashpad"), path.join(profile, "Crash Reports")];
    try {
      crashPaths.unshift(app.getPath("crashDumps"));
    } catch {
      /* Runtime may not expose Crashpad. */
    }
    const metadata = {
      ...require("./presentation.cjs").metadata(),
      ...process.versions,
      platform: process.platform,
      arch: process.arch,
    };
    let timer;
    try {
      metadata.gpu = await Promise.race([
        app.getGPUInfo("basic"),
        new Promise((resolve) => {
          timer = setTimeout(() => resolve({ unavailable: "GPU diagnostics timed out" }), 1500);
        }),
      ]);
    } catch {
      metadata.gpu = { unavailable: "GPU diagnostics unavailable" };
    } finally {
      clearTimeout(timer);
    }
    for (const [key, read] of [
      ["processes", () => app.getAppMetrics()],
      ["gpuFeatures", () => app.getGPUFeatureStatus()],
    ]) {
      try {
        metadata[key] = read();
      } catch {
        /* Optional diagnostics must not block saving. */
      }
    }
    const probes = [
      ["screensharing", () => require("./screensharing.cjs").snapshot()],
      ["capturePermission", () => require("./capture-permission.cjs").status()],
      [
        "capture",
        () => {
          const state = require("./capture-backends.cjs").state();
          if (!state) return { active: false };
          return Object.fromEntries(
            ["method", "phase", "error", "authorizationRequired", "authorizationPending"].map(
              (key) => [key, state[key]],
            ),
          );
        },
      ],
      [
        "settings",
        () => {
          const profile = require("./profile.cjs").config();
          return {
            screensharing: require("../video/settings.cjs").normalize(profile.screensharing),
            desktop: require("../desktop/preferences.cjs").normalize(profile.desktop),
            audioAutoDetect: profile.audioAutoDetect,
            audioSkipScreenPicker: profile.audioSkipScreenPicker,
            noTrack: profile.noTrack,
            quickstart: profile.quickstart,
          };
        },
      ],
      [
        "spelling",
        () => {
          const state = require("../desktop/spellcheck.cjs").status();
          return {
            enabled: state.enabled,
            languages: state.languages,
            availableCount: state.availableLanguages?.length,
          };
        },
      ],
      [
        "flags",
        () => {
          const result = {};
          for (const name of [
            "ozone-platform",
            "use-gl",
            "use-angle",
            "enable-features",
            "disable-features",
            "disable-gpu",
            "disable-gpu-sandbox",
            "enable-logging",
            "v",
            "vmodule",
            "no-sandbox",
          ])
            if (app.commandLine.hasSwitch(name))
              result[name] = app.commandLine.getSwitchValue(name).slice(0, 2048);
          return result;
        },
      ],
    ];
    await Promise.all(
      probes.map(async ([name, read]) => {
        let timer;
        try {
          metadata[name] = await Promise.race([
            Promise.resolve().then(read),
            new Promise((resolve) => {
              timer = setTimeout(() => resolve({ unavailable: "Diagnostic timed out" }), 1800);
            }),
          ]);
        } catch {
          metadata[name] = { unavailable: "Diagnostic unavailable in this process" };
        } finally {
          clearTimeout(timer);
        }
      }),
    );
    const request = {
      profile,
      app: process.env.DPE_APP_ROOT,
      crashPaths,
      executable: await fs.realpath(process.execPath).catch(() => process.execPath),
      executables: [process.env.DPE_RUNTIME_SOURCE, process.env.DPE_RUNTIME_EXECUTABLE].filter(
        Boolean,
      ),
      logFiles: [
        process.env.CHROME_LOG_FILE,
        ...(() => {
          try {
            return [
              app.commandLine.getSwitchValue("log-file"),
              app.commandLine.getSwitchValue("log-net-log"),
            ];
          } catch {
            return [];
          }
        })(),
      ].filter((file) => typeof file === "string" && path.isAbsolute(file)),
      logPaths: [
        ...(process.env.DPE_CLIENT_HOST ? [path.join(process.env.DPE_CLIENT_HOST, "modules")] : []),
        ...["EQUICORD_USER_DATA_DIR", "VENCORD_USER_DATA_DIR"].flatMap((key) =>
          process.env[key] ? [path.join(process.env[key], "logs")] : [],
        ),
        ...(() => {
          try {
            return [app.getPath("logs")];
          } catch {
            return [];
          }
        })(),
      ],
      metadata,
    };
    await new Promise((resolve, reject) => {
      const child = execFile(
        "python3",
        [path.join(__dirname, "export_logs.py"), output],
        {
          timeout: 90 * 1000,
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
        (error, _stdout, stderr) =>
          error ? reject(new Error(stderr.trim() || error.message)) : resolve(),
      );
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(request));
    });
    if (fileOnly) await fs.copyFile(output, selection.filePath);
    else {
      try {
        await fs.rename(output, selection.filePath);
      } catch (error) {
        if (!["EXDEV", "EACCES", "EPERM", "ENOTSUP"].includes(error.code)) throw error;
        await fs.copyFile(output, selection.filePath);
      }
    }
    return { canceled: false, filePath: selection.filePath };
  } catch (error) {
    try {
      require("./diagnostic-log.cjs").record("log-export-failed", {
        code: error.code,
        message: error.message,
      });
    } catch {
      /* Preserve the original failure. */
    }
    throw error;
  } finally {
    busy = false;
    if (temporary) await fs.rm(temporary, { recursive: true, force: true }).catch(() => {});
  }
}
module.exports = { exportLogs };
