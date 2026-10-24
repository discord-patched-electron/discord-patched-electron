"""Private test-bus portal. Does not register keys on the real desktop."""
import dbus
import dbus.service
import dbus.mainloop.glib
import json
import os
from pathlib import Path
from gi.repository import GLib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
name = dbus.service.BusName('org.freedesktop.portal.Desktop', bus)
output = Path(os.environ['DPE_SHORTCUT_OUTPUT'])


class Request(dbus.service.Object):
    @dbus.service.signal('org.freedesktop.portal.Request', signature='ua{sv}')
    def Response(self, code, result):
        pass

    @dbus.service.method('org.freedesktop.portal.Request')
    def Close(self):
        pass


class Session(dbus.service.Object):
    @dbus.service.method('org.freedesktop.portal.Session')
    def Close(self):
        pass


class Portal(dbus.service.Object):
    objects = []
    evidence = {}

    def save(self):
        (output / 'portal.json').write_text(json.dumps(self.evidence, indent=2))

    @dbus.service.method('org.freedesktop.host.portal.Registry', in_signature='sa{sv}')
    def Register(self, app_id, options):
        self.evidence['app_id'] = str(app_id)
        self.save()

    @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        return dbus.UInt32(2)

    def request(self, sender, options, result):
        path = '/org/freedesktop/portal/desktop/request/' + sender[1:].replace('.', '_') + '/' + str(options['handle_token'])
        request = Request(bus, path)
        self.objects.append(request)
        GLib.timeout_add(50, lambda: request.Response(0, result))
        return path

    @dbus.service.method('org.freedesktop.portal.GlobalShortcuts', in_signature='a{sv}', out_signature='o', sender_keyword='sender')
    def CreateSession(self, options, sender=None):
        session = '/org/freedesktop/portal/desktop/session/' + sender[1:].replace('.', '_') + '/' + str(options['session_handle_token'])
        self.objects.append(Session(bus, session))
        return self.request(sender, options, {'session_handle': session})

    @dbus.service.method('org.freedesktop.portal.GlobalShortcuts', in_signature='oa(sa{sv})sa{sv}', out_signature='o', sender_keyword='sender')
    def BindShortcuts(self, session, shortcuts, parent, options, sender=None):
        self.evidence['shortcuts'] = [[str(key), {str(k): str(v) for k,v in props.items()}] for key,props in shortcuts]
        self.save()
        key = shortcuts[0][0]
        GLib.timeout_add(300, lambda: self.Activated(session,key,dbus.UInt64(100),{}))
        GLib.timeout_add(500, lambda: self.Deactivated(session,key,dbus.UInt64(300),{}))
        return self.request(sender, options, {'shortcuts': shortcuts})

    @dbus.service.method('org.freedesktop.portal.GlobalShortcuts', in_signature='oa{sv}', out_signature='o', sender_keyword='sender')
    def ListShortcuts(self, session, options, sender=None):
        return self.request(sender, options, {'shortcuts': dbus.Array([], signature='(sa{sv})')})

    @dbus.service.signal('org.freedesktop.portal.GlobalShortcuts', signature='osta{sv}')
    def Activated(self, session, shortcut, timestamp, options):
        pass

    @dbus.service.signal('org.freedesktop.portal.GlobalShortcuts', signature='osta{sv}')
    def Deactivated(self, session, shortcut, timestamp, options):
        pass


portal = Portal(bus, '/org/freedesktop/portal/desktop')
(output / 'portal-ready').touch()
GLib.MainLoop().run()
