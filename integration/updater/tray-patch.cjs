// Two narrow main-process hooks retain Discord's tray lifecycle, menu and states.
// No renderer code or upstream assets are changed.
function patch(source, adapter) {
  const getIcon = "function getTrayIcon(){";
  const tooltip = ").setToolTip(currentStrings.appName),setContextMenu()";
  for (const site of [getIcon, tooltip]) {
    if (source.split(site).length !== 2)
      throw new Error(`Unsupported Discord client layout: ${site}`);
  }
  const load = `require(${JSON.stringify(adapter)})`;
  return source
    .replace(
      getIcon,
      `function getTrayIcon(){return ${load}.image(getOriginalTrayIcon(),getTrayStates())}function getOriginalTrayIcon(){`,
    )
    .replace(
      tooltip,
      `).setToolTip(currentStrings.appName),${load}.register(atomTray,getTrayIcon,trayIcons),setContextMenu()`,
    );
}
module.exports = { patch };
