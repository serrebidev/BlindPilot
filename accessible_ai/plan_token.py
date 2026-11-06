"""The non-interactive plan-token command for the Codex backend.

Takes one ChatGPT-plan Account id, prints a current access token to stdout, and
exits without opening a window. It reads the same stored sign-in the chat half
uses and refreshes from the stored refresh token. Only the token goes to
stdout; every diagnostic goes to stderr. The access token never reaches the
command line and is never written to disk.
"""

from __future__ import annotations

import os

from accessible_ai.providers import chatgpt
from accessible_ai.storage.credentials import CredentialStore, FileCredentialStore
from accessible_ai.storage.paths import app_data_dir

FLAG = "--print-plan-token"


def _credential_store() -> CredentialStore:
    # A test home keeps its records in files, so a test never reads the keychain.
    if os.environ.get("BLINDPILOT_HOME"):
        return FileCredentialStore(app_data_dir() / "credentials")
    return CredentialStore()


def _write(fd: int, text: str) -> None:
    try:
        os.write(fd, (text + "\n").encode("utf-8"))
    except OSError:
        # No such descriptor, or it was closed under us: nothing more to do.
        pass


def _usage() -> str:
    return f"usage: blind_pilot.py {FLAG} <account-id>"


def _fail(message: str) -> int:
    _write(2, f"error: {message}")
    return 1


def main(argv: list[str]) -> int:
    if FLAG not in argv:
        _write(2, f"error: missing {FLAG}")
        _write(2, _usage())
        return 2
    index = argv.index(FLAG)
    rest = argv[index + 1 :]
    if len(rest) != 1 or not rest[0].strip():
        _write(2, f"error: {FLAG} needs exactly one Account id")
        _write(2, _usage())
        return 2
    try:
        account_id = int(rest[0])
    except ValueError:
        _write(2, f"error: {rest[0]} is not an Account id")
        _write(2, _usage())
        return 2
    try:
        token = chatgpt.access_token(_credential_store(), account_id)
    except chatgpt.ChatGPTError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - the command must report, not traceback
        return _fail(str(exc))
    _write(1, token)
    return 0
