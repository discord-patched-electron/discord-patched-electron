const picker = require("../../integration/audio/audio-picker.cjs");
window.audioTest = {
  replies: [],
  open(options) {
    this.close = picker.show(
      options || [
        { id: "system", label: "System audio", detail: "All applications except Discord" },
        { id: "200", label: "Helium", detail: "Music", iconNames: ["helium-browser"] },
        { id: "300", label: "mpv", detail: "Video playback", iconNames: ["mpv"] },
      ],
      (value) => this.replies.push(value),
    );
  },
};
