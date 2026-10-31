// Keep the pinned OpenAsar source verified and untouched. DPE owns its local UI.
const fs = require("node:fs");
const path = require("node:path");
module.exports = function configHtml(source) {
  if (!source.includes('<div id="container"></div>'))
    throw new Error("Unsupported pinned OpenAsar settings layout");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DPE Settings</title>
    <style>${fs.readFileSync(path.join(__dirname, "../discord-ui/components.css"), "utf8")}</style>
    <link rel="stylesheet" href="launch-settings.css"><script src="config-app.js" defer></script>
    </head><body class="dpe-ui"><div id="app"></div></body></html>`;
};
