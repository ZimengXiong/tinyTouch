"""Discover macOS serial devices without retaining native registry objects."""

from __future__ import annotations

import ctypes
from typing import NamedTuple


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
            raise MemoryError("Cannot allocate an IOKit property key")
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
            raise MemoryError("Cannot allocate an IOKit matching dictionary")
        iterator = ctypes.c_uint32()
        # IOServiceGetMatchingServices consumes the dictionary, including on error.
        status = self.io.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator))
        try:
            if status:
                raise OSError(f"Cannot enumerate serial devices (IOKit status {status})")
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


_PORTS = MacSerialPorts()


def comports() -> list[SerialPort]:
    return _PORTS.comports()
