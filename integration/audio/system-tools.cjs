const path = require("node:path");

// Native installs retain their exact system executable paths. AppImage supplies
// its helper directory so downloaded application code uses the bundled tools.
function systemTool(name) {
  return path.join(process.env.DPE_TOOLS_DIR || "/usr/bin", name);
}
module.exports = { systemTool };
