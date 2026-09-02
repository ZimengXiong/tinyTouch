"""Small Security.framework wrapper for generic-password items."""

import ctypes
import subprocess


_SECURITY = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
_CORE_FOUNDATION = ctypes.CDLL(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
_NOT_FOUND = -25300
_BACKGROUND_MODE = False

_STATUS_NAMES = {
    -25308: "interaction_not_allowed",
    -25293: "authentication_failed",
    -25300: "item_not_found",
    -25315: "interaction_required",
    -25320: "data_not_available",
    -34018: "missing_entitlement",
}

_SECURITY.SecKeychainFindGenericPassword.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
]
_SECURITY.SecKeychainFindGenericPassword.restype = ctypes.c_int32
_SECURITY.SecKeychainAddGenericPassword.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
_SECURITY.SecKeychainAddGenericPassword.restype = ctypes.c_int32
_SECURITY.SecKeychainItemModifyAttributesAndData.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
]
_SECURITY.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
_SECURITY.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
_SECURITY.SecKeychainItemDelete.restype = ctypes.c_int32
_SECURITY.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_SECURITY.SecKeychainItemFreeContent.restype = ctypes.c_int32
_CORE_FOUNDATION.CFRelease.argtypes = [ctypes.c_void_p]


_SECURITY.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_bool]
_SECURITY.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
_SECURITY.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
_SECURITY.SecKeychainCopyDefault.restype = ctypes.c_int32
_SECURITY.SecKeychainUnlock.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_bool,
]
_SECURITY.SecKeychainUnlock.restype = ctypes.c_int32


class KeychainError(RuntimeError):
    def __init__(self, operation: str, status: int):
        name = _STATUS_NAMES.get(status, "unknown")
        super().__init__(f"Keychain {operation} failed ({name}, {status})")
        self.status = status
        self.status_name = name
        self.transient = status in {-25308, -25315, -25320}


def disable_user_interaction() -> None:
    """Forbid Keychain UI so callers fail in the terminal instead of prompting."""
    global _BACKGROUND_MODE
    status = _SECURITY.SecKeychainSetUserInteractionAllowed(False)
    if status != 0:
        raise KeychainError("disable interaction", status)
    _BACKGROUND_MODE = True


def set_background_mode() -> None:
    """Disable Keychain UI in the unattended helper process."""
    disable_user_interaction()


def unlock_default_keychain(password: bytearray) -> None:
    """Unlock the login Keychain from a caller-provided terminal password."""
    if not password:
        raise KeychainError("unlock", -1)
    keychain = ctypes.c_void_p()
    status = _SECURITY.SecKeychainCopyDefault(ctypes.byref(keychain))
    if status != 0 or not keychain:
        raise KeychainError("default keychain", status)
    try:
        buffer = (ctypes.c_ubyte * len(password)).from_buffer(password)
        status = _SECURITY.SecKeychainUnlock(keychain, len(password), buffer, True)
        if status != 0:
            raise KeychainError("unlock", status)
    finally:
        _CORE_FOUNDATION.CFRelease(keychain)


def _encoded(value: str) -> tuple[bytes, ctypes.Array]:
    raw = value.encode("utf-8")
    return raw, ctypes.create_string_buffer(raw, len(raw) + 1)


def _find(service: str, account: str, *, include_secret: bool):
    service_raw, service_buffer = _encoded(service)
    account_raw, account_buffer = _encoded(account)
    length = ctypes.c_uint32(0)
    secret = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = _SECURITY.SecKeychainFindGenericPassword(
        None, len(service_raw), service_buffer, len(account_raw), account_buffer,
        ctypes.byref(length) if include_secret else None,
        ctypes.byref(secret) if include_secret else None, ctypes.byref(item),
    )
    return status, item, length.value, secret


def get_password_bytes(service: str, account: str) -> bytearray | None:
    """Copy a secret into caller-wipeable memory."""
    if _BACKGROUND_MODE:
        # The login Keychain can be unlocked while denying a newly rebuilt
        # LaunchAgent executable access through its per-process ACL. The
        # system security tool is stable across CLI replacements and reads the
        # same unlocked item without presenting UI.
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return bytearray(result.stdout.removesuffix(b"\n"))
        if result.returncode != 44:
            raise KeychainError("read", -25293)
        return None
    status, item, length, secret = _find(service, account, include_secret=True)
    if status == _NOT_FOUND:
        return None
    if status != 0:
        raise KeychainError("read", status)
    try:
        return bytearray(ctypes.string_at(secret, length))
    finally:
        if secret:
            _SECURITY.SecKeychainItemFreeContent(None, secret)
        if item:
            _CORE_FOUNDATION.CFRelease(item)


def get_password(service: str, account: str) -> str | None:
    value = get_password_bytes(service, account)
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    finally:
        value[:] = b"\x00" * len(value)


def has_password(service: str, account: str) -> bool:
    status, item, _, _ = _find(service, account, include_secret=False)
    if item:
        _CORE_FOUNDATION.CFRelease(item)
    if status == _NOT_FOUND:
        return False
    if status != 0:
        raise KeychainError("find", status)
    return True


def set_password(service: str, account: str, value: str) -> None:
    """Store a password readable by both the CLI and its LaunchAgent.

    Do not attach a per-executable ACL. The CLI is a replaceable standalone
    executable while the helper is a separate LaunchAgent executable. An ACL
    written for one of those paths makes the other fail without a usable UI.
    Recreating an old item deliberately removes any ACL created by an older
    tinyTouch version.
    """
    status, item, _, _ = _find(service, account, include_secret=False)
    value_raw, value_buffer = _encoded(value)
    try:
        if status == 0:
            result = _SECURITY.SecKeychainItemDelete(item)
            if result != 0:
                raise KeychainError("replace", result)
        elif status != _NOT_FOUND:
            raise KeychainError("find", status)
        service_raw, service_buffer = _encoded(service)
        account_raw, account_buffer = _encoded(account)
        new_item = ctypes.c_void_p()
        result = _SECURITY.SecKeychainAddGenericPassword(
            None, len(service_raw), service_buffer, len(account_raw), account_buffer,
            len(value_raw), value_buffer, ctypes.byref(new_item),
        )
        if result != 0:
            raise KeychainError("write", result)
        if not new_item:
            raise KeychainError("write", -1)
        _CORE_FOUNDATION.CFRelease(new_item)
    finally:
        if item:
            _CORE_FOUNDATION.CFRelease(item)


def delete_password(service: str, account: str) -> bool:
    status, item, _, _ = _find(service, account, include_secret=False)
    if status == _NOT_FOUND:
        return False
    if status != 0:
        raise KeychainError("find", status)
    try:
        result = _SECURITY.SecKeychainItemDelete(item)
        if result != 0:
            raise KeychainError("delete", result)
        return True
    finally:
        if item:
            _CORE_FOUNDATION.CFRelease(item)
