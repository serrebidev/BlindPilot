"""Windows Credential Manager writes keep chat keys across logon sessions.

The keyring path every other platform takes is covered at the bottom: a
stand-in module stands in for `keyring`, so the legacy migration and the
failure reporting are tested without a real backend being installed.
"""

import ctypes
import sys
import types
from types import SimpleNamespace

import pytest

import accessible_ai.storage.credentials as credentials_module
from accessible_ai.storage.credentials import (
    LEGACY_SERVICE_NAME,
    SERVICE_NAME,
    CredentialStore,
    CredentialStoreError,
)


def test_windows_chat_key_uses_local_machine_persistence(monkeypatch):
    persisted = []

    def write(credential, _flags):
        persisted.append(credential._obj.Persist)
        return 1

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(CredWriteW=write), raising=False
    )

    CredentialStore._windows_write("BlindPilotChat:test", "synthetic-key")

    assert persisted == [2]


def _install_keyring(monkeypatch):
    """A `keyring` that answers as the test asks it to, on any platform.

    Returns the module and its error classes, so a test can make a call
    raise the exception `keyring` itself would raise.
    """
    errors = types.ModuleType("keyring.errors")
    errors.KeyringError = type("KeyringError", (Exception,), {})
    errors.PasswordDeleteError = type("PasswordDeleteError", (errors.KeyringError,), {})
    keyring = types.ModuleType("keyring")
    keyring.get_password = lambda _service, _username: None
    keyring.set_password = lambda _service, _username, _key: None
    keyring.delete_password = lambda _service, _username: None
    keyring.errors = errors
    monkeypatch.setitem(sys.modules, "keyring", keyring)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    # The Windows branch is the one this machine would otherwise take.
    monkeypatch.setattr(credentials_module, "os", SimpleNamespace(name="posix"))
    return keyring, errors


def test_a_key_under_the_legacy_service_is_moved_to_the_current_one(monkeypatch):
    written: list[tuple[str, str, str]] = []
    keyring, _errors = _install_keyring(monkeypatch)
    keyring.get_password = lambda service, _username: (
        "legacy-key" if service == LEGACY_SERVICE_NAME else ""
    )
    keyring.set_password = lambda service, username, key: written.append((service, username, key))

    assert CredentialStore().get_api_key(7) == "legacy-key"
    assert written == [(SERVICE_NAME, "account:7:api_key", "legacy-key")]


def test_a_keyring_failure_is_reported_in_words_somebody_can_act_on(monkeypatch):
    keyring, errors = _install_keyring(monkeypatch)

    def on_get(_service, _username):
        raise errors.KeyringError("backend unavailable")

    keyring.get_password = on_get

    with pytest.raises(CredentialStoreError) as excinfo:
        CredentialStore().get_api_key(7)

    assert "backend unavailable" in str(excinfo.value)


def test_deleting_a_key_that_is_not_there_is_not_an_error(monkeypatch):
    keyring, errors = _install_keyring(monkeypatch)

    def on_delete(_service, _username):
        raise errors.PasswordDeleteError("no such key")

    keyring.delete_password = on_delete

    CredentialStore().delete_api_key(7)


def test_a_platform_with_no_keyring_says_so_when_a_key_is_stored(monkeypatch):
    _install_keyring(monkeypatch)
    monkeypatch.setitem(sys.modules, "keyring", None)

    with pytest.raises(CredentialStoreError) as excinfo:
        CredentialStore().set_api_key(7, "k")

    assert "No credential backend" in str(excinfo.value)
