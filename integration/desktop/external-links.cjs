// Same-window links never reach setWindowOpenHandler. Keep Discord loaded while
// handing ordinary web links to the same native opener used by new-window links.
let sequence = 0;
async function openExternal(
  address,
  open = (url) => require("electron").shell.openExternal(url),
  record = require("../updater/diagnostic-log.cjs").record,
) {
  const request = ++sequence;
  const start = Date.now();
  record("external-link", { request, phase: "requested" });
  try {
    await open(address);
    // On Linux this acknowledges launching xdg-open, not loading a browser tab.
    record("external-link", { request, phase: "submitted", elapsedMs: Date.now() - start });
  } catch (error) {
    record("external-link", { request, phase: "failed", elapsedMs: Date.now() - start });
    throw error;
  }
}

function navigate(event, address, contents, endpoint, open) {
  event.preventDefault();
  if (!URL.canParse(address) || !URL.canParse(contents.getURL()) || !URL.canParse(endpoint)) return;
  try {
    const source = new URL(contents.getURL());
    const expected = new URL(endpoint);
    const target = new URL(address);
    if (
      source.origin !== expected.origin ||
      source.protocol !== "https:" ||
      !["discord.com", "canary.discord.com", "ptb.discord.com"].includes(source.hostname) ||
      !["https:", "http:"].includes(target.protocol)
    )
      return;
    if (require("./spellcheck.cjs").openLanguages(address, contents)) return;
    // Never log addresses: they can contain private channel IDs or credentials.
    Promise.resolve(open(address)).catch(() => console.warn("Could not open an external link."));
  } catch {
    console.warn("Could not open an external link.");
  }
}

function patchDesktop(source, adapter, replace) {
  source = replace(
    source,
    "electron_1.shell.openExternal(externalUrl)",
    `require(${JSON.stringify(adapter)}).openExternal(externalUrl)`,
  );
  for (const [contents, endpoint] of [
    ["mainWindow.webContents", "exports.WEBAPP_ENDPOINT"],
    ["popoutWindow.webContents", "webappEndpoint"],
  ]) {
    const before = `${contents}.on("will-navigate",(evt,url)=>{(0,securityUtils_1.checkUrlOriginMatches)(url,${endpoint})||evt.preventDefault()})`;
    const after = `${contents}.on("will-navigate",(evt,url)=>{(0,securityUtils_1.checkUrlOriginMatches)(url,${endpoint})||require(${JSON.stringify(adapter)}).navigate(evt,url,${contents},${endpoint},securityUtils_1.saferShellOpenExternal)})`;
    source = replace(source, before, after);
  }
  return source;
}

module.exports = { navigate, patchDesktop, openExternal };
