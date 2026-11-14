"""Run one bounded, temporary KWin script and return only its owned-window result."""

import json
import signal
import sys
import tempfile
import uuid
from pathlib import Path


def main():
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    source = sys.argv[1] if len(sys.argv) == 2 else ""
    if not source or len(source) > 65536:
        raise ValueError("Oversized KWin script")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(private=True)
    owner = bus.get_name_owner("org.kde.KWin")
    loop = GLib.MainLoop()
    result = []

    class Reply(dbus.service.Object):
        @dbus.service.method("org.discordpatched.Popout", in_signature="s", sender_keyword="sender")
        def report(self, value, sender=None):
            if sender != owner or result:
                return
            result.append(str(value))
            loop.quit()

    reply = Reply(bus, "/org/discordpatched/Popout")
    name = "dpe-popout-" + uuid.uuid4().hex
    api = dbus.Interface(bus.get_object(owner, "/Scripting"), "org.kde.kwin.Scripting")
    script_id = None

    def timeout(_signal, _frame):
        raise TimeoutError("KWin script timed out")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(4)
    try:
        with tempfile.TemporaryDirectory(prefix="dpe-kwin-") as directory:
            script = Path(directory) / "placement.js"
            script.write_text(
                f"const result=({source})();\n"
                f"callDBus({json.dumps(bus.get_unique_name())},"
                '"/org/discordpatched/Popout","org.discordpatched.Popout","report",'
                "JSON.stringify(result));\n"
            )
            script_id = int(api.loadScript(str(script), name, signature="ss", timeout=1))
            if script_id < 0:
                raise RuntimeError("KWin rejected the temporary script")
            script_api = dbus.Interface(
                bus.get_object(owner, f"/Scripting/Script{script_id}"), "org.kde.kwin.Script"
            )
            script_api.run(timeout=1)
            GLib.timeout_add(1500, lambda: (loop.quit(), False)[1])
            if not result:
                loop.run()
            if not result:
                raise TimeoutError("KWin did not return window geometry")
            print(result[0])
    finally:
        signal.alarm(0)
        if script_id is not None:
            try:
                api.unloadScript(name, timeout=1)
            except dbus.DBusException:
                pass
        reply.remove_from_connection()
        bus.close()


if __name__ == "__main__":
    try:
        main()
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
