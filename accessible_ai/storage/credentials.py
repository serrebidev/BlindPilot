from __future__ import annotations

import os


SERVICE_NAME = "BlindPilotChat"
LEGACY_SERVICE_NAME = "AccessibleAI"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def _username(self, account_id: int) -> str:
        return f"account:{account_id}:api_key"

    def _target(self, account_id: int) -> str:
        return f"{SERVICE_NAME}:{self._username(account_id)}"

    def get_api_key(self, account_id: int) -> str:
        if os.name == "nt":
            current = self._windows_read(self._target(account_id))
            if current:
                return current
            legacy = self._windows_read(f"{LEGACY_SERVICE_NAME}:{self._username(account_id)}")
            if legacy:
                self._windows_write(self._target(account_id), legacy)
            return legacy
        current = self._fallback_read(self._username(account_id), SERVICE_NAME)
        if current:
            return current
        legacy = self._fallback_read(self._username(account_id), LEGACY_SERVICE_NAME)
        if legacy:
            self._fallback_write(self._username(account_id), legacy)
        return legacy

    def set_api_key(self, account_id: int, api_key: str) -> None:
        if os.name == "nt":
            self._windows_write(self._target(account_id), api_key)
            return
        self._fallback_write(self._username(account_id), api_key)

    def delete_api_key(self, account_id: int) -> None:
        if os.name == "nt":
            self._windows_delete(self._target(account_id))
            self._windows_delete(f"{LEGACY_SERVICE_NAME}:{self._username(account_id)}")
            return
        self._fallback_delete(self._username(account_id))
        self._fallback_delete(self._username(account_id), LEGACY_SERVICE_NAME)

    @staticmethod
    def _fallback_read(username: str, service_name: str = SERVICE_NAME) -> str:
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError:
            return ""
        try:
            return keyring.get_password(service_name, username) or ""
        except KeyringError as exc:
            raise CredentialStoreError(str(exc)) from exc

    @staticmethod
    def _fallback_write(username: str, api_key: str) -> None:
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise CredentialStoreError(
                "No credential backend is available on this platform."
            ) from exc
        try:
            keyring.set_password(SERVICE_NAME, username, api_key)
        except KeyringError as exc:
            raise CredentialStoreError(str(exc)) from exc

    @staticmethod
    def _fallback_delete(username: str, service_name: str = SERVICE_NAME) -> None:
        try:
            import keyring
            from keyring.errors import KeyringError, PasswordDeleteError
        except ImportError:
            return
        try:
            try:
                keyring.delete_password(service_name, username)
            except PasswordDeleteError:
                pass
        except KeyringError as exc:
            raise CredentialStoreError(str(exc)) from exc

    @staticmethod
    def _windows_read(target: str) -> str:
        import ctypes
        from ctypes import wintypes

        CRED_TYPE_GENERIC = 1
        ERROR_NOT_FOUND = 1168

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_read = advapi32.CredReadW
        cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi32.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        cred_free.restype = None

        credential_ptr = ctypes.POINTER(CREDENTIALW)()
        if not cred_read(target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential_ptr)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return ""
            raise CredentialStoreError(
                f"Windows Credential Manager read failed with error {error}."
            )

        try:
            credential = credential_ptr.contents
            if credential.CredentialBlobSize == 0 or not credential.CredentialBlob:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            try:
                return raw.decode("utf-16-le")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace")
        finally:
            cred_free(credential_ptr)

    @staticmethod
    def _windows_write(target: str, api_key: str) -> None:
        import ctypes
        from ctypes import wintypes

        CRED_TYPE_GENERIC = 1
        CRED_PERSIST_LOCAL_MACHINE = 2

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_write = advapi32.CredWriteW
        cred_write.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        cred_write.restype = wintypes.BOOL

        blob = api_key.encode("utf-16-le")
        blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else None
        credential = CREDENTIALW()
        credential.Flags = 0
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "BlindPilot chat API key"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = (
            ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
            if blob_buffer is not None
            else None
        )
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = target

        if not cred_write(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CredentialStoreError(
                f"Windows Credential Manager write failed with error {error}."
            )

    @staticmethod
    def _windows_delete(target: str) -> None:
        import ctypes
        from ctypes import wintypes

        CRED_TYPE_GENERIC = 1
        ERROR_NOT_FOUND = 1168

        advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        cred_delete = advapi32.CredDeleteW
        cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        cred_delete.restype = wintypes.BOOL

        if not cred_delete(target, CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return
            raise CredentialStoreError(
                f"Windows Credential Manager delete failed with error {error}."
            )
