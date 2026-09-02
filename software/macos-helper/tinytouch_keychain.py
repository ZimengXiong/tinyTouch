"""Small Security.framework wrapper for generic-password items."""

import ctypes


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


_SECURITY.SecAccessCreate.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
]
_SECURITY.SecAccessCreate.restype = ctypes.c_int32
_SECURITY.SecKeychainItemSetAccess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_SECURITY.SecKeychainItemSetAccess.restype = ctypes.c_int32
_SECURITY.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_bool]
_SECURITY.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
_CORE_FOUNDATION.CFStringCreateWithCString.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
]
_CORE_FOUNDATION.CFStringCreateWithCString.restype = ctypes.c_void_p


class KeychainError(RuntimeError):
    def __init__(self, operation: str, status: int):
        name = _STATUS_NAMES.get(status, "unknown")
        super().__init__(f"Keychain {operation} failed ({name}, {status})")
        self.status = status
        self.status_name = name
        self.transient = status in {-25308, -25315, -25320}


def set_background_mode() -> None:
    """Disable Keychain UI in the unattended helper process."""
    global _BACKGROUND_MODE
    status = _SECURITY.SecKeychainSetUserInteractionAllowed(False)
    if status != 0:
        raise KeychainError("disable interaction", status)
    _BACKGROUND_MODE = True


def _encoded(value: str) -> tuple[bytes, ctypes.Array]:
    raw = value.encode("utf-8")
    return raw, ctypes.create_string_buffer(raw, len(raw) + 1)


def _set_calling_app_access(item, service: str) -> None:
    """Restrict the item to the calling application and verify the ACL update."""
    if not item:
        raise KeychainError("set access", -1)
    service_bytes = service.encode("utf-8")
    cf_desc = _CORE_FOUNDATION.CFStringCreateWithCString(None, service_bytes, 0x08000100)
    if not cf_desc:
        raise KeychainError("set access", -1)
    access = ctypes.c_void_p()
    try:
        status = _SECURITY.SecAccessCreate(cf_desc, None, ctypes.byref(access))
    finally:
        _CORE_FOUNDATION.CFRelease(cf_desc)
    if status != 0 or not access:
        raise KeychainError("create access", status)
    try:
        status = _SECURITY.SecKeychainItemSetAccess(item, access)
        if status != 0:
            raise KeychainError("set access", status)
    finally:
        _CORE_FOUNDATION.CFRelease(access)


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
    status, item, _, _ = _find(service, account, include_secret=False)
    value_raw, value_buffer = _encoded(value)
    try:
        if status == 0:
            result = _SECURITY.SecKeychainItemModifyAttributesAndData(
                item, None, len(value_raw), value_buffer
            )
            if result != 0:
                raise KeychainError("write", result)
            _set_calling_app_access(item, service)
            return
        if status == _NOT_FOUND:
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
            try:
                _set_calling_app_access(new_item, service)
            finally:
                _CORE_FOUNDATION.CFRelease(new_item)
            return
        raise KeychainError("find", status)
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
