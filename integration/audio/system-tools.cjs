const path = require("node:path");

// Only Python belongs to the AppImage bootstrap. Audio and desktop tools must
// use the host libraries, plugins and configuration.
function systemTool(name) {
  return path.join((name === "python3" && process.env.DPE_TOOLS_DIR) || "/usr/bin", name);
}
module.exports = { systemTool };
