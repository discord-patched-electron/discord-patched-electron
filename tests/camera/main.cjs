const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
const output = process.env.DPE_CAMERA_OUTPUT;
const udp = require("node:dgram").createSocket("udp4");
const packetTimes = [];
udp.on("message", (packet, remote) => {
  if (
    packet.length >= 12 &&
    packet[0] >> 6 === 2 &&
    (packet[1] & 127) === 120 &&
    packet.readUInt32BE(8) === 42
  ) {
    packetTimes.push(performance.now()); // Counts/timing only, never payloads.
  } else if (packet.length === 74 && packet.readUInt16BE(0) === 1) {
    const reply = Buffer.alloc(74);
    reply.writeUInt16BE(2);
    reply.writeUInt16BE(70, 2);
    packet.copy(reply, 4, 4, 8);
    reply.write("127.0.0.1", 8);
    reply.writeUInt16BE(remote.port, 72);
    udp.send(reply, remote.port, "127.0.0.1");
  } else if (packet.length <= 8) udp.send(packet, remote.port, "127.0.0.1");
});
app.setPath("userData", path.join(output, "profile"));
let finished = false;
function finish(result) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    path.join(output, "result.json"),
    // Keep the persisted schema metadata-only even if a future fixture starts
    // returning extra fields. Physical webcam pixels must never leave memory.
    JSON.stringify(
      {
        passed: result.passed === true,
        load_and_initialize_ms: result.load_and_initialize_ms,
        first_frame_ms: result.first_frame_ms,
        width: result.width,
        height: result.height,
        engine_heartbeat_count: result.engine_heartbeat_count,
        engine_max_gap_ms: result.engine_max_gap_ms,
        fake_audio_callbacks: result.fake_audio_callbacks,
        fake_audio_max_gap_ms: result.fake_audio_max_gap_ms,
        loopback_audio_packets: packetTimes.length,
        // Startup silence packets alone do not establish voice continuity.
        sustained_loopback_audio_verified:
          packetTimes.length > 100 && packetTimes.at(-1) - packetTimes[0] > 2000,
        loopback_audio_max_gap_ms:
          packetTimes.length > 1
            ? Math.max(...packetTimes.slice(1).map((t, i) => t - packetTimes[i]))
            : null,
        ...(result.error ? { error: String(result.error).slice(0, 500) } : {}),
        versions: process.versions,
      },
      null,
      2,
    ),
  );
  app.exit(result.passed ? 0 : 1);
}
ipcMain.on("camera-result", (_event, result) => finish(result));
app.whenReady().then(() => {
  udp.bind(0, "127.0.0.1", () => {
    process.env.DPE_CAMERA_UDP_PORT = String(udp.address().port);
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("file:") }),
    );
    const window = new BrowserWindow({
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        sandbox: false,
        contextIsolation: true,
      },
    });
    window.webContents.on("preload-error", (_event, _path, error) =>
      finish({ passed: false, error: String(error) }),
    );
    window.webContents.on("render-process-gone", (_event, details) =>
      finish({ passed: false, error: details.reason }),
    );
    window.loadFile(path.join(__dirname, "index.html"));
  });
});
setTimeout(() => finish({ passed: false, error: "Camera probe timed out" }), 30000);
