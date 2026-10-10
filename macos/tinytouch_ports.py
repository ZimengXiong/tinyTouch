"""Discover macOS serial devices without retaining native registry objects."""

from __future__ import annotations

import ctypes
from typing import Callable, NamedTuple


class SerialPort(NamedTuple):
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    location: str


class MacSerialPorts:
    """Own and release every Core Foundation value and IOKit handle per scan."""

    def __init__(self):
        self.io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        pointer = ctypes.c_void_p
        handle = ctypes.c_uint32
        signatures = [
            (self.io, "IOServiceMatching", [ctypes.c_char_p], pointer),
            (self.io, "IOServiceGetMatchingServices",
             [handle, pointer, ctypes.POINTER(handle)], ctypes.c_int),
            (self.io, "IOIteratorNext", [handle], handle),
            (self.io, "IOObjectRelease", [handle], ctypes.c_int),
            (self.io, "IORegistryEntrySearchCFProperty",
             [handle, ctypes.c_char_p, pointer, pointer, ctypes.c_uint32], pointer),
            (self.cf, "CFStringCreateWithCString",
             [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer),
            (self.cf, "CFStringGetLength", [pointer], ctypes.c_long),
            (self.cf, "CFStringGetMaximumSizeForEncoding",
             [ctypes.c_long, ctypes.c_uint32], ctypes.c_long),
            (self.cf, "CFStringGetCString",
             [pointer, pointer, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
            (self.cf, "CFNumberGetValue",
             [pointer, ctypes.c_int, pointer], ctypes.c_bool),
            (self.cf, "CFGetTypeID", [pointer], ctypes.c_ulong),
            (self.cf, "CFStringGetTypeID", [], ctypes.c_ulong),
            (self.cf, "CFNumberGetTypeID", [], ctypes.c_ulong),
            (self.cf, "CFRelease", [pointer], None),
        ]
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result

    def _property(self, service, name, *, numeric=False, parents=True):
        utf8 = 0x08000100
        key = self.cf.CFStringCreateWithCString(None, name.encode("utf-8"), utf8)
        if not key:
            raise MemoryError("Could not allocate an IOKit property key.")
        try:
            # Search recursively through parents without acquiring parent handles.
            value = self.io.IORegistryEntrySearchCFProperty(
                service, b"IOService", key, None, 3 if parents else 0
            )
            if not value:
                return None
            try:
                kind = self.cf.CFGetTypeID(value)
                if numeric:
                    if kind != self.cf.CFNumberGetTypeID():
                        return None
                    number = ctypes.c_int64()
                    if self.cf.CFNumberGetValue(value, 4, ctypes.byref(number)):
                        return number.value
                elif kind == self.cf.CFStringGetTypeID():
                    size = self.cf.CFStringGetMaximumSizeForEncoding(
                        self.cf.CFStringGetLength(value), utf8
                    ) + 1
                    buffer = ctypes.create_string_buffer(size)
                    if self.cf.CFStringGetCString(value, buffer, size, utf8):
                        return buffer.value.decode("utf-8")
                return None
            finally:
                self.cf.CFRelease(value)
        finally:
            self.cf.CFRelease(key)

    def comports(self) -> list[SerialPort]:
        matching = self.io.IOServiceMatching(b"IOSerialBSDClient")
        if not matching:
            raise MemoryError("Could not allocate an IOKit matching dictionary.")
        iterator = ctypes.c_uint32()
        # IOServiceGetMatchingServices consumes the dictionary, including on error.
        status = self.io.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator))
        try:
            if status:
                raise OSError(f'Could not list serial devices. IOKit status: {status}.')
            ports = []
            while service := self.io.IOIteratorNext(iterator):
                try:
                    device = self._property(service, "IOCalloutDevice", parents=False)
                    if not device:
                        continue
                    vid = self._property(service, "idVendor", numeric=True)
                    pid = self._property(service, "idProduct", numeric=True)
                    serial_number = self._property(service, "USB Serial Number")
                    location = self._property(service, "locationID", numeric=True)
                    ports.append(SerialPort(
                        device, vid, pid, serial_number, _location_string(location)
                    ))
                finally:
                    self.io.IOObjectRelease(service)
            return ports
        finally:
            if iterator.value:
                self.io.IOObjectRelease(iterator)


def _location_string(value: int | None) -> str:
    if value is None:
        return ""
    value &= 0xFFFFFFFF
    parts = [f"{value >> 24}-"]
    while value & 0xF00000:
        if len(parts) > 1:
            parts.append(".")
        parts.append(str((value >> 20) & 0xF))
        value <<= 4
    return "".join(parts)


class SerialPortWatcher:
    """Call on_change from a private dispatch queue when a serial device appears or disappears."""

    def __init__(self, on_change: Callable[[], None]):
        self._on_change = on_change
        self.io = _PORTS.io
        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        pointer = ctypes.c_void_p
        handle = ctypes.c_uint32
        callback_type = ctypes.CFUNCTYPE(None, pointer, handle)
        signatures = [
            (self.io, "IONotificationPortCreate", [handle], pointer),
            (self.io, "IONotificationPortSetDispatchQueue", [pointer, pointer], None),
            (self.io, "IOServiceAddMatchingNotification",
             [pointer, ctypes.c_char_p, pointer, callback_type, pointer, ctypes.POINTER(handle)],
             ctypes.c_int),
            (system, "dispatch_queue_create", [ctypes.c_char_p, pointer], pointer),
        ]
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result
        # ctypes frees the trampoline with this object, so keep it for the process lifetime.
        self._callback = callback_type(self._notified)
        self.iterators: list[int] = []
        port = self.io.IONotificationPortCreate(0)
        if not port:
            raise OSError("Could not create an IOKit notification port.")
        for kind in (b"IOServiceFirstMatch", b"IOServiceTerminate"):
            matching = self.io.IOServiceMatching(b"IOSerialBSDClient")
            if not matching:
                raise MemoryError("Could not allocate an IOKit matching dictionary.")
            iterator = handle()
            status = self.io.IOServiceAddMatchingNotification(
                port, kind, matching, self._callback, None, ctypes.byref(iterator)
            )
            if status:
                raise OSError(f"Could not watch serial devices. IOKit status: {status}.")
            self.iterators.append(iterator.value)
            # Draining arms the notification. Attach the queue afterward so the
            # callback never drains an iterator concurrently with this loop.
            self._drain(iterator.value)
        queue = system.dispatch_queue_create(b"dev.tinytouch.serial-ports", None)
        if not queue:
            raise OSError("Could not create a dispatch queue for serial device notifications.")
        self.io.IONotificationPortSetDispatchQueue(port, queue)

    def _drain(self, iterator: int) -> None:
        while service := self.io.IOIteratorNext(iterator):
            self.io.IOObjectRelease(service)

    def _notified(self, _context, iterator: int) -> None:
        try:
            self._drain(iterator)
            self._on_change()
        except Exception:
            pass


_PORTS = MacSerialPorts()


def comports() -> list[SerialPort]:
    return _PORTS.comports()


def watch_serial_ports(on_change: Callable[[], None]) -> SerialPortWatcher | None:
    """Return a live watcher, or None when the caller has to poll instead."""
    try:
        return SerialPortWatcher(on_change)
    except (AttributeError, OSError, MemoryError):
        return None
