"""Small Security.framework wrapper for generic-password items."""

import ctypes
import getpass
import warnings
import plistlib
import re
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


def can_read_password(service: str, account: str) -> bool:
    """Check unattended access and wipe the temporary credential copy."""
    status = _SECURITY.SecKeychainSetUserInteractionAllowed(False)
    if status != 0:
        raise KeychainError("disable interaction", status)
    value = None
    try:
        value = get_password_bytes(service, account)
        return value is not None
    except KeychainError as exc:
        if exc.status in {-25293, -25308, -25315, -25320}:
            return False
        raise
    finally:
        if value is not None:
            value[:] = b"\x00" * len(value)
        _SECURITY.SecKeychainSetUserInteractionAllowed(not _BACKGROUND_MODE)


def prompt_keychain_password() -> bytearray:
    """Read the Keychain password from the terminal without echoing it."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return bytearray(
                getpass.getpass("Login Keychain password: ").encode("utf-8")
            )
        except (getpass.GetPassWarning, EOFError) as exc:
            raise RuntimeError(
                "Run tinytouch repair in an interactive terminal."
            ) from exc


def authorize_executable(
    service: str,
    account: str,
    executable: str,
    *,
    password_provider=prompt_keychain_password,
) -> None:
    """Authorize a stable signed CLI without replacing its saved credential."""
    metadata = subprocess.run(
        ["codesign", "-d", "--verbose=2", executable],
        check=True,
        capture_output=True,
        text=True,
    )
    team = re.search(r"^TeamIdentifier=([A-Z0-9]{10})$", metadata.stderr, re.MULTILINE)
    if team is None:
        raise RuntimeError("Repair requires a certificate-signed tinyTouch CLI")
    requirement = (
        'identifier "com.tinytouch.cli" and anchor apple generic '
        f'and certificate leaf[subject.OU] = "{team[1]}"'
    )
    subprocess.run(
        ["codesign", "--verify", "--strict", "-R", "=" + requirement, executable],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    status, item, _, _ = _find(service, account, include_secret=False)
    if status != 0:
        raise KeychainError("find", status)
    access, trusted = ctypes.c_void_p(), ctypes.c_void_p()
    pointer = ctypes.c_void_p
    output = ctypes.POINTER(pointer)
    for name, arguments, result in (
        ("SecKeychainItemCopyAccess", [pointer, output], ctypes.c_int32),
        ("SecKeychainItemSetAccess", [pointer, pointer], ctypes.c_int32),
        (
            "SecKeychainItemSetAccessWithPassword",
            [pointer, pointer, ctypes.c_uint32, pointer],
            ctypes.c_int32,
        ),
        (
            "SecTrustedApplicationCreateFromPath",
            [ctypes.c_char_p, output],
            ctypes.c_int32,
        ),
        ("SecAccessCopyMatchingACLList", [pointer, pointer], pointer),
        (
            "SecACLCopyContents",
            [pointer, output, output, ctypes.POINTER(ctypes.c_uint16)],
            ctypes.c_int32,
        ),
        (
            "SecACLSetContents",
            [pointer, pointer, pointer, ctypes.c_uint16],
            ctypes.c_int32,
        ),
    ):
        function = getattr(_SECURITY, name)
        function.argtypes, function.restype = arguments, result
    for name, arguments, result in (
        ("CFArrayGetCount", [pointer], ctypes.c_ssize_t),
        ("CFArrayGetValueAtIndex", [pointer, ctypes.c_ssize_t], pointer),
        ("CFArrayCreateMutableCopy", [pointer, ctypes.c_ssize_t, pointer], pointer),
        ("CFArrayAppendValue", [pointer, pointer], None),
        ("CFStringGetLength", [pointer], ctypes.c_ssize_t),
        (
            "CFStringGetCString",
            [pointer, pointer, ctypes.c_ssize_t, ctypes.c_uint32],
            ctypes.c_bool,
        ),
        (
            "CFStringCreateWithCString",
            [pointer, ctypes.c_char_p, ctypes.c_uint32],
            pointer,
        ),
    ):
        function = getattr(_CORE_FOUNDATION, name)
        function.argtypes, function.restype = arguments, result

    def check(result):
        if result != 0:
            raise KeychainError("authorize executable", result)

    try:
        check(_SECURITY.SecKeychainItemCopyAccess(item, ctypes.byref(access)))
        check(
            _SECURITY.SecTrustedApplicationCreateFromPath(
                executable.encode(), ctypes.byref(trusted)
            )
        )
        for authorization in (
            "kSecACLAuthorizationPartitionID",
            "kSecACLAuthorizationDecrypt",
        ):
            tag = pointer.in_dll(_SECURITY, authorization)
            acls = _SECURITY.SecAccessCopyMatchingACLList(access, tag)
            if not acls:
                if authorization == "kSecACLAuthorizationPartitionID":
                    continue
                raise KeychainError("find credential ACL", -1)
            changed = False
            try:
                for index in range(_CORE_FOUNDATION.CFArrayGetCount(acls)):
                    acl = _CORE_FOUNDATION.CFArrayGetValueAtIndex(acls, index)
                    applications, description = pointer(), pointer()
                    selector = ctypes.c_uint16()
                    updated_apps = updated_description = None
                    try:
                        check(
                            _SECURITY.SecACLCopyContents(
                                acl,
                                ctypes.byref(applications),
                                ctypes.byref(description),
                                ctypes.byref(selector),
                            )
                        )
                        if authorization == "kSecACLAuthorizationDecrypt":
                            # Preserve all existing restrictions and trusted applications.
                            if not applications:
                                continue
                            updated_apps = _CORE_FOUNDATION.CFArrayCreateMutableCopy(
                                None, 0, applications
                            )
                            _CORE_FOUNDATION.CFArrayAppendValue(updated_apps, trusted)
                        else:
                            size = (
                                _CORE_FOUNDATION.CFStringGetLength(description) * 4 + 1
                            )
                            buffer = ctypes.create_string_buffer(size)
                            if not _CORE_FOUNDATION.CFStringGetCString(
                                description, buffer, size, 0x08000100
                            ):
                                raise KeychainError("read credential partition ACL", -1)
                            partitions = plistlib.loads(
                                bytes.fromhex(buffer.value.decode())
                            )
                            values = partitions["Partitions"]
                            team_partition = "teamid:" + team[1]
                            if team_partition in values:
                                continue
                            values.append(team_partition)
                            encoded = (
                                plistlib.dumps(partitions, fmt=plistlib.FMT_XML)
                                .hex()
                                .encode()
                            )
                            updated_description = (
                                _CORE_FOUNDATION.CFStringCreateWithCString(
                                    None, encoded, 0x08000100
                                )
                            )
                        check(
                            _SECURITY.SecACLSetContents(
                                acl,
                                updated_apps or applications,
                                updated_description or description,
                                selector,
                            )
                        )
                        changed = True
                    finally:
                        for reference in (
                            updated_apps,
                            updated_description,
                            applications,
                            description,
                        ):
                            if reference:
                                _CORE_FOUNDATION.CFRelease(reference)
            finally:
                _CORE_FOUNDATION.CFRelease(acls)
            if not changed:
                continue
            if authorization == "kSecACLAuthorizationPartitionID":
                # An old ad hoc partition rejects this process before macOS can
                # show the application-access dialog. Authorize the new team first.
                password = password_provider()
                try:
                    buffer = (ctypes.c_ubyte * len(password)).from_buffer(password)
                    status = _SECURITY.SecKeychainItemSetAccessWithPassword(
                        item, access, len(password), buffer
                    )
                    if status != 0:
                        raise KeychainError("authorize signing identity", status)
                finally:
                    password[:] = b"\x00" * len(password)
                # Reload before editing the decrypt ACL. Combining both changes
                # makes Security.framework reject legacy items with errSecAuthFailed.
                _CORE_FOUNDATION.CFRelease(access)
                access = pointer()
                check(_SECURITY.SecKeychainItemCopyAccess(item, ctypes.byref(access)))
            else:
                check(_SECURITY.SecKeychainSetUserInteractionAllowed(True))
                try:
                    check(_SECURITY.SecKeychainItemSetAccess(item, access))
                finally:
                    check(
                        _SECURITY.SecKeychainSetUserInteractionAllowed(
                            not _BACKGROUND_MODE
                        )
                    )

    finally:
        for reference in (trusted, access, item):
            if reference:
                _CORE_FOUNDATION.CFRelease(reference)


def set_password(service: str, account: str, value: str) -> None:
    """Update a saved password without replacing its item or access permissions.

    New items trust the creating executable. Existing items keep any permissions
    granted during repair. Callers must repair denied access before updating.
    """
    value_raw, value_buffer = _encoded(value)
    item = ctypes.c_void_p()
    try:
        status, item, _, _ = _find(service, account, include_secret=False)
        if status == 0:
            result = _SECURITY.SecKeychainItemModifyAttributesAndData(
                item, None, len(value_raw), value_buffer
            )
            if result != 0:
                raise KeychainError("update", result)
            return
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
        ctypes.memset(value_buffer, 0, ctypes.sizeof(value_buffer))
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
