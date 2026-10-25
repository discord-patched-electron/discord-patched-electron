// Preserve OpenAsar's cache contract while accepting Discord's current tokens.
let lastTheme = "";
// Concatenated with the pinned mainWindow.js, which schedules this collector.
// eslint-disable-next-line no-unused-vars
const themesync = async () => {
  const aliases = {
    "--background-primary": ["--background-primary", "--background-base-lowest"],
    "--background-secondary": ["--background-secondary", "--background-base-lower"],
    "--brand-experiment": ["--brand-experiment", "--brand-500"],
    "--header-primary": ["--header-primary", "--text-default"],
    "--text-muted": ["--text-muted"],
  };
  const read = (names) => {
    for (let element = document.body; element; element = element.parentElement) {
      const style = getComputedStyle(element);
      for (const name of names) {
        const value = style.getPropertyValue(name).trim();
        if (value) return value;
      }
    }
    return "";
  };
  const values = Object.entries(aliases).map(([name, names]) => [name, read(names)]);
  if (!values[0][1]) return;
  const css = `body { ${values
    .filter(([, value]) => value)
    .map(([name, value]) => `${name}: ${value};`)
    .join(" ")} }`;
  if (css === lastTheme) return;
  const cached = (await DiscordNative.userDataCache.getCached()) || {};
  if (cached.openasarSplashCSS !== css) {
    cached.openasarSplashCSS = css;
    await DiscordNative.userDataCache.cacheUserData(JSON.stringify(cached));
  }
  lastTheme = css;
};
