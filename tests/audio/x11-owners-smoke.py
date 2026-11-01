"""Real XRes fixture; run on a disposable Xvfb, never the user's display.

Creates sibling window-manager/application processes, with deliberately false
_NET_WM_PID properties. No audio, Discord account, or desktop images are used.
"""

import ctypes as C
import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integration/audio"))

import pipewire_audio as audio
import x11_owners as x11


def client(identifier, manager):
    connection = x11.XConnection(os.environ["DISPLAY"])
    x = connection.x
    ptr, u32, u16, i16, u8 = C.c_void_p, C.c_uint32, C.c_uint16, C.c_int16, C.c_uint8
    x11.bind(x, "xcb_generate_id", u32, ptr)
    x11.bind(
        x,
        "xcb_create_window",
        x11.Cookie,
        ptr,
        u8,
        u32,
        u32,
        i16,
        i16,
        u16,
        u16,
        u16,
        u16,
        u32,
        u32,
        ptr,
    )
    x11.bind(x, "xcb_change_property", x11.Cookie, ptr, u8, u32, u32, u32, u8, u32, ptr)
    x11.bind(x, "xcb_set_selection_owner", x11.Cookie, ptr, u32, u32, u32)
    x11.bind(x, "xcb_map_window", x11.Cookie, ptr, u32)
    xid = x.xcb_generate_id(connection.connection)
    x.xcb_create_window(
        connection.connection, 0, xid, connection.root(), 0, 0, 40, 40, 0, 1, 0, 0, None
    )
    value = ("different-instance\0" + identifier + "\0").encode()
    x.xcb_change_property(connection.connection, 0, xid, 67, 31, 8, len(value), value)

    def atom(name):
        encoded = name.encode()
        cookie = x.xcb_intern_atom(connection.connection, 0, len(encoded), encoded)
        with connection.reply(x, "xcb_intern_atom", cookie) as reply:
            return C.cast(reply, C.POINTER(x11.IdReply)).contents.value

    fake_pid = u32(1)  # Intentionally unusable: XRes must still find this process.
    x.xcb_change_property(
        connection.connection, 0, xid, atom("_NET_WM_PID"), 6, 32, 1, C.byref(fake_pid)
    )
    if manager:
        x.xcb_set_selection_owner(connection.connection, xid, atom("WM_S0"), 0)
    x.xcb_map_window(connection.connection, xid)
    connection.windows()  # Round trip flushes all setup requests.
    print(json.dumps({"pid": os.getpid(), "xid": xid}), flush=True)
    try:
        sys.stdin.buffer.read()
    finally:
        connection.close()


@contextmanager
def window(identifier, manager=False):
    process = subprocess.Popen(
        [sys.executable, __file__, "--client", identifier, "manager" if manager else "app"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        if not select.select([process.stdout], [], [], 5)[0]:
            raise AssertionError("X11 fixture timed out")
        row = json.loads(process.stdout.readline())
        yield row
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


@contextmanager
def private_server(*extra):
    # Own this server explicitly so fault injection cannot stop a user's X11
    # session, or even the outer xvfb-run server used for the ownership fixture.
    process = subprocess.Popen(
        ["Xvfb", "-displayfd", "1", "-screen", "0", "800x600x24", "-nolisten", "tcp", *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    old_display = os.environ.get("DISPLAY")
    try:
        if not select.select([process.stdout], [], [], 5)[0]:
            raise AssertionError("Private X server startup timed out")
        number = process.stdout.readline().strip()
        assert number.isdecimal(), "Private X server failed to start"
        os.environ["DISPLAY"] = ":" + number
        yield process
    finally:
        if old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old_display
        if process.poll() is None:
            process.send_signal(signal.SIGCONT)
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def until(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Fixture condition timed out")


def failures():
    # Reap the deliberately orphaned query ourselves, including when PID 1 in
    # a test container doesn't reap children. This affects only this fixture.
    assert C.CDLL(None).prctl(36, 1) == 0  # PR_SET_CHILD_SUBREAPER
    with private_server() as server:
        server.send_signal(signal.SIGSTOP)
        until(
            lambda: Path(f"/proc/{server.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T"
        )
        started = time.monotonic()
        assert x11.query("fixture") is None
        elapsed = time.monotonic() - started
        assert 1.8 <= elapsed < 4.5, f"Unbounded X query: {elapsed}"
        caller = subprocess.Popen([sys.executable, __file__, "--query"], stdout=subprocess.DEVNULL)
        child = None
        try:
            children = Path(f"/proc/{caller.pid}/task/{caller.pid}/children")
            child = int(until(lambda: children.read_text().split())[0])
            caller.kill()
            caller.wait(timeout=2)
            pid, status = until(lambda: r if (r := os.waitpid(child, os.WNOHANG))[0] else None)
            assert pid == child and os.waitstatus_to_exitcode(status) in (-signal.SIGKILL, 1)
            child = None
        finally:
            if caller.poll() is None:
                caller.kill()
                caller.wait()
            if child and audio.process_identity(child):
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
    with private_server("-extension", "X-Resource"):
        assert x11.query("fixture") is None
    return round(elapsed, 3)


def check():
    # The test is only supported on the private server launched by the caller.
    if os.environ.get("DPE_X11_FIXTURE") != "1":
        raise RuntimeError(
            "Run with DPE_X11_FIXTURE=1 xvfb-run -a uv run python tests/audio/x11-owners-smoke.py"
        )
    identifier = "dpe.ExactClass"
    with window("fixture.wm", True) as manager, window(identifier) as app:
        proxy = audio.process_identity(manager["pid"])

        def resolve():
            return x11.resolve(identifier, proxy, audio.process_identity, audio.descendant)

        assert resolve()[:2] == audio.process_identity(app["pid"])[:2]
        assert x11.query("different-instance")["owners"] == []
        assert x11.query(identifier.lower())["owners"] == []
        assert (
            x11.resolve(
                identifier,
                audio.process_identity(os.getpid()),
                audio.process_identity,
                audio.descendant,
            )
            is None
        )
        with window(identifier):
            assert resolve() is None, "Distinct instances must remain ambiguous"
        assert resolve()[:2] == audio.process_identity(app["pid"])[:2]
        with window("unrelated.Class"):
            assert resolve()[:2] == audio.process_identity(app["pid"])[:2]
        for _ in range(10):
            with window(identifier):
                assert resolve() is None
            assert resolve()[:2] == audio.process_identity(app["pid"])[:2]
    timeout_seconds = failures()
    print(
        json.dumps(
            {
                "result": "passed",
                "hung_server_timeout_seconds": timeout_seconds,
                "checks": [
                    "server-owned PID despite spoofed property",
                    "exact class, not instance or case folding",
                    "selected satellite must own WM selection",
                    "duplicate instance falls back",
                    "unrelated window excluded",
                    "disappeared instance no longer ambiguous",
                    "ten duplicate-instance create/destroy cycles",
                    "frozen X server times out",
                    "killed routing parent leaves no query process",
                    "missing XRes extension falls back",
                ],
            }
        )
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--query"]:
        x11.query("fixture")
    elif len(sys.argv) == 4 and sys.argv[1] == "--client":
        client(sys.argv[2], sys.argv[3] == "manager")
    else:
        check()
