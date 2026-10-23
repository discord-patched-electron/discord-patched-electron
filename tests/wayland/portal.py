import dbus, dbus.service, dbus.mainloop.glib, os, json, subprocess, socket
from gi.repository import GLib
from pathlib import Path

# Test-only portal: permits ONLY the private compositor's synthetic fixture windows.
O = Path(os.environ["DPE_AUDIO_OUTPUT"])
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("org.freedesktop.portal.Desktop", bus)


class Request(dbus.service.Object):
    @dbus.service.signal("org.freedesktop.portal.Request", signature="ua{sv}")
    def Response(self, code, results):
        pass

    @dbus.service.method(
        "org.freedesktop.portal.Request", in_signature="", out_signature=""
    )
    def Close(self):
        pass


class Session(dbus.service.Object):
    @dbus.service.method(
        "org.freedesktop.portal.Session", in_signature="", out_signature=""
    )
    def Close(self):
        if hasattr(self, "native"):
            dbus.Interface(
                bus.get_object("org.gnome.Mutter.ScreenCast", self.native),
                "org.gnome.Mutter.ScreenCast.Session",
            ).Stop()

    @dbus.service.signal("org.freedesktop.portal.Session", signature="")
    def Closed(self):
        pass


class Portal(dbus.service.Object):
    sessions = {}
    requests = []

    def req(self, sender, opts):
        path = (
            "/org/freedesktop/portal/desktop/request/"
            + sender[1:].replace(".", "_")
            + "/"
            + str(opts["handle_token"])
        )
        r = Request(bus, path)
        self.requests.append(r)
        return path, r

    @dbus.service.method(
        "org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface):
        return {
            "version": dbus.UInt32(4),
            "AvailableSourceTypes": dbus.UInt32(3),
            "AvailableCursorModes": dbus.UInt32(3),
        }

    @dbus.service.method(
        "org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v"
    )
    def Get(self, interface, prop):
        return self.GetAll(interface)[prop]

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="a{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def CreateSession(self, opts, sender=None):
        print("create", flush=True)
        path, r = self.req(sender, opts)
        session = (
            "/org/freedesktop/portal/desktop/session/"
            + sender[1:].replace(".", "_")
            + "/"
            + str(opts["session_handle_token"])
        )
        self.sessions[session] = Session(bus, session)
        GLib.timeout_add(50, lambda: r.Response(0, {"session_handle": session}))
        return path

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="oa{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def SelectSources(self, session, opts, sender=None):
        print("select", dict(opts), flush=True)
        path, r = self.req(sender, opts)
        GLib.timeout_add(50, lambda: r.Response(0, {}))
        return path

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="osa{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def Start(self, session, parent, opts, sender=None):
        print("start", flush=True)
        path, r = self.req(sender, opts)
        choice = json.loads((O / "selection.json").read_text())
        if choice["kind"] == "cancel":
            GLib.timeout_add(50, lambda: r.Response(1, {}))
            return path
        window = None
        if choice["kind"] == "window":
            allowed = json.loads((O / "inputs.json").read_text())
            assert choice["pid"] in [item["pid"] for item in allowed.values()]
            windows = json.loads(
                subprocess.check_output(["niri", "msg", "-j", "windows"])
            )
            window = next(w for w in windows if w["pid"] == choice["pid"])
        interface = dbus.Interface(
            bus.get_object(
                "org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast"
            ),
            "org.gnome.Mutter.ScreenCast",
        )
        native = interface.CreateSession(dbus.Dictionary({}, signature="sv"))
        self.sessions[str(session)].native = native
        sess = dbus.Interface(
            bus.get_object("org.gnome.Mutter.ScreenCast", native),
            "org.gnome.Mutter.ScreenCast.Session",
        )
        stream = (
            sess.RecordWindow(
                dbus.Dictionary(
                    {
                        "window-id": dbus.UInt64(window["id"]),
                        "cursor-mode": dbus.UInt32(0),
                    },
                    signature="sv",
                )
            )
            if window
            else sess.RecordMonitor(
                "winit",
                dbus.Dictionary({"cursor-mode": dbus.UInt32(0)}, signature="sv"),
            )
        )

        def started(node):
            print("stream", node, flush=True)
            r.Response(
                0,
                {
                    "streams": dbus.Array(
                        [
                            dbus.Struct(
                                (
                                    dbus.UInt32(node),
                                    dbus.Dictionary(
                                        {
                                            "source_type": dbus.UInt32(
                                                2 if window else 1
                                            ),
                                            "size": dbus.Struct(
                                                (dbus.Int32(240), dbus.Int32(160)),
                                                signature="ii",
                                            ),
                                        },
                                        signature="sv",
                                    ),
                                ),
                                signature="ua{sv}",
                            )
                        ],
                        signature="(ua{sv})",
                    )
                },
            )

        bus.add_signal_receiver(
            started,
            signal_name="PipeWireStreamAdded",
            dbus_interface="org.gnome.Mutter.ScreenCast.Stream",
            path=str(stream),
        )
        GLib.timeout_add(100, lambda: sess.Start())
        return path

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast", in_signature="oa{sv}", out_signature="h"
    )
    def OpenPipeWireRemote(self, session, opts):
        print("remote", flush=True)
        s = socket.socket(socket.AF_UNIX)
        s.connect(os.environ["XDG_RUNTIME_DIR"] + "/" + os.environ["PIPEWIRE_REMOTE"])
        fd = dbus.types.UnixFd(s.fileno())
        s.close()
        return fd


portal = Portal(bus, "/org/freedesktop/portal/desktop")
print("ready", flush=True)
GLib.MainLoop().run()
