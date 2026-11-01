"""Correlate portal selections with a single linked video source and live app.

No focus/title guessing, arbitrary PID properties, or remembered audio choices.
App IDs must resolve to one process family. Unknown providers stay manual.
"""


def app_id(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    return value.removesuffix(".desktop")


def application_pid(identifier, windows, entries, identity, descendant, audio_pid):
    identifier = app_id(identifier)
    if not identifier:
        return None
    # Compositor metadata identifies the window owner even before it plays sound.
    pids = {w["pid"] for w in windows if app_id(w.get("app_id")) == identifier}
    # Audio metadata can reveal another running instance absent from the window
    # inventory. A matching ID is not sufficient when process families disagree.
    pids.update(
        {
            audio_pid(p)
            for p in entries
            if identifier
            in {
                app_id(p.get("application.id")),
                app_id(p.get("pipewire.access.portal.app_id")),
                app_id(p.get("application.process.binary")),
            }
        }
    )
    identities = [identity(pid) for pid in pids]
    if not identities or any(item is None for item in identities):
        return None
    roots = [root for root in identities if all(descendant(pid, root) for pid in pids)]
    return roots[0][0] if len(roots) == 1 else None


def target(objects, source, observations, windows, entries, identity, descendant, audio_pid):
    node_id, serial = source
    node = next(
        (
            n
            for n in objects
            if n.get("type") == "PipeWire:Interface:Node" and n.get("id") == node_id
        ),
        None,
    )
    if not node or not serial:
        return None
    p = node.get("info", {}).get("props", {})
    if str(p.get("object.serial", "")) != serial:
        return None
    matches = [o for o in observations if o.get("node") == node_id]
    if len(matches) > 1:
        return None
    if len(matches) == 1:
        observed = matches[0]
        if observed.get("serial") not in (None, serial):
            return None
        key = ("portal", node_id, serial, observed.get("token"))
        if observed.get("kind") == "system":
            return {"kind": "system", "key": key, "backend": "portal-monitor"}
        if observed.get("backend") == "mutter-window":
            pids = {
                w["pid"]
                for w in windows
                if w.get("backend") == "gnome-window-calls"
                and type(w.get("id")) is int
                and w["id"] == observed.get("windowId")
            }
            if len(pids) > 1:
                return None
            if pids:
                pid = next(iter(pids))
                if identity(pid) is None:
                    return None
                return {"kind": "application", "pid": pid, "key": key, "backend": "mutter-window"}
            # A portal app ID can still resolve through audio metadata when
            # Window Calls is absent; never substitute a focused window.
        identifier = observed.get("appId")
        backend = observed.get("backend", "portal")
    else:
        # KWin names window streams after desktopFileName. Require a matching
        # compositor window, so output/region names cannot act as app IDs.
        name = p.get("media.name", "")
        if not isinstance(name, str) or not name.startswith("kwin-screencast-"):
            return None
        identifier = name.removeprefix("kwin-screencast-")
        windows = [w for w in windows if w.get("backend") == "kwin"]
        if not any(app_id(w.get("app_id")) == app_id(identifier) for w in windows):
            return None
        backend = "kwin-stream"
        key = (backend, node_id, serial, identifier)
    pid = application_pid(identifier, windows, entries, identity, descendant, audio_pid)
    if pid is None:
        return None
    return {"kind": "application", "pid": pid, "key": key, "backend": backend}
