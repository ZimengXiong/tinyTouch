"""Bounded, fresh KDE/Wayland app lookup without Accessibility permissions.

Each lookup uses a private D-Bus connection and a temporary KWin script. Only
KWin's unique bus owner may report the result. No cached focus survives a KWin
restart. Lock-state errors, timeouts and missing app IDs disable app overrides.
"""
import asyncio
import json
from pathlib import Path
import re
import secrets
import tempfile

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus

APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
TEMPLATE = Path(__file__).resolve().parents[1] / "kwin/active_app.js"
LOOKUP_TIMEOUT = 0.35
CLEANUP_TIMEOUT = 0.1


def normalize_app_id(value):
    if value.endswith(".desktop"):
        value = value[:-8]
    return value if APP_ID.fullmatch(value) else None


async def call(bus, destination, path, interface, member, signature="", body=None):
    reply = await bus.call(Message(destination=destination, path=path,
                                   interface=interface, member=member,
                                   signature=signature, body=body or []))
    if reply is None or reply.message_type != MessageType.METHOD_RETURN:
        raise RuntimeError("Desktop D-Bus request failed")
    return reply.body


async def unlocked(bus):
    result = await call(bus, "org.freedesktop.ScreenSaver", "/ScreenSaver",
                        "org.freedesktop.ScreenSaver", "GetActive")
    return len(result) == 1 and result[0] is False


async def query_app():
    bus = None
    plugin = "tinytouch-query-" + secrets.token_hex(16)
    token = secrets.token_hex(16)
    temporary = tempfile.TemporaryDirectory(prefix="tinytouch-kwin-")
    attempted_load = False
    try:
        async def lookup():
            nonlocal bus, attempted_load
            bus = await MessageBus().connect()
            if not await unlocked(bus):
                return None
            owner = (await call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                "org.freedesktop.DBus", "GetNameOwner", "s",
                                ["org.kde.KWin"]))[0]
            report = asyncio.get_running_loop().create_future()

            def receive(message):
                if (message.message_type == MessageType.METHOD_CALL
                        and message.path == "/org/tinytouch/Query"
                        and message.interface == "org.tinytouch.Query"
                        and message.member == "Report"):
                    if (message.sender == owner and message.signature == "ss"
                            and message.body[0] == token):
                        if not report.done():
                            report.set_result(normalize_app_id(message.body[1]))
                        return Message.new_method_return(message)
                    return Message.new_error(message, "org.freedesktop.DBus.Error.AccessDenied",
                                             "Only the queried KWin owner may report focus")
                return None

            bus.add_message_handler(receive)
            script = Path(temporary.name) / "active-app.js"
            script.write_text(TEMPLATE.read_text().replace("DESTINATION", json.dumps(bus.unique_name))
                              .replace("TOKEN", json.dumps(token)), encoding="utf-8")
            attempted_load = True
            script_id = (await call(bus, "org.kde.KWin", "/Scripting",
                                    "org.kde.kwin.Scripting", "loadScript", "ss",
                                    [str(script), plugin]))[0]
            if type(script_id) is not int or script_id < 0:
                return None
            await call(bus, "org.kde.KWin", f"/Scripting/Script{script_id}",
                       "org.kde.kwin.Script", "run")
            app = await report
            # A lock may have started while the compositor was answering.
            return app if await unlocked(bus) else None

        return await asyncio.wait_for(lookup(), LOOKUP_TIMEOUT)
    finally:
        if bus is not None:
            if attempted_load:
                try:
                    await asyncio.wait_for(call(bus, "org.kde.KWin", "/Scripting",
                                                "org.kde.kwin.Scripting", "unloadScript",
                                                "s", [plugin]), CLEANUP_TIMEOUT)
                except Exception:
                    pass
            bus.disconnect()
        temporary.cleanup()


def frontmost_app():
    try:
        return asyncio.run(query_app())
    except Exception:
        # Preserve default/finger behavior without exposing raw D-Bus errors.
        return None
