"""Read server-verified X11 window owners, without titles or _NET_WM_PID.

Run the XCB probe in a bounded child: an unresponsive X server must not stall
audio routing. XRes 1.2 supplies host PIDs even for sandboxed X11 clients.
Only niri's xwayland-satellite class mapping is currently supported.
"""

import ctypes as C
import json
import os
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from functools import partial
from pathlib import Path

MAX_WINDOWS = 4096
LOCAL_DISPLAY = re.compile(r"(?:unix)?:\d+(?:\.\d+)?\Z")


def child_guard(parent):
    # The routing helper can be killed during a blocked XCB call. Do not leave
    # an orphan behind when its normal subprocess timeout can no longer run.
    if C.CDLL(None).prctl(1, signal.SIGKILL) != 0 or os.getppid() != parent:
        os._exit(1)


class Cookie(C.Structure):
    _fields_ = [("sequence", C.c_uint)]


class Iterator(C.Structure):
    _fields_ = [("data", C.c_void_p), ("rem", C.c_int), ("index", C.c_int)]


class Version(C.Structure):
    _fields_ = [
        ("response", C.c_uint8),
        ("pad", C.c_uint8),
        ("sequence", C.c_uint16),
        ("length", C.c_uint32),
        ("major", C.c_uint16),
        ("minor", C.c_uint16),
    ]


class Property(C.Structure):
    _fields_ = [
        ("response", C.c_uint8),
        ("format", C.c_uint8),
        ("sequence", C.c_uint16),
        ("length", C.c_uint32),
        ("type", C.c_uint32),
        ("bytes_after", C.c_uint32),
        ("value_len", C.c_uint32),
    ]


class Spec(C.Structure):
    _fields_ = [("client", C.c_uint32), ("mask", C.c_uint32)]


class IdReply(C.Structure):
    _fields_ = [
        ("response", C.c_uint8),
        ("pad", C.c_uint8),
        ("sequence", C.c_uint16),
        ("length", C.c_uint32),
        ("value", C.c_uint32),
    ]


def bind(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
    return fn


def stamp(pid):
    """Snapshot identity while the X connection/resource still exists."""
    path = Path(f"/proc/{pid}")
    if pid <= 1 or path.stat().st_uid != os.getuid():
        raise ValueError("Non-local X11 process")
    fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] == "Z":
        raise ValueError("Exited X11 process")
    return [pid, fields[19]]


