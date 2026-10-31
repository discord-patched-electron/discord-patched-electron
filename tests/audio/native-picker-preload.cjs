// Legacy native-component contract harness. Production uses the shared custom
// picker; keep this historical probe independent of its public show() path.
const picker = require("../../integration/audio/native-picker-bridge.cjs");
const { webFrame } = require("electron");
const { createResolver } = require("../../integration/audio/app-icons.cjs");
window.audioTest = {
  replies: [],
  open(options) {
    this.close = picker.show(
      options || [
        { id: "system", label: "System audio", detail: "All applications except Discord" },
        { id: "200", label: "Zen Browser", detail: "Music", iconNames: ["zen-bin"] },
        { id: "300", label: "mpv", detail: "Video playback", iconNames: ["mpv"] },
      ],
      (value) => this.replies.push(value),
      {
        execute: (code) => webFrame.executeJavaScript(code),
        resolveIcon: createResolver(),
        fallback: () => {
          this.replies.push("fallback");
          return () => {};
        },
      },
    );
  },
};
