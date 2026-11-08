"""Private test portal exposing only this fixture's generated video node."""

import json
import os
import socket
from pathlib import Path

import dbus
import dbus.lowlevel
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

root = Path(os.environ["DPE_SCREENSHARE_OUTPUT"])
assert os.environ["PIPEWIRE_REMOTE"] == "dpe-capture-test"
assert {name for _, name in socket.if_nameindex()} == {"lo"}
config = json.loads((root / "config.json").read_text())
node = next(
    int(line) for line in (root / "source.log").read_text().splitlines() if line.isdecimal()
)
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName("org.freedesktop.portal.Desktop", bus)


class Request(dbus.service.Object):
    @dbus.service.signal("org.freedesktop.portal.Request", signature="ua{sv}")
    def Response(self, code, results):
        pass

    @dbus.service.method("org.freedesktop.portal.Request", in_signature="", out_signature="")
    def Close(self):
        pass


class Session(dbus.service.Object):
    @dbus.service.method("org.freedesktop.portal.Session", in_signature="", out_signature="")
    def Close(self):
        self.Closed()

    @dbus.service.signal("org.freedesktop.portal.Session", signature="")
    def Closed(self):
        pass


class Portal(dbus.service.Object):
    objects = []

    def request(self, sender, options, response):
        path = (
            "/org/freedesktop/portal/desktop/request/"
            + sender[1:].replace(".", "_")
            + "/"
            + str(options["handle_token"])
        )
        request = Request(bus, path)
        self.objects.append(request)

        def respond():
            # Real portal responses are directed to the requesting connection.
            # Clients need not install a broadcast signal match to receive them.
            message = dbus.lowlevel.SignalMessage(
                path, "org.freedesktop.portal.Request", "Response"
            )
            message.set_destination(sender)
            message.append(
                dbus.UInt32(0), dbus.Dictionary(response, signature="sv"), signature="ua{sv}"
            )
            bus.send_message(message)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(20, respond)
        return path

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return {
            "version": dbus.UInt32(4),
            "AvailableSourceTypes": dbus.UInt32(3),
            "AvailableCursorModes": dbus.UInt32(3),
        }

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self.GetAll(interface)[prop]

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="a{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def CreateSession(self, options, sender=None):
        path = (
            "/org/freedesktop/portal/desktop/session/"
            + sender[1:].replace(".", "_")
            + "/"
            + str(options["session_handle_token"])
        )
        self.objects.append(Session(bus, path))
        # ScreenCast keeps this object-path value typed as a string for API
        # compatibility; subsequent method arguments use object-path type.
        return self.request(sender, options, {"session_handle": dbus.String(path)})

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="oa{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def SelectSources(self, session, options, sender=None):
        return self.request(sender, options, {})

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast",
        in_signature="osa{sv}",
        out_signature="o",
        sender_keyword="sender",
    )
    def Start(self, session, parent, options, sender=None):
        return self.request(
            sender,
            options,
            {
                "streams": dbus.Array(
                    [
                        dbus.Struct(
                            (
                                dbus.UInt32(node),
                                dbus.Dictionary(
                                    {
                                        "source_type": dbus.UInt32(
                                            2 if config["source"] == "window" else 1
                                        ),
                                        "size": dbus.Struct(
                                            (
                                                dbus.Int32(config["width"]),
                                                dbus.Int32(config["height"]),
                                            ),
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

    @dbus.service.method(
        "org.freedesktop.portal.ScreenCast", in_signature="oa{sv}", out_signature="h"
    )
    def OpenPipeWireRemote(self, session, options):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(Path(os.environ["XDG_RUNTIME_DIR"]) / "dpe-capture-test"))
            return dbus.types.UnixFd(connection.fileno())


portal = Portal(bus, "/org/freedesktop/portal/desktop")
(root / "portal-ready").touch()
GLib.MainLoop().run()
