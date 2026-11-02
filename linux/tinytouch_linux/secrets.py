"""Secret Service storage. Never unlock or prompt from the background helper.

Use explicit attributes instead of an auto-selected keyring backend: a missing
Secret Service must not silently fall back to plaintext storage.
"""
from contextlib import contextmanager

import secretstorage


class KeychainError(RuntimeError):
    """Compatible with the shared helper's credential retry diagnostics."""
    def __init__(self, operation, status=-25308):
        super().__init__(f"Secret Service {operation} failed; unlock your desktop wallet and retry.")
        self.status = status
        self.status_name = "secret_service_unavailable"
        self.transient = True


def set_background_mode():
    # No operation in this module invokes unlock() or a Secret Service prompt.
    pass


@contextmanager
def collection():
    connection = None
    try:
        connection = secretstorage.dbus_init()
        store = secretstorage.get_default_collection(connection)
        if store.is_locked():
            raise KeychainError("access")
        yield store
    except KeychainError:
        raise
    except Exception:
        # D-Bus errors may contain secret method arguments. Do not propagate them.
        raise KeychainError("access") from None
    finally:
        if connection is not None:
            connection.close()


def attributes(service, account):
    return {"application": "tinyTouch", "service": service, "account": account}


def get_password_bytes(service, account):
    with collection() as store:
        items = list(store.search_items(attributes(service, account)))
        if not items:
            return None
        return bytearray(items[0].get_secret())


def get_password(service, account):
    value = get_password_bytes(service, account)
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    finally:
        value[:] = bytes(len(value))


def has_password(service, account):
    with collection() as store:
        return next(iter(store.search_items(attributes(service, account))), None) is not None


def set_password(service, account, value):
    with collection() as store:
        store.create_item("tinyTouch credential", attributes(service, account),
                          value.encode("utf-8"), replace=True, content_type="text/plain")


def delete_password(service, account):
    with collection() as store:
        for item in store.search_items(attributes(service, account)):
            item.delete()
