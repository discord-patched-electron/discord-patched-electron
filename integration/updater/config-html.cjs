// Adapt only the served copy; the pinned, checksum-verified source stays intact.
const focus =
  "    [ 'Focus', 'Which aspect of the client to focus on', 'cmdPreset', [ 'performance', 'battery life', 'balanced' ], () => config.cmdPreset ],";
module.exports = function configHtml(source) {
  const target = '<div id="container"></div>';
  if (source.split(focus).length !== 2 || source.split(target).length !== 2)
    throw new Error("Unsupported pinned OpenAsar settings layout");
  return source.replace(focus, "").replace(
    target,
    `
    <section aria-label="Launch settings" style="margin-bottom:20px;-webkit-app-region:no-drag">
      <button type="button" onclick="Native.flags()" style="min-height:40px;padding:8px 14px;cursor:pointer">Launch presets and flags</button>
      <p style="font-size:14px;line-height:1.5">Inspect and edit presets, video acceleration and experimental options.</p>
    </section>
    ${target}`,
  );
};