class XConnection:
    def __init__(self, display):
        if not LOCAL_DISPLAY.fullmatch(display):
            raise ValueError("Only local X11 displays are supported")
        self.x = C.CDLL("libxcb.so.1")
        self.res = C.CDLL("libxcb-res.so.0")
        self.free = bind(C.CDLL(None), "free", None, C.c_void_p)
        ptr, u32, u8 = C.c_void_p, C.c_uint32, C.c_uint8
        for name, result, args in (
            ("connect", ptr, (C.c_char_p, C.POINTER(C.c_int))),
            ("disconnect", None, (ptr,)),
            ("connection_has_error", C.c_int, (ptr,)),
            ("get_setup", ptr, (ptr,)),
            ("setup_roots_iterator", Iterator, (ptr,)),
            ("screen_next", None, (C.POINTER(Iterator),)),
            ("query_tree", Cookie, (ptr, u32)),
            ("query_tree_children_length", C.c_int, (ptr,)),
            ("query_tree_children", C.POINTER(u32), (ptr,)),
            ("get_property", Cookie, (ptr, u8, u32, u32, u32, u32, u32)),
            ("get_property_value_length", C.c_int, (ptr,)),
            ("get_property_value", ptr, (ptr,)),
            ("intern_atom", Cookie, (ptr, u8, C.c_uint16, C.c_char_p)),
            ("get_selection_owner", Cookie, (ptr, u32)),
        ):
            bind(self.x, "xcb_" + name, result, *args)
        for name, result, args in (
            ("query_version", Cookie, (ptr, u8, u8)),
            ("query_client_ids", Cookie, (ptr, u32, C.POINTER(Spec))),
            ("query_client_ids_ids_iterator", Iterator, (ptr,)),
            ("client_id_value_value_length", C.c_int, (ptr,)),
            ("client_id_value_value", C.POINTER(u32), (ptr,)),
        ):
            bind(self.res, "xcb_res_" + name, result, *args)
        for lib, prefix, names in (
            (self.x, "xcb_", ("query_tree", "get_property", "intern_atom", "get_selection_owner")),
            (self.res, "xcb_res_", ("query_version", "query_client_ids")),
        ):
            for name in names:
                bind(lib, prefix + name + "_reply", ptr, ptr, Cookie, C.POINTER(ptr))
        self.screen = C.c_int()
        self.connection = self.x.xcb_connect(display.encode(), C.byref(self.screen))
        if not self.connection or self.x.xcb_connection_has_error(self.connection):
            self.close()
            raise ValueError("X11 connection unavailable")

    def close(self):
        if self.connection:
            self.x.xcb_disconnect(self.connection)
            self.connection = None

    @contextmanager
    def reply(self, lib, name, cookie):
        error = C.c_void_p()
        value = getattr(lib, name + "_reply")(self.connection, cookie, C.byref(error))
        try:
            if error.value or not value:
                raise ValueError("X11 ownership query failed")
            yield value
        finally:
            self.free(error)
            self.free(value)

    def manager(self):
        # Verify that DISPLAY is managed by the selected satellite instance.
        # SO_PEERCRED is insufficient: niri can create Xwayland's listen socket.
        name = f"WM_S{self.screen.value}".encode()
        cookie = self.x.xcb_intern_atom(self.connection, 1, len(name), name)
        with self.reply(self.x, "xcb_intern_atom", cookie) as reply:
            atom = C.cast(reply, C.POINTER(IdReply)).contents.value
        if not atom:
            raise ValueError("No X11 window manager")
        cookie = self.x.xcb_get_selection_owner(self.connection, atom)
        with self.reply(self.x, "xcb_get_selection_owner", cookie) as reply:
            xid = C.cast(reply, C.POINTER(IdReply)).contents.value
        if not xid:
            raise ValueError("No X11 window manager")
        return self.owner(xid)

    def root(self):
        iterator = self.x.xcb_setup_roots_iterator(self.x.xcb_get_setup(self.connection))
        for _ in range(self.screen.value):
            self.x.xcb_screen_next(C.byref(iterator))
        if iterator.rem < 1:
            raise ValueError("Missing X11 screen")
        return C.cast(iterator.data, C.POINTER(C.c_uint32))[0]

    def windows(self):
        # Satellite's rootless top-level windows are direct root children. Do
        # not guess through a different WM's reparented hierarchy.
        cookie = self.x.xcb_query_tree(self.connection, self.root())
        with self.reply(self.x, "xcb_query_tree", cookie) as reply:
            count = self.x.xcb_query_tree_children_length(reply)
            if not 0 <= count <= MAX_WINDOWS:
                raise ValueError("Too many X11 windows")
            return list(self.x.xcb_query_tree_children(reply)[:count])

    def window_class(self, xid):
        # WM_CLASS=67, STRING=31. Match satellite's class (second nonempty
        # field, falling back to instance), not an arbitrary instance alias.
        cookie = self.x.xcb_get_property(self.connection, 0, xid, 67, 31, 0, 256)
        with self.reply(self.x, "xcb_get_property", cookie) as reply:
            prop = C.cast(reply, C.POINTER(Property)).contents
            if prop.type == 0:
                return None
            size = self.x.xcb_get_property_value_length(reply)
            if prop.format != 8 or prop.type != 31 or prop.bytes_after or not 0 <= size <= 1024:
                raise ValueError("Unsupported X11 class")
            fields = [
                v for v in C.string_at(self.x.xcb_get_property_value(reply), size).split(b"\0") if v
            ]
            value = (fields[1] if len(fields) > 1 else fields[0]) if fields else b""
            return value.decode("utf-8", errors="replace")

    def owner(self, xid):
        # XRes verifies the client that owns this resource. _NET_WM_PID is
        # deliberately never read: it is client-supplied and may be namespaced.
        spec = Spec(xid, 2)  # LOCAL_CLIENT_PID
        cookie = self.res.xcb_res_query_client_ids(self.connection, 1, C.byref(spec))
        with self.reply(self.res, "xcb_res_query_client_ids", cookie) as reply:
            iterator = self.res.xcb_res_query_client_ids_ids_iterator(reply)
            if iterator.rem != 1 or C.cast(iterator.data, C.POINTER(Spec)).contents.mask != 2:
                raise ValueError("No unique X11 owner")
            if self.res.xcb_res_client_id_value_value_length(iterator.data) != 1:
                raise ValueError("No local X11 owner PID")
            pid = self.res.xcb_res_client_id_value_value(iterator.data)[0]
            return stamp(pid)

    def probe(self, identifier):
        cookie = self.res.xcb_res_query_version(self.connection, 1, 2)
        with self.reply(self.res, "xcb_res_query_version", cookie) as reply:
            version = C.cast(reply, C.POINTER(Version)).contents
            if (version.major, version.minor) < (1, 2):
                raise ValueError("XRes 1.2 is required")
        first = self.snapshot(identifier)
        if first != self.snapshot(identifier):
            raise ValueError("X11 ownership changed during query")
        return first

    def snapshot(self, identifier):
        manager = self.manager()
        windows = self.windows()
        owners = [
            {"xid": xid, "identity": self.owner(xid)}
            for xid in sorted(windows)
            if self.window_class(xid) == identifier
        ]
        if set(windows) != set(self.windows()) or manager != self.manager():
            raise ValueError("X11 inventory changed during query")
        return {"manager": manager, "owners": owners}


