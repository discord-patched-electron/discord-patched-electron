const { contextBridge } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const root = path.dirname(fs.realpathSync(path.join(process.env.DPE_APP_ROOT, "integration")));
const React = require(path.join(root, "node_modules/preact/compat"));
const icons = require(path.join(root, "integration/discord-ui/icons.cjs"))();
const Sources = require(path.join(root, "integration/discord-ui/capture-picker-view.cjs"))(
  React,
  (name) => icons[name],
);
contextBridge.exposeInMainWorld("captureFixture", {
  async render(props) {
    React.render(React.createElement(Sources, props), document.getElementById("fixture"));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  },
});
