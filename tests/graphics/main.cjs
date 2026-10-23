const {app, BrowserWindow, session} = require("electron");
const fs = require("node:fs"), path = require("node:path");
const output = process.env.DPE_GRAPHICS_OUTPUT;
if (!output) throw new Error("A private output directory is required");
app.setPath("userData", path.join(output, "profile"));
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
app.commandLine.appendSwitch("disable-background-networking");
const players = {};
let finished = false;
let stage = "startup";
function finish(report) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(path.join(output, "graphics.json"), JSON.stringify({versions:process.versions, stage, players, ...report}, null, 2));
  app.exit(report.passed ? 0 : 1);
}
app.whenReady().then(async () => {
  stage = "window";
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => callback({cancel:!details.url.startsWith("file:")}));
  const window = new BrowserWindow({show:true, width:640, height:480, title:"Discord graphics check", webPreferences:{sandbox:true, contextIsolation:true, backgroundThrottling:false}});
  window.webContents.on("render-process-gone", (_event, detail) => finish({passed:false, error:detail.reason}));
  await window.loadFile(path.join(__dirname,"index.html"));
  stage = "media-debugger";
  window.webContents.debugger.attach("1.3");
  window.webContents.debugger.on("message", (_event, method, params) => {
    if (method === "Media.playerPropertiesChanged") {
      const properties = players[params.playerId] ??= {};
      for (const {name, value} of params.properties) properties[name] = value;
    }
  });
  await window.webContents.debugger.sendCommand("Media.enable");
  stage = "renderer";
  const rendering = await window.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname,"renderer.js"),"utf8"));
  stage = "gpu-info";
  const gpu = await app.getGPUInfo("complete");
  stage = "complete";
  finish({passed: rendering.webglPassed && rendering.videoFrames >= 5, rendering,
    gpuFeatures:app.getGPUFeatureStatus(), gpu, ozoneSwitch:app.commandLine.getSwitchValue("ozone-platform"),
    hardwareAccelerationEnabled:app.isHardwareAccelerationEnabled(),
    note:"Synthetic video and WebGL on the local GPU; no real Discord call."});
}).catch(error => finish({passed:false,error:error.stack}));
setTimeout(() => finish({passed:false,error:"Graphics probe timed out"}),30000);
