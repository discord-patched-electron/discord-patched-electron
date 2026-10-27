// Adapt only the served copy; the pinned, checksum-verified source stays intact.
const fs = require("node:fs");
const path = require("node:path");
const focus =
  "    [ 'Focus', 'Which aspect of the client to focus on', 'cmdPreset', [ 'performance', 'battery life', 'balanced' ], () => config.cmdPreset ],";
module.exports = function configHtml(source) {
  const target = '<div id="container"></div>';
  if (source.split(focus).length !== 2 || source.split(target).length !== 2)
    throw new Error("Unsupported pinned OpenAsar settings layout");
  return source
    .replace(focus, "")
    .replace(
      /<header>[\s\S]*?<\/header>/,
      `
      <header>
        <h1>OpenAsar Config</h1>
        <nav role="tablist" aria-label="OpenAsar">
          <button type="button" id="t_settings" role="tab" aria-selected="true" aria-controls="container" class="active">Settings</button>
          <button type="button" id="t_theming" role="tab" aria-selected="false" aria-controls="container" tabindex="-1">Theming</button>
          <button type="button" id="t_flags" role="tab" aria-selected="false" aria-controls="flags-panel" tabindex="-1">Flags</button>
        </nav>
      </header>`,
    )
    .replace(
      target,
      `<main>
      <div id="container" role="tabpanel" aria-labelledby="t_settings"></div>
      ${fs.readFileSync(path.join(__dirname, "launch-settings.html"), "utf8")}
      </main>`,
    ).concat(`
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="launch-settings.css">
      <script src="flag-editor.js" defer></script>
      <script src="launch-settings.js" defer></script>
      <script src="config-tabs.js" defer></script>`);
};
