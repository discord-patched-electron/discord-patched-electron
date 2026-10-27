const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const install = require("../../integration/updater/desktop.cjs");

test("desktop identity is set before startup and icons cover every Electron window", () => {
  const app = new EventEmitter();
  let desktopName;
  app.setDesktopName = (name) => (desktopName = name);
  let started = false;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/entry.cjs"), "utf8"),
    {
      process: {
        env: {
          DPE_APP_ROOT: "/portable app",
          CHROME_DESKTOP: "discord-canary.desktop",
          DPE_UPDATER_UI: "1",
        },
      },
      require(name) {
        if (name === "electron") return { app };
        if (name === "./patches.cjs") return {};
        if (name === "./desktop.cjs") return install;
        if (name === "./startup.cjs") {
          assert.equal(desktopName, "discord-canary.desktop");
          for (const kind of ["updater", "retry", "splash", "settings", "client", "popout"]) {
            let icon;
            app.emit("browser-window-created", {}, { setIcon: (value) => (icon = value) });
            assert.equal(icon, "/portable app/discord.png", kind);
          }
          started = true;
          return;
        }
        return require(name);
      },
    },
  );
  assert.equal(started, true);
});
