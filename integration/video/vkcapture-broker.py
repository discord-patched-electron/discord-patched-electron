#!/usr/bin/env python3
"""Explicit same-user game selection. JSONL stdin/stdout; no capture on connect."""

import argparse
import array
import errno
import json
import os
import secrets
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

MAGIC = 0x44504547
VERSION = 1
PACKET_SIZE = 128
OBS_ENDPOINT = "\0/com/obsproject/vkcapture"
PREVIEW_LIMIT = 750000


def hello(sock):
    data, ancillary, flags, _ = sock.recvmsg(
        PACKET_SIZE, socket.CMSG_SPACE(64), socket.MSG_CMSG_CLOEXEC
    )
    descriptors = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(payload[: len(payload) - len(payload) % values.itemsize])
            descriptors.extend(values)
    for fd in descriptors:
        os.close(fd)
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or descriptors:
        raise ValueError("Invalid hello descriptors")
    if len(data) != PACKET_SIZE or struct.unpack_from("=IHH", data) != (MAGIC, VERSION, 1):
        raise ValueError("Unsupported game hook")
    # Only an opted-in executable label, bounded and stripped of control chars.
    name = (
        "".join(
            c
            for c in data[112:128].split(b"\0", 1)[0].decode("utf-8", "replace")
            if c.isprintable()
        )[:16]
        or "Game"
    )
    return {
        "name": name,
        "uuid": data[96:112].hex(),
        "direct": bool(struct.unpack_from("=I", data, 44)[0] & 8),
    }


def obs_hello(sock, buffered):
    data, ancillary, flags, _ = sock.recvmsg(
        PACKET_SIZE - len(buffered), socket.CMSG_SPACE(64), socket.MSG_CMSG_CLOEXEC
    )
    descriptors = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(payload[: len(payload) - len(payload) % values.itemsize])
            descriptors.extend(values)
    for fd in descriptors:
        os.close(fd)
    if not data or descriptors or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise ValueError("Invalid upstream hello")
    buffered.extend(data)
    if len(buffered) < PACKET_SIZE:
        return None
    if buffered[0] != 10:
        raise ValueError("Unsupported upstream hook")
    name = "".join(
        c for c in buffered[1:49].split(b"\0", 1)[0].decode("utf-8", "replace") if c.isprintable()
    )[:48]
    return {"name": name or "Game", "direct": False, "uuid": None}


