"""Exercise the private portal handshake without a broadcast Response match."""

import socket
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
responses = {}


def message_received(_connection, message):
    if message.is_signal("org.freedesktop.portal.Request", "Response"):
        assert message.get_destination() == bus.get_unique_name()
        responses[message.get_path()] = message.get_args_list()


bus.add_message_filter(message_received)
api = dbus.Interface(
    bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"),
    "org.freedesktop.portal.ScreenCast",
)
context = GLib.MainContext.default()


def response(path):
    until = time.monotonic() + 2
    while path not in responses:
        while context.pending():
            context.iteration(False)
        if time.monotonic() > until:
            raise RuntimeError("Directed portal response timed out")
        time.sleep(0.005)
    code, value = responses.pop(path)
    assert code == 0
    return value


created = response(api.CreateSession({"handle_token": "create", "session_handle_token": "test"}))
session = created["session_handle"]
assert isinstance(session, dbus.String), type(session)
response(
    api.SelectSources(dbus.ObjectPath(session), {"handle_token": "select", "types": dbus.UInt32(1)})
)
started = response(api.Start(dbus.ObjectPath(session), "", {"handle_token": "start"}))
assert started["streams"][0][0] == 123
remote = api.OpenPipeWireRemote(dbus.ObjectPath(session), {})
with socket.socket(fileno=remote.take()) as stream:
    assert stream.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM
print("directed portal handshake passed")