def query(identifier):
    display = os.environ.get("DISPLAY", "")
    if (
        not isinstance(identifier, str)
        or not 0 < len(identifier) <= 512
        or not LOCAL_DISPLAY.fullmatch(display)
    ):
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), identifier],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
            preexec_fn=partial(child_guard, os.getpid()),
        )
        if len(result.stdout) > 1024 * 1024:
            return None
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def resolve(identifier, proxy, identity, descendant):
    """Accept exactly one live family on the X server hosted by this proxy."""
    result = query(identifier)
    if not isinstance(result, dict):
        return None

    def live(value):
        if not isinstance(value, list) or len(value) != 2 or type(value[0]) is not int:
            return None
        found = identity(value[0])
        return found if found and list(found[:2]) == value else None

    manager = live(result.get("manager"))
    if manager is None or manager[:2] != proxy[:2]:
        return None  # DISPLAY must belong to the selected window's proxy.
    owners = result.get("owners")
    if not isinstance(owners, list) or not 0 < len(owners) <= MAX_WINDOWS:
        return None
    identities = [live(row.get("identity")) if isinstance(row, dict) else None for row in owners]
    if any(owner is None for owner in identities):
        return None
    roots = {
        owner for owner in identities if all(descendant(other[0], owner) for other in identities)
    }
    if len(roots) != 1:
        return None
    owner = roots.pop()
    return owner if not descendant(owner[0], proxy) else None


if __name__ == "__main__":
    connection = None
    try:
        if len(sys.argv) != 2 or not 0 < len(sys.argv[1]) <= 512:
            raise ValueError("Expected one application class")
        connection = XConnection(os.environ.get("DISPLAY", ""))
        # A tooltip may disappear during enumeration. Retry once from a fresh
        # snapshot; a changing/ambiguous inventory still falls back to manual.
        try:
            result = connection.probe(sys.argv[1])
        except ValueError:
            result = connection.probe(sys.argv[1])
        print(json.dumps(result))
    except (OSError, ValueError, IndexError, AttributeError):
        print("null")
    finally:
        if connection:
            connection.close()
