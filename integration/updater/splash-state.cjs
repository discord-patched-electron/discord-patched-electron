// Translate backend events into OpenAsar's existing Splash.onState contract.
module.exports = function splashState() {
  let total = 1;
  const downloads = new Map();
  const installed = new Set();
  return (event) => {
    if (event.stage === "batch") {
      total = Math.max(1, event.total);
      downloads.clear();
      installed.clear();
      return { status: "checking-for-updates" };
    }
    if (event.stage === "download") {
      downloads.set(event.label, event.total > 0 ? Math.min(1, event.current / event.total) : 0);
      const complete = [...downloads.values()].filter((value) => value === 1).length;
      return {
        status: "downloading",
        current: Math.min(total, complete + 1),
        total,
        progress: (100 * [...downloads.values()].reduce((a, b) => a + b, 0)) / total,
      };
    }
    if (event.stage === "installed") installed.add(event.label);
    if (event.stage === "install" || event.stage === "installed")
      return {
        status: "installing",
        current: Math.min(total, installed.size + 1),
        total,
        progress: (100 * installed.size) / total,
      };
    if (event.stage === "ready") return { status: "starting" };
    if (event.stage === "error") return { status: "fail", seconds: 10 };
    return { status: "checking-for-updates" };
  };
};
