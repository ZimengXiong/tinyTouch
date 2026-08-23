"""Small Security.framework wrapper for generic-password items."""

import ctypes


_SECURITY = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
_CORE_FOUNDATION = ctypes.CDLL(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
_NOT_FOUND = -25300

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


class KeychainError(RuntimeError):
    def __init__(self, operation: str, status: int):
        super().__init__(f"Keychain {operation} failed ({status})")
        self.status = status


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


def get_password(service: str, account: str) -> str | None:
    status, item, length, secret = _find(service, account, include_secret=True)
    if status == _NOT_FOUND:
        return None
    if status != 0:
        raise KeychainError("read", status)
    try:
        return ctypes.string_at(secret, length).decode("utf-8")
    finally:
        if secret:
            _SECURITY.SecKeychainItemFreeContent(None, secret)
        if item:
            _CORE_FOUNDATION.CFRelease(item)


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
    status, item, _, _ = _find(service, account, include_secret=False)
    value_raw, value_buffer = _encoded(value)
    try:
        if status == 0:
            result = _SECURITY.SecKeychainItemModifyAttributesAndData(
                item, None, len(value_raw), value_buffer
            )
        elif status == _NOT_FOUND:
            service_raw, service_buffer = _encoded(service)
            account_raw, account_buffer = _encoded(account)
            result = _SECURITY.SecKeychainAddGenericPassword(
                None, len(service_raw), service_buffer, len(account_raw), account_buffer,
                len(value_raw), value_buffer, None,
            )
        else:
            raise KeychainError("find", status)
        if result != 0:
            raise KeychainError("write", result)
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
