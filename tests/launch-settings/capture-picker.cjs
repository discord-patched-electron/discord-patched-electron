const { BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

module.exports = async function check({ root, output }) {
  const window = new BrowserWindow({
    width: 700,
    height: 480,
    show: true,
    webPreferences: {
      preload: path.join(root, "tests/launch-settings/capture-picker-preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  try {
    await window.loadURL("data:text/html,<body class='dpe-ui'><main id='fixture'></main></body>");
    await window.webContents.insertCSS(
      fs.readFileSync(path.join(root, "integration/discord-ui/components.css"), "utf8") +
        "body{margin:0;padding:24px;box-sizing:border-box;background:var(--dpe-bg);font:16px sans-serif}",
    );
    const metrics = [];
    for (const light of [false, true]) {
      await window.webContents
        .executeJavaScript(`document.body.style.setProperty('--background-base-lowest', '${light ? "#fff" : "#121214"}');
        document.body.style.setProperty('--text-strong', '${light ? "#1a1a1e" : "#dcdce0"}');
        document.body.style.setProperty('--text-subtle', '${light ? "#53545b" : "#96979e"}');`);
      for (const [width, zoom] of [
        [700, 1],
        [360, 1.5],
      ]) {
        window.setSize(width, 480);
        window.webContents.setZoomFactor(zoom);
        for (const [kind, props] of [
          ["loading", { loading: true }],
          [
            "error",
            { error: "Capture selection is unavailable. Try Vanilla or reopen the picker." },
          ],
          ["empty", {}],
        ]) {
          await window.webContents.executeJavaScript(
            `captureFixture.render(${JSON.stringify(props)})`,
          );
          const result = await window.webContents.executeJavaScript(`(() => {
            const state=document.querySelector('[data-dpe-capture-state]');
            const icon=state.querySelector('[data-dpe-icon]');
            const heading=state.querySelector('h3');
            const rect=state.getBoundingClientRect();
            const center=rect.left+rect.width/2;
            return {kind:state.dataset.dpeCaptureState,role:state.getAttribute('role'),
              icon:icon.dataset.dpeIcon,svg:!!icon.querySelector('svg'),
              centered:[icon,heading,...state.querySelectorAll('p')].every(element => {
                const r=element.getBoundingClientRect();return Math.abs(r.left+r.width/2-center)<1;
              }),overflow:document.documentElement.scrollWidth>innerWidth,
              headingColor:getComputedStyle(heading).color};
          })()`);
          assert.equal(result.kind, kind);
          assert.equal(result.role, kind === "error" ? "alert" : "status");
          assert.equal(result.icon, kind === "error" ? "warning" : "screen");
          assert.equal(result.svg, true);
          assert.equal(result.centered, true);
          assert.equal(result.overflow, false);
          metrics.push({ light, width, zoom, ...result });
          if (!light && width === 700)
            fs.writeFileSync(
              path.join(output, `capture-${kind}.png`),
              (await window.webContents.capturePage()).toPNG(),
            );
        }
      }
    }
    fs.writeFileSync(path.join(output, "capture-states.json"), JSON.stringify(metrics, null, 2));
  } finally {
    window.destroy();
  }
};
