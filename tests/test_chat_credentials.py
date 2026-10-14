"""Windows Credential Manager writes keep chat keys across logon sessions."""

import ctypes
from types import SimpleNamespace

from accessible_ai.storage.credentials import CredentialStore


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
