// One bar covers download and installation work. Parallel workers and late
// callbacks must never move a completed task backwards.
module.exports = function splashState() {
  let total = 1;
  const downloads = new Map();
  const installs = new Map();
  function snapshot(tasks) {
    const finished = [...tasks.values()].filter((value) => value === 1).length;
    return {
      total,
      current: tasks.size ? Math.min(total, finished + 1) : 0,
      started: tasks.size > 0,
      complete: finished >= total,
      progress: Math.min(100, (100 * [...tasks.values()].reduce((a, b) => a + b, 0)) / total),
    };
  }
  return (event) => {
    if (event.stage === "batch" || event.stage === "error") {
      total = Number.isFinite(event.total) ? Math.max(1, event.total) : 1;
      downloads.clear();
      installs.clear();
    }
    if (event.stage === "download") {
      const value =
        event.total > 0 && Number.isFinite(event.current)
          ? Math.max(0, Math.min(1, event.current / event.total))
          : 0;
      downloads.set(event.label, Math.max(downloads.get(event.label) || 0, value));
    }
    if (event.stage === "install" || event.stage === "installed") {
      downloads.set(event.label, 1);
      installs.set(
        event.label,
        Math.max(installs.get(event.label) || 0, event.stage === "installed" ? 1 : 0),
      );
    }
    if (["download", "install", "installed"].includes(event.stage)) {
      const downloading = snapshot(downloads);
      const installing = snapshot(installs);
      const status = downloading.complete ? "installing" : "downloading";
      return {
        status,
        ...(status === "installing" ? installing : downloading),
        label: event.label,
        progress: (downloading.progress + installing.progress) / 2,
      };
    }
    if (event.stage === "ready") return { status: "starting" };
    if (event.stage === "error") return { status: "fail", seconds: 10 };
    return { status: "checking-for-updates" };
  };
};
