// Test-only hooks in the isolated preload world; nothing is exposed to the page.
window.picker = require("../../integration/audio/audio-picker.cjs");
const attach = Element.prototype.attachShadow;
Element.prototype.attachShadow = function (options) {
  window.pickerRoot = attach.call(this, options);
  return window.pickerRoot;
};