class Broker:
    def __init__(self, endpoint, producer, render_node, compat_endpoint=None):
        self.endpoint = Path(endpoint)
        self.producer = Path(producer).resolve(strict=True)
        self.render_node = render_node
        parent = self.endpoint.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Game capture requires an owned private runtime directory")
        if self.endpoint.exists() or self.endpoint.is_symlink():
            raise ValueError("Game capture socket already exists; refusing replacement")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        self.server.bind(str(self.endpoint))
        os.chmod(self.endpoint, 0o600)
        self.identity = self.endpoint.lstat()
        self.server.listen(16)
        self.server.setblocking(False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.server, selectors.EVENT_READ, "accept")
        self.peers = {}
        self.child = None
        self.child_token = None
        self.stopping = None
        self.child_buffer = b""
        self.running = True
        self.compat_endpoint = compat_endpoint
        self.compat_server = None
        self.compat_status = "disabled"
        self.last_listing = 0
        self.identities = {}
        self.previews = {}
        self.pending_start = None

    def listen_compat(self):
        if self.compat_endpoint is None or self.compat_server is not None:
            return
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            server.bind(self.compat_endpoint)
            server.listen(16)
            server.setblocking(False)
            self.selector.register(server, selectors.EVENT_READ, "accept-obs")
        except OSError as error:
            server.close()
            self.compat_status = "in-use" if error.errno == errno.EADDRINUSE else "unavailable"
            return
        self.compat_server = server
        self.compat_status = "listening"

    def close_compat(self):
        if self.compat_server is not None:
            self.selector.unregister(self.compat_server)
            self.compat_server.close()
            self.compat_server = None
        for token, peer in list(self.peers.items()):
            if peer.get("protocol") == "obs":
                self.forget(token)
        self.compat_status = "idle"

    @staticmethod
    def emit(value):
        print(json.dumps(value, separators=(",", ":")), flush=True)

    def forget(self, token):
        self.cancel_preview(token)
        peer = self.peers.pop(token, None)
        if peer and peer["socket"] is not None:
            self.selector.unregister(peer["socket"])
            peer["socket"].close()

    def accept(self, compat=False):
        sock, _ = (self.compat_server if compat else self.server).accept()
        sock.setblocking(False)
        pid, uid, _ = struct.unpack(
            "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.geteuid():
            sock.close()
            return
        protocol = "obs" if compat else "dpe"
        identity = (pid, protocol)
        previous = self.identities.get(identity)
        old = self.peers.get(previous[0]) if previous else None
        if len(self.peers) >= 32 and (old is None or old["socket"] is not None):
            sock.close()
            return
        token = (
            previous[0]
            if previous and (old is None or old["socket"] is None)
            else secrets.token_hex(16)
        )
        self.identities[identity] = (token, time.monotonic())
        if len(self.identities) > 64:
            del self.identities[min(self.identities, key=lambda item: self.identities[item][1])]
        self.peers[token] = {
            "socket": sock,
            "pid": pid,
            "name": None,
            "created": time.monotonic(),
            "protocol": protocol,
            "hello": bytearray(),
        }
        self.selector.register(sock, selectors.EVENT_READ, token)

    def child_environment(self, peer):
        environment = {
            key: os.environ[key]
            for key in (
                "PATH",
                "XDG_RUNTIME_DIR",
                "PIPEWIRE_REMOTE",
                "HOME",
                "LANG",
                "DISPLAY",
                "XAUTHORITY",
                "DPE_GAME_CAPTURE_HANDOFF",
            )
            if key in os.environ
        }
        handoff = environment.get("DPE_GAME_CAPTURE_HANDOFF", "auto")
        if handoff not in ("auto", "copy", "relay"):
            raise ValueError("Invalid game capture handoff")
        environment["DPE_GAME_CAPTURE_HANDOFF"] = (
            "copy"
            if peer.get("protocol") == "obs" or (handoff == "auto" and not peer.get("direct"))
            else handoff
        )
        return environment

    def child_arguments(self, peer):
        args = ["--hook-fd", str(peer["socket"].fileno()), "--render-node", self.render_node]
        if peer.get("protocol") == "obs":
            return args + ["--hook-protocol", "obs"]
        return args + ["--device-uuid", peer["uuid"]]

    def cancel_preview(self, token=None):
        for key, preview in list(self.previews.items()):
            if token is not None and key != token:
                continue
            del self.previews[key]
            process = preview["process"]
            if not process.stdout.closed:
                self.selector.unregister(process.stdout)
                process.stdout.close()
            process.stdin.close()
            process.kill()
            process.wait(timeout=2)
            if preview["id"] is not None:
                self.emit({"id": preview["id"], "ok": False, "error": "Preview cancelled"})
            peer = self.peers.get(key)
            if peer:
                peer["reconnecting"] = time.monotonic()
                self.identities[(peer["pid"], peer.get("protocol", "dpe"))] = (
                    key,
                    time.monotonic(),
                )

    def cancel_pending(self):
        if self.pending_start:
            self.emit(
                {
                    "id": self.pending_start["request"].get("id"),
                    "ok": False,
                    "error": "Capture cancelled",
                }
            )
            self.pending_start = None

    def preview_input(self, token):
        preview = self.previews.get(token)
        if not preview:
            return
        data = os.read(preview["process"].stdout.fileno(), 65536)
        if not data:
            self.cancel_preview(token)
            return
        preview["buffer"] += data
        if len(preview["buffer"]) > PREVIEW_LIMIT or preview["id"] is None:
            self.cancel_preview(token)
            return
        if b"\n" not in preview["buffer"]:
            return
        try:
            line, rest = preview["buffer"].split(b"\n", 1)
            value = json.loads(line)
            if (
                rest
                or not isinstance(value, dict)
                or value.get("format") != "BGRA"
                or type(value.get("width")) is not int
                or type(value.get("height")) is not int
                or not 1 <= value["width"] <= 480
                or not 1 <= value["height"] <= 270
                or not isinstance(value.get("pixels"), str)
                or len(value["pixels"]) != (value["width"] * value["height"] * 4 + 2) // 3 * 4
            ):
                raise ValueError("Invalid preview")
            self.emit({"id": preview["id"], "ok": True, **value})
            preview["id"] = None
            preview["buffer"] = b""
            preview["started"] = time.monotonic()
        except (ValueError, TypeError):
            self.cancel_preview(token)

    def command(self, request):
        result = {"id": request.get("id"), "ok": True}
        op = request.get("op")
        if op == "list":
            self.last_listing = time.monotonic()
            self.listen_compat()
            result["compatibility"] = self.compat_status
            result["sources"] = [
                {
                    "token": token,
                    "name": peer["name"],
                    "available": peer.get("uuid") != "00" * 16,
                    "protocol": peer.get("protocol", "dpe"),
                    "producerPacing": peer.get("protocol") != "obs",
                }
                for token, peer in self.peers.items()
                if peer["name"] is not None
            ]
        elif op == "preview":
            token = request.get("token")
            peer = self.peers.get(token)
            width, height = request.get("width"), request.get("height")
            executable = self.producer.with_name("dpe-vkcapture-preview")
            preview = self.previews.get(token)
            if preview:
                if (
                    self.child
                    or self.pending_start
                    or preview["id"] is not None
                    or (width, height) != preview["size"]
                ):
                    raise ValueError("Preview unavailable")
                preview["id"] = request.get("id")
                preview["started"] = time.monotonic()
                preview["process"].stdin.write(b"\n")
                preview["process"].stdin.flush()
                return
            if (
                self.child
                or len(self.previews) >= 4
                or self.pending_start
                or not peer
                or peer["name"] is None
                or peer["socket"] is None
                or peer.get("uuid") == "00" * 16
                or type(width) is not int
                or type(height) is not int
                or not 1 <= width <= 480
                or not 1 <= height <= 270
                or not executable.is_file()
            ):
                raise ValueError("Preview unavailable")
            fd = peer["socket"].fileno()
            process = subprocess.Popen(
                [
                    str(executable),
                    *self.child_arguments(peer),
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--persistent",
                    "1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                pass_fds=(fd,),
                env=self.child_environment(peer),
            )
            self.previews[token] = {
                "size": (width, height),
                "process": process,
                "id": request.get("id"),
                "buffer": b"",
                "started": time.monotonic(),
            }
            self.selector.unregister(peer["socket"])
            peer["socket"].close()
            peer["socket"] = None
            peer["reconnecting"] = time.monotonic()
            self.identities[(peer["pid"], peer.get("protocol", "dpe"))] = (
                request["token"],
                time.monotonic(),
            )
            os.set_blocking(process.stdout.fileno(), False)
            self.selector.register(process.stdout, selectors.EVENT_READ, ("preview", token))
            return
        elif op == "cancel-preview":
            self.cancel_preview(request.get("token"))
        elif op == "suspend":
            self.cancel_preview()
            self.cancel_pending()
            self.close_compat()
        elif op == "start":
            self.cancel_preview()
            peer = self.peers.get(request.get("token"))
            fps = request.get("fps")
            if (
                self.child
                or not peer
                or peer["name"] is None
                or peer.get("uuid") == "00" * 16
                or type(fps) is not int
                or not 1 <= fps <= 240
            ):
                raise ValueError("Game is unavailable or another capture is active")
            if peer["socket"] is None:
                if self.pending_start:
                    raise ValueError("Another game capture is starting")
                self.pending_start = {"request": dict(request), "deadline": time.monotonic() + 3}
                return
            fd = peer["socket"].fileno()
            self.child = subprocess.Popen(
                [
                    str(self.producer),
                    *self.child_arguments(peer),
                    "--fps",
                    str(fps),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                pass_fds=(fd,),
                env=self.child_environment(peer),
            )
            os.set_blocking(self.child.stdin.fileno(), False)
            self.child_token = request["token"]
            self.forget(self.child_token)
            os.set_blocking(self.child.stdout.fileno(), False)
            self.selector.register(self.child.stdout, selectors.EVENT_READ, "producer")
            result["starting"] = True
        elif op == "rate":
            fps = request.get("fps")
            if (
                not self.child
                or self.stopping is not None
                or type(fps) is not int
                or not 1 <= fps <= 240
            ):
                raise ValueError("No active capture or invalid rate")
            command = f"rate {fps}\n".encode()
            if os.write(self.child.stdin.fileno(), command) != len(command):
                raise ValueError("Rate command was not accepted")
            result["requestedFps"] = fps
        elif op == "stop":
            self.cancel_pending()
            if self.child and self.stopping is None:
                self.child.terminate()
                self.stopping = time.monotonic()
        elif op == "quit":
            self.running = False
        else:
            raise ValueError("Unknown game capture operation")
        self.emit(result)

    def producer_input(self):
        data = os.read(self.child.stdout.fileno(), 4096)
        if not data:
            self.selector.unregister(self.child.stdout)
            self.child.stdout.close()
            return
        self.child_buffer += data
        if len(self.child_buffer) > 16384:
            self.child.terminate()
            raise ValueError("Producer status exceeded bound")
        while b"\n" in self.child_buffer:
            line, self.child_buffer = self.child_buffer.split(b"\n", 1)
            value = json.loads(line)
            if value.get("event") not in ("ready", "ended"):
                continue
            self.emit(
                {
                    "token": self.child_token,
                    **{
                        k: value[k]
                        for k in (
                            "event",
                            "backend",
                            "nodeId",
                            "width",
                            "height",
                            "generation",
                            "fps",
                            "frames",
                            "dropped",
                            "textureChanges",
                            "retiredBuffers",
                            "fencedFrames",
                            "handoff",
                            "handoffFallback",
                            "relayFrames",
                            "relayReleases",
                            "failed",
                            "errorCode",
                        )
                        if k in value
                    },
                }
            )

    def close(self):
        self.cancel_preview()
        self.close_compat()
        for token in list(self.peers):
            self.forget(token)
        if self.child:
            self.child.terminate()
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=2)
        if self.child:
            self.child.stdin.close()
            self.child.stdout.close()
        self.selector.close()
        self.server.close()
        try:
            info = self.endpoint.lstat()
            if (info.st_dev, info.st_ino) == (self.identity.st_dev, self.identity.st_ino):
                self.endpoint.unlink()
        except FileNotFoundError:
            pass

    def run(self):
        fd = sys.stdin.fileno()
        os.set_blocking(fd, False)
        self.selector.register(fd, selectors.EVENT_READ, "stdin")
        buffer = b""
        self.emit({"event": "listening", "protocol": VERSION})
        while self.running:
            for key, _ in self.selector.select(0.25):
                try:
                    if key.data == "accept":
                        self.accept()
                    elif key.data == "accept-obs":
                        self.accept(compat=True)
                    elif isinstance(key.data, tuple) and key.data[0] == "preview":
                        self.preview_input(key.data[1])
                    elif key.data == "producer":
                        self.producer_input()
                    elif key.data == "stdin":
                        data = os.read(fd, 4096)
                        if not data:
                            self.running = False
                            break
                        buffer += data
                        if len(buffer) > 16384:
                            self.running = False
                            break
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            request = json.loads(line)
                            if not isinstance(request, dict):
                                raise ValueError("Expected command object")
                            try:
                                self.command(request)
                            except (ValueError, OSError):
                                self.emit(
                                    {
                                        "id": request.get("id"),
                                        "ok": False,
                                        "error": "Game capture operation failed",
                                    }
                                )
                    else:
                        peer = self.peers.get(key.data)
                        if peer is None:
                            continue
                        if peer["name"] is not None:
                            self.forget(key.data)  # unsolicited data/EOF, never start capture
                        else:
                            value = (
                                obs_hello(peer["socket"], peer["hello"])
                                if peer.get("protocol") == "obs"
                                else hello(peer["socket"])
                            )
                            if value:
                                peer.update(value)
                except (ValueError, OSError, json.JSONDecodeError):
                    if key.data in self.peers:
                        self.forget(key.data)
                    else:
                        self.emit({"ok": False, "error": "Game capture operation failed"})
            for token, peer in list(self.peers.items()):
                if peer["name"] is None and time.monotonic() - peer["created"] > 5:
                    self.forget(token)
                elif (
                    token not in self.previews
                    and peer["socket"] is None
                    and time.monotonic() - peer["reconnecting"] > 7
                ):
                    self.forget(token)
            now = time.monotonic()
            for identity, (_, seen) in list(self.identities.items()):
                if now - seen > 30:
                    del self.identities[identity]
            if (
                self.compat_server is not None
                and not self.pending_start
                and now - self.last_listing > 7
            ):
                self.close_compat()  # Do not reserve OBS's global endpoint outside the picker.
            for token, preview in list(self.previews.items()):
                if preview["process"].poll() is not None or now - preview["started"] > (
                    3.5 if preview["id"] is not None else 1
                ):
                    self.cancel_preview(token)
            if self.pending_start:
                waiting = self.pending_start
                peer = self.peers.get(waiting["request"].get("token"))
                if peer and peer["socket"] is not None and peer["name"] is not None:
                    self.pending_start = None
                    try:
                        self.command(waiting["request"])
                    except (ValueError, OSError):
                        self.emit(
                            {
                                "id": waiting["request"].get("id"),
                                "ok": False,
                                "error": "Game capture operation failed",
                            }
                        )
                elif now >= waiting["deadline"]:
                    self.pending_start = None
                    self.emit(
                        {
                            "id": waiting["request"].get("id"),
                            "ok": False,
                            "error": "Game capture hook did not reconnect",
                        }
                    )
            if self.child:
                if (
                    self.stopping
                    and time.monotonic() - self.stopping > 2
                    and self.child.poll() is None
                ):
                    self.child.kill()
                if self.child.poll() is not None:
                    # The child has exited: drain its bounded status then unregister
                    # before clearing self.child, so EOF cannot dispatch against None.
                    for _ in range(4):
                        if self.child.stdout.closed:
                            break
                        self.producer_input()
                    if not self.child.stdout.closed:
                        self.selector.unregister(self.child.stdout)
                        self.child.stdout.close()
                    self.emit(
                        {
                            "event": "stopped",
                            "token": self.child_token,
                            "code": self.child.returncode,
                        }
                    )
                    self.child.stdin.close()
                    self.child = None
                    self.child_token = None
                    self.stopping = None
                    self.child_buffer = b""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--render-node", required=True)
    args = parser.parse_args()
    broker = Broker(args.socket, args.producer, args.render_node, OBS_ENDPOINT)

    def stop(_signum, _frame):
        broker.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        broker.run()
    finally:
        broker.close()


if __name__ == "__main__":
    main()
