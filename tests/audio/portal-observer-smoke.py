"""Run under dbus-run-session with uv run --with dbus-next==0.2.3.

Synthetic portal/Mutter services only; no desktop, account or capture access.
"""

import asyncio
import json
from pathlib import Path

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus


async def main():
    service = await MessageBus().connect()
    caller = await MessageBus().connect()
    for name in (
        "org.freedesktop.impl.portal.desktop.kde",
        "org.freedesktop.portal.Desktop",
        "org.gnome.Mutter.ScreenCast",
    ):
        await service.request_name(name)

    def handle(message):
        if message.message_type != MessageType.METHOD_CALL:
            return None
        if message.member == "Start":
            return Message.new_method_return(
                message,
                "ua{sv}",
                [
                    0,
                    {
                        "streams": Variant("a(ua{sv})", [[42, {"source_type": Variant("u", 2)}]]),
                        "restore_data": Variant(
                            "(suv)",
                            [
                                "KDE",
                                1,
                                Variant(
                                    "a{sv}",
                                    {
                                        "windows": Variant(
                                            "a(ss)", [["org.mozilla.firefox", "PRIVATE TITLE"]]
                                        ),
                                    },
                                ),
                            ],
                        ),
                    },
                ],
            )
        if message.member == "RecordWindow":
            return Message.new_method_return(
                message, "o", ["/org/gnome/Mutter/ScreenCast/Stream/u1"]
            )
        return None

    service.add_message_handler(handle)
    module = Path(__file__).resolve().parents[2] / "integration/audio/portal-detection.cjs"
    node = await asyncio.create_subprocess_exec(
        "node",
        "-e",
        'const o = require(process.argv[1]).createObserver({changed:()=>console.log(JSON.stringify(o.snapshot()))}); o.start(); setTimeout(()=>console.log("ready"),300); setTimeout(()=>{o.clear();process.exit(0)},2500);',
        str(module),
        stdout=asyncio.subprocess.PIPE,
    )
    assert await node.stdout.readline() == b"ready\n"
    await caller.call(
        Message(
            destination="org.freedesktop.impl.portal.desktop.kde",
            path="/org/freedesktop/portal/desktop",
            interface="org.freedesktop.impl.portal.ScreenCast",
            member="Start",
        )
    )
    await caller.call(
        Message(
            destination="org.gnome.Mutter.ScreenCast",
            path="/org/gnome/Mutter/ScreenCast/Session/u1",
            interface="org.gnome.Mutter.ScreenCast.Session",
            member="RecordWindow",
            signature="a{sv}",
            body=[{"window-id": Variant("t", 999)}],
        )
    )
    await service.send(
        Message.new_signal(
            "/org/gnome/Mutter/ScreenCast/Stream/u1",
            "org.gnome.Mutter.ScreenCast.Stream",
            "PipeWireStreamAdded",
            "u",
            [43],
        )
    )
    await service.send(
        Message.new_signal(
            "/org/freedesktop/portal/desktop/request/1/test",
            "org.freedesktop.portal.Request",
            "Response",
            "ua{sv}",
            [0, {"streams": Variant("a(ua{sv})", [[44, {"source_type": Variant("u", 1)}]])}],
        )
    )
    output = (await node.communicate())[0].decode()
    assert node.returncode == 0
    records = json.loads(output.splitlines()[-1])
    assert {r["node"] for r in records} == {42, 43, 44}, records
    assert records[0]["appId"] == "org.mozilla.firefox"
    assert records[1]["windowId"] == 999
    assert records[2]["kind"] == "system"
    assert "PRIVATE" not in output
    print("PASS: real busctl portal replies, Mutter call/reply/signal and monitor selection")
    service.disconnect()
    caller.disconnect()


asyncio.run(main())
