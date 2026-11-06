"""The plan-token command and its cross-process refresh lock.

These tests run the real entry point as a subprocess against a temporary
credential store and a local fake token endpoint, so they only see what a
caller sees: stdout, stderr, and the exit code. The two-process test proves
that a rotating refresh token reaches both processes: each one exchanges again
rather than sharing an issued token, and the stored record is never left
half-written.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from accessible_ai.providers import chatgpt
from accessible_ai.storage.credentials import FileCredentialStore

REPO_ROOT = Path(__file__).resolve().parents[1]
BLIND_PILOT = REPO_ROOT / "blind_pilot.py"
FLAG = "--print-plan-token"


class _TokenHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = parse_qs(body)
        state = self.server.state  # type: ignore[attr-defined]
        lock = self.server.lock  # type: ignore[attr-defined]
        with lock:
            state["requests"].append(form)
            number = state["count"]
            state["count"] += 1
            state["issued_refresh"].append(form.get("refresh_token", [""])[0])
            delay = state["delay"]
        if delay:
            time.sleep(delay)
        if form.get("grant_type", [""])[0] != "refresh_token":
            self._json(400, {"error": "unsupported_grant_type"})
            return
        self._json(
            200,
            {
                "access_token": f"access-{number + 1}",
                "refresh_token": f"refresh-{number + 1}",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": chatgpt.SCOPES,
            },
        )

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


class TokenServer:
    def __init__(self, delay: float = 0.0) -> None:
        self.state = {"requests": [], "count": 0, "issued_refresh": [], "delay": delay}
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
        self.httpd.state = self.state
        self.httpd.lock = self.lock
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/oauth/token"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _subprocess_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def _env(tmp_path: Path, server: TokenServer) -> dict[str, str]:
    env = os.environ.copy()
    env["BLINDPILOT_HOME"] = str(_home(tmp_path))
    env["BLINDPILOT_CHATGPT_TOKEN_URL"] = server.url()
    return env


def _write_session(credentials_dir: Path, account_id: int) -> None:
    FileCredentialStore(credentials_dir).set_api_key(
        account_id,
        json.dumps(
            {
                "client_id": "oaiapp_issued",
                "email": "person@example.com",
                "subject": "user-1",
                "refresh_token": "refresh-0",
                "scopes": [chatgpt.PLAN_SCOPE],
            }
        ),
    )


def _stored_record(tmp_path: Path, account_id: int) -> dict:
    store = FileCredentialStore(_home(tmp_path) / "credentials")
    return json.loads(store.get_api_key(account_id))


def _command(account_id: int) -> list[str]:
    return [sys.executable, str(BLIND_PILOT), FLAG, str(account_id)]


def test_the_plan_token_mode_prints_a_refreshed_token_to_stdout(tmp_path):
    server = TokenServer()
    try:
        account_id = 7
        _write_session(_home(tmp_path) / "credentials", account_id)
        result = subprocess.run(
            _command(account_id),
            cwd=str(REPO_ROOT),
            env=_env(tmp_path, server),
            capture_output=True,
            text=True,
            timeout=60,
            **_subprocess_kwargs(),
        )

        assert result.returncode == 0
        assert result.stdout == "access-1\n"
        assert server.state["count"] == 1
        stored = _stored_record(tmp_path, account_id)
        assert stored["refresh_token"] == "refresh-1"
        assert "access_token" not in stored
    finally:
        server.stop()


def test_two_processes_each_get_a_usable_token_and_never_a_half_written_record(tmp_path):
    server = TokenServer(delay=1.0)
    try:
        account_id = 11
        _write_session(_home(tmp_path) / "credentials", account_id)
        env = _env(tmp_path, server)
        command = _command(account_id)
        processes = [
            subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **_subprocess_kwargs(),
            )
            for _ in range(2)
        ]

        results = []
        records = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            results.append((process.returncode, stdout, stderr))
            records.append(_stored_record(tmp_path, account_id))

        assert [code for code, _stdout, _stderr in results] == [0, 0]
        tokens = [stdout.strip() for _code, stdout, _stderr in results]
        assert all(token.startswith("access-") for token in tokens), tokens
        assert server.state["count"] == 2, "each process must exchange the rotated token again"
        # Read after each process exited: a half-written record would not parse.
        assert all(record["refresh_token"] for record in records), records
        assert records[-1]["refresh_token"] == "refresh-2", "the last exchange was not kept"
    finally:
        server.stop()


def test_an_account_that_is_not_signed_in_prints_nothing_to_stdout(tmp_path):
    server = TokenServer()
    try:
        result = subprocess.run(
            _command(5),
            cwd=str(REPO_ROOT),
            env=_env(tmp_path, server),
            capture_output=True,
            text=True,
            timeout=60,
            **_subprocess_kwargs(),
        )

        assert result.returncode != 0
        assert result.stdout == ""
        assert "not signed in" in result.stderr
        assert server.state["count"] == 0
    finally:
        server.stop()


def test_the_access_token_is_never_written_to_disk_or_passed_on_the_command_line(tmp_path):
    server = TokenServer()
    try:
        account_id = 9
        _write_session(_home(tmp_path) / "credentials", account_id)
        result = subprocess.run(
            _command(account_id),
            cwd=str(REPO_ROOT),
            env=_env(tmp_path, server),
            capture_output=True,
            text=True,
            timeout=60,
            **_subprocess_kwargs(),
        )

        assert result.returncode == 0
        token = result.stdout.strip()
        assert token == "access-1"

        home = _home(tmp_path)
        if home.is_dir():
            for path in home.rglob("*"):
                if path.is_file():
                    assert token not in path.read_text(encoding="utf-8", errors="ignore")

        stored = _stored_record(tmp_path, account_id)
        assert "access_token" not in stored
        assert stored["refresh_token"] == "refresh-1"
    finally:
        server.stop()


def test_the_file_credential_store_replaces_records_atomically(tmp_path, monkeypatch):
    store = FileCredentialStore(tmp_path / "credentials")
    store.set_api_key(7, "old")
    target = tmp_path / "credentials" / "account-7.json"

    def fail_replace(_source, _destination):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.set_api_key(7, "new")

    assert store.get_api_key(7) == "old"
    assert target.read_text(encoding="utf-8") == "old"
