// Executed only by a temporary KWin script. No rules, shortcuts or config edits.
module.exports = function kwinPopout(request) {
  const window = request.id
    ? workspace
        .windowList()
        .find((w) => String(w.internalId) === request.id && w.pid === request.pid)
    : workspace.activeWindow;
  if (
    !window ||
    window.pid !== request.pid ||
    window.specialWindow ||
    window.fullScreen ||
    window.minimized ||
    window.tile ||
    window.maximizeMode
  )
    return null;
  const id = String(window.internalId);
  if (request.id && request.id !== id) return null;
  const geometry = window.frameGeometry;
  const copy = (r) => ({ x: r.x, y: r.y, width: r.width, height: r.height });
  const area = workspace.clientArea(KWin.MaximizeArea, window);
  if (typeof request.keepAbove === "boolean") window.keepAbove = request.keepAbove;
  if (request.target) {
    const before = request.before;
    if (
      !before ||
      ["x", "y", "width", "height"].some(
        (key) => !Number.isFinite(before[key]) || Math.abs(before[key] - geometry[key]) > 2,
      )
    )
      return null;
    const { x, y } = request.target;
    if (
      ![x, y].every(Number.isFinite) ||
      x < area.x ||
      y < area.y ||
      x + geometry.width > area.x + area.width ||
      y + geometry.height > area.y + area.height
    )
      return null;
    window.frameGeometry = { x, y, width: geometry.width, height: geometry.height };
  }
  return {
    id,
    pid: window.pid,
    title: window.caption,
    focused: window.active,
    keepAbove: Boolean(window.keepAbove),
    bounds: copy(window.frameGeometry),
    client: copy(window.clientGeometry),
    area: copy(area),
    output: copy(window.output.geometry),
    scale: window.output.scale,
    workspace: window.desktops.map((desktop) => String(desktop.id)).join(":"),
  };
};
