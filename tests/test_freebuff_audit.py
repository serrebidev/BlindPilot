"""Regression cases found while auditing the FreeBuff integration."""

import ctypes
import json
import platform
import sys
import threading
from types import SimpleNamespace

import pytest

import agent_backends as ab
import session_history as history
from accessible_ai.storage.credentials import CredentialStore


def _worker(**callbacks):
    defaults = {
        "on_session": lambda _: None,
        "on_started": lambda: None,
        "on_activity": lambda *_: None,
        "on_complete": lambda _: None,
        "on_failed": lambda _: None,
        "on_done": lambda: None,
    }
    defaults.update(callbacks)
    return ab.FreebuffWorker("do the work", None, ".", "default", **defaults)


@pytest.mark.parametrize("mtime", ["broken", [1], {"bad": 1}])
def test_corrupt_catalog_timestamp_is_a_cache_miss(monkeypatch, tmp_path, mtime):
    cache = tmp_path / "catalog.json"
    cache.write_text(json.dumps({"path": "binary", "size": 10, "mtime": mtime, "models": ["a/b"]}))
    monkeypatch.setattr(ab, "_freebuff_catalog_cache_path", lambda: cache)
    assert ab._read_freebuff_catalog_cache(("binary", 1.0, 10)) == []


def test_the_catalog_cache_is_written_beside_the_file_and_moved_into_place(monkeypatch, tmp_path):
    """A read landing mid-write must never see a half-copied catalog."""
    cache = tmp_path / "freebuff-catalog.json"
    monkeypatch.setattr(ab, "_freebuff_catalog_cache_path", lambda: cache)

    ab._write_freebuff_catalog_cache(("binary", 1.5, 10), ["a/b"])

    assert ab._read_freebuff_catalog_cache(("binary", 1.5, 10)) == ["a/b"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_keychain_credentials_count_as_signed_in_without_rewriting_them(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.Path, "home", classmethod(lambda _: tmp_path))
    monkeypatch.setattr(ab.platform, "system", lambda: "Windows")
    credential = tmp_path / ".config/manicode/credentials.json"
    credential.parent.mkdir(parents=True)
    contents = json.dumps(
        {
            "default": {
                "email": "test@example.com",
                "tokenStore": "keychain",
                "fingerprintId": "id",
                "fingerprintHash": "hash",
            }
        }
    )
    credential.write_text(contents)
    reads = []

    def read(target, encoding="utf-16-le"):
        reads.append((target, encoding))
        return "stored-token"

    monkeypatch.setattr(CredentialStore, "_windows_read", staticmethod(read))
    account = ab._freebuff_account()
    assert ab._freebuff_signed_in(account)
    assert account["authToken"] == "stored-token"
    assert reads == [(f"freebuff-cli/{credential}", "utf-8")]
    assert credential.read_text() == contents


def test_current_credentials_do_not_require_optional_fingerprints():
    assert ab._freebuff_signed_in({"email": "test@example.com", "authToken": "token"})


def test_submitting_multiline_unicode_uses_bracketed_paste_then_enter():
    worker = _worker()
    writes = []
    worker._write = lambda text: writes.append(text) or True
    text = "FIRST Café Ω 🙂\nSECOND"
    assert worker._submit_text(text)
    assert writes == [f"\x1b[200~{text}\x1b[201~", "\r"]


def test_freebuff_does_not_claim_a_busy_terminal_accepted_steering():
    worker = _worker()
    worker._accepting_input.set()
    writes = []
    worker._write = lambda text: writes.append(text) or True
    assert not worker.steer("changed instruction")
    assert writes == []


def test_macos_keychain_uses_freebuffs_service_and_credential_path(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.platform, "system", lambda: "Darwin")
    seen = []

    def read(name, service):
        seen.append((service, name))
        return "stored-token"

    monkeypatch.setattr(CredentialStore, "_fallback_read", staticmethod(read))
    path = tmp_path / "credentials.json"
    assert ab._freebuff_keychain_token(path) == "stored-token"
    assert seen == [("freebuff-cli", str(path))]


@pytest.mark.parametrize("locked", [False, True])
def test_linux_secure_store_uses_buns_attributes_and_closes_the_connection(
    monkeypatch, tmp_path, locked
):
    monkeypatch.setattr(ab.platform, "system", lambda: "Linux")
    seen = []
    connection = SimpleNamespace(close=lambda: seen.append("closed"))
    item = SimpleNamespace(is_locked=lambda: locked, get_secret=lambda: b"stored-token")

    def search(conn, attributes):
        assert conn is connection
        seen.append(attributes)
        return [item]

    monkeypatch.setitem(
        sys.modules,
        "secretstorage",
        SimpleNamespace(dbus_init=lambda: connection, search_items=search),
    )
    path = tmp_path / "credentials.json"
    assert ab._freebuff_keychain_token(path) == ("" if locked else "stored-token")
    assert seen == [{"service": "freebuff-cli", "account": str(path)}, "closed"]


def test_a_missing_secure_token_does_not_claim_a_signed_in_account(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.Path, "home", classmethod(lambda _: tmp_path))
    monkeypatch.setattr(ab, "_freebuff_keychain_token", lambda _: "")
    path = tmp_path / ".config/manicode/credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"default": {"email": "test@example.com", "tokenStore": "keychain"}})
    )
    assert not ab._freebuff_signed_in(ab._freebuff_account())


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
def test_windows_reader_preserves_bun_utf8_and_chat_utf16_credentials(monkeypatch, encoding):
    raw = "stored-token".encode(encoding)
    buffer = ctypes.create_string_buffer(raw)
    state = {}

    def read(_target, _kind, _flags, output):
        credential = output._obj._type_()
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        state["credential"] = credential
        output._obj.contents = credential
        return 1

    api = SimpleNamespace(CredReadW=_Api(read), CredFree=_Api(lambda _: state.update(freed=True)))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: api, raising=False)
    if encoding == "utf-16-le":
        assert CredentialStore._windows_read("test") == "stored-token"
    else:
        assert CredentialStore._windows_read("test", encoding=encoding) == "stored-token"
    assert state["freed"]


def test_cyclic_model_aliases_are_ignored():
    try:
        result = ab._resolve_minified_string("a=b;b=a;", "a")
    except RecursionError:
        pytest.fail("cyclic model aliases recurse forever", pytrace=False)
    assert result == ""


def test_invalid_settings_are_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.Path, "home", classmethod(lambda _: tmp_path))
    monkeypatch.setattr(ab, "_freebuff_choice_path", lambda: tmp_path / "choice.json")
    settings = tmp_path / ".config/manicode/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"importantSetting":', encoding="utf-8")
    with pytest.raises(OSError, match="settings"):
        ab.set_freebuff_model("a/b")
    assert settings.read_text(encoding="utf-8") == '{"importantSetting":'
    assert not (tmp_path / "choice.json").exists()


def test_choice_write_failure_is_reported(monkeypatch, tmp_path):
    parent = tmp_path / "file"
    parent.write_text("not a directory")
    monkeypatch.setattr(ab, "_freebuff_choice_path", lambda: parent / "choice.json")
    with pytest.raises(OSError):
        ab._write_freebuff_choice("a/b")


def test_repeating_prompt_words_in_the_answer_does_not_move_the_boundary():
    worker = _worker()
    _, answer = worker._freebuff_sections(
        "do the work\nFirst paragraph.\nI will do the work carefully.\nFinal paragraph.\nDescribe your task"
    )
    assert answer == "First paragraph.\nI will do the work carefully.\nFinal paragraph."


def test_a_resumed_chat_with_no_new_boot_log_is_not_ready(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text('{"msg":"Main prompt finished"}\n', encoding="utf-8")
    assert not ab._freebuff_boot_ready(".", {}, tmp_path, log.stat().st_size)


def test_finished_boot_clears_its_initial_disconnect(tmp_path):
    log = tmp_path / "log.jsonl"
    events = [
        "[chat-runtime] Freebuff session over; holding queued messages until rejoin",
        "Enhanced CLI fingerprint generated successfully",
        "[send-message] Sending message with sdk run config",
    ]
    log.write_text("\n".join(json.dumps({"msg": event}) for event in events))
    assert ab._freebuff_run_status(tmp_path) == ""
    assert ab._freebuff_log_boot_finished(log.read_text())
    with log.open("a") as handle:
        handle.write("\n" + json.dumps({"msg": events[0]}))
    assert ab._freebuff_run_status(tmp_path) == "disconnected"
    assert not ab._freebuff_log_boot_finished(log.read_text())


def test_global_history_includes_the_unbucketed_chats(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "_home", lambda: tmp_path)
    chat = tmp_path / ".config/manicode/projects/chats/ours"
    chat.mkdir(parents=True)
    (chat / "chat-meta.json").write_text('{"firstPrompt":"Do the work"}')
    assert [entry.session_id for entry in history._freebuff_entries(None)] == ["ours"]


def test_missing_cli_session_fails_without_starting_freebuff(monkeypatch):
    failures = []
    worker = _worker(on_failed=failures.append)
    worker._session_id = "desktop-only-id"
    monkeypatch.setattr(ab, "find_backend_cli", lambda _: "freebuff")
    monkeypatch.setattr(ab, "set_freebuff_model", lambda _: None)
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _: {})
    monkeypatch.setattr(ab, "_freebuff_chat_path", lambda *_: None)
    monkeypatch.setattr(worker, "_spawn_pty", lambda _: pytest.fail("started an unrelated chat"))
    worker._do_run()
    assert failures and "cannot resume" in failures[0]


def test_a_prewarm_remembers_the_log_offset_before_it_started(monkeypatch, tmp_path):
    spawned = threading.Event()
    chat = tmp_path / "chat"
    chat.mkdir()
    log = chat / "log.jsonl"
    log.write_text('{"msg":"Main prompt finished"}\n')
    old_size = log.stat().st_size
    monkeypatch.setattr(ab, "find_backend_cli", lambda _: "freebuff")
    monkeypatch.setattr(ab, "set_freebuff_model", lambda _: None)
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _: {"chat": 1})
    monkeypatch.setattr(ab, "_freebuff_chat_path", lambda *_: chat)
    monkeypatch.setattr(ab, "_freebuff_resume_chat_path", lambda *_: chat)

    def spawn(*_):
        with log.open("a") as handle:
            handle.write('{"msg":"Enhanced CLI fingerprint generated successfully"}\n')
        spawned.set()
        return SimpleNamespace(terminate=lambda *_: None, close=lambda *_: None), lambda _: ""

    monkeypatch.setattr(ab, "_spawn_freebuff_pty", spawn)
    ab.discard_freebuff_prewarm()
    try:
        ab.prewarm_freebuff(".", "chat", "model")
        assert spawned.wait(3)
        holding = ab._take_freebuff_prewarm(".", "chat", "model")
        assert holding is not None
        assert holding["log_offset"] == old_size
        assert ab._freebuff_boot_ready(".", holding["before"], chat, holding["log_offset"])
    finally:
        if "holding" in locals() and holding is not None:
            ab._end_prewarm(holding)
        ab.discard_freebuff_prewarm()


def test_desktop_history_opens_for_reading_without_a_cli_session(monkeypatch, wx_app):
    import blindpilot_app as app

    entry = history.HistoryEntry("freebuff", "desktop", "Desktop chat", "desktop-v2.db", 1)
    monkeypatch.setattr(app, "load_turns", lambda _: [history.HistoryTurn("Question", "Answer")])
    viewed = []
    monkeypatch.setattr(
        app,
        "ReadView",
        lambda _parent, text, title: SimpleNamespace(
            ShowModal=lambda: viewed.append((text, title)), Destroy=lambda: None
        ),
    )
    frame = SimpleNamespace(
        _history_cwd=lambda: ".",
        _add_session=lambda *_a, **_k: pytest.fail("desktop ID was passed to the CLI"),
    )
    app.MainFrame._resume_history(frame, entry)
    assert viewed and "read only" in viewed[0][1]
    assert "Question" in viewed[0][0] and "Answer" in viewed[0][0]


def test_chat_discovery_uses_the_terminal_process(monkeypatch, tmp_path):
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    for chat, pid in ((ours, 123), (theirs, 456)):
        chat.mkdir()
        (chat / "log.jsonl").write_text(json.dumps({"pid": pid, "msg": "boot"}) + "\n")
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _: {"ours": 1, "theirs": 2})
    monkeypatch.setattr(ab, "_freebuff_chat_path", lambda _, sid: tmp_path / sid)
    monkeypatch.setattr(ab, "_descendant_pids", lambda roots: roots | {123})
    worker = _worker()
    worker._pty = SimpleNamespace(pid=122)
    assert worker._adopt_new_chat({}) == "ours"


def test_a_chat_in_another_project_cannot_be_resumed(monkeypatch, tmp_path):
    monkeypatch.setattr(ab.Path, "home", classmethod(lambda _: tmp_path))
    cwd = tmp_path / "current"
    cwd.mkdir()
    (cwd / ".git").mkdir()
    root = tmp_path / ".config/manicode/projects"
    wrong = root / "other/chats/chat"
    wrong.mkdir(parents=True)
    assert ab._freebuff_chat_path(str(cwd), "chat") == wrong
    assert ab._freebuff_resume_chat_path(str(cwd), "chat") is None
    correct = root / "current/chats/ours"
    correct.mkdir(parents=True)
    assert ab._freebuff_resume_chat_path(str(cwd), "ours") == correct
    subfolder = cwd / "nested"
    subfolder.mkdir()
    nested_chat = root / "nested/chats/nested-chat"
    nested_chat.mkdir(parents=True)
    assert ab._freebuff_resume_chat_path(str(subfolder), "nested-chat") == nested_chat
    assert ab._freebuff_resume_chat_path(str(subfolder), "ours") is None


def test_restarting_a_prewarm_handles_the_replacement_model_picker(monkeypatch):
    state = {"generation": 0, "picked": False, "sent": False, "enters": 0, "reads": 0}
    completed, failed = [], []
    worker = _worker(on_complete=completed.append, on_failed=failed.append)
    worker._model = "a/selected"
    clock = iter(range(10000))
    monkeypatch.setattr(
        ab, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None)
    )
    monkeypatch.setattr(ab, "find_backend_cli", lambda _: "freebuff")
    monkeypatch.setattr(ab, "set_freebuff_model", lambda _: None)
    monkeypatch.setattr(ab, "freebuff_model_options", lambda: (["a/selected"], "a/selected", ""))
    monkeypatch.setattr(ab, "_freebuff_chat_dirs", lambda _: {})
    monkeypatch.setattr(ab, "_freebuff_chat_path", lambda *_: None)
    monkeypatch.setattr(ab, "_freebuff_boot_ready", lambda *_: state["generation"] == 1)
    monkeypatch.setattr(ab, "_FREEBUFF_TURN_SECONDS", 500)
    monkeypatch.setattr(ab, "_FREEBUFF_STARTUP_SILENCE_SECONDS", 40)

    def write(text):
        if text == "\r":
            state["enters"] += 1
            state["picked"] = True
        if text == "\x1b[200~do the work\x1b[201~":
            state["sent"] = True
        return True

    def read(timeout):
        if timeout == 0:
            return ""
        if state["sent"]:
            state["reads"] += 1
            if state["reads"] > 2:
                worker._stream_ended.set()
                return ""
            text = "do the work\nHere is the answer.\nDescribe your task"
        elif state["picked"]:
            text = "Describe your task"
        else:
            text = "RECOMMENDED\n│ › Selected │"
        return "\x1b[2J\x1b[H" + text.replace("\n", "\r\n")

    holding = {"pty": SimpleNamespace(), "read": read, "ended": threading.Event(), "before": {}}
    monkeypatch.setattr(ab, "_take_freebuff_prewarm", lambda *_: holding)

    def restart(_):
        state.update(generation=1, picked=False)
        return read

    monkeypatch.setattr(worker, "_spawn_pty", restart)
    monkeypatch.setattr(worker, "_write", write)
    worker._do_run()
    assert failed == []
    assert completed == ["Here is the answer."]
    assert state["enters"] == 3  # Both pickers, then submission.


class _Api:
    def __init__(self, call=lambda *_: 1):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def _kernel(monkeypatch):
    closed = []
    next_handle = iter(range(1, 10))

    def pipe(read, write, *_):
        read._obj.value, write._obj.value = next(next_handle), next(next_handle)
        return 1

    def pseudo(_coord, _input, _output, _flags, result):
        result._obj.value = next(next_handle)
        return 0

    kernel = SimpleNamespace(
        **{
            name: _Api()
            for name in (
                "CreateProcessW",
                "ReadFile",
                "WriteFile",
                "InitializeProcThreadAttributeList",
                "UpdateProcThreadAttribute",
                "DeleteProcThreadAttributeList",
                "GetExitCodeProcess",
                "TerminateProcess",
                "WaitForSingleObject",
                "PeekNamedPipe",
            )
        }
    )
    kernel.CreatePipe = _Api(pipe)
    kernel.CreatePseudoConsole = _Api(pseudo)
    kernel.CloseHandle = _Api(lambda h: closed.append(("handle", getattr(h, "value", h))))
    kernel.ClosePseudoConsole = _Api(lambda h: closed.append(("pseudo", getattr(h, "value", h))))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    return kernel, closed


def test_failed_windows_spawn_releases_every_handle(monkeypatch):
    _, closed = _kernel(monkeypatch)

    def fail(*_):
        raise OSError("process creation failed")

    monkeypatch.setattr(ab._ConPtyTerminal, "_create_process", fail)
    with pytest.raises(OSError, match="creation failed"):
        ab._ConPtyTerminal(["missing.exe"], ".", {}, 60, 180)
    assert set(closed) == {("handle", n) for n in range(1, 5)} | {("pseudo", 5)}


def test_teardown_closes_the_output_before_the_pseudoconsole(monkeypatch):
    _, closed = _kernel(monkeypatch)
    monkeypatch.setattr(ab._ConPtyTerminal, "_create_process", lambda *_: (6, 123))
    terminal = ab._ConPtyTerminal(["test.exe"], ".", {}, 60, 180)
    monkeypatch.setattr(terminal, "isalive", lambda: False)
    terminal.close()
    assert closed.index(("handle", 3)) < closed.index(("pseudo", 5))


def test_a_closed_terminal_never_uses_its_released_process_handle(monkeypatch):
    kernel, _ = _kernel(monkeypatch)
    monkeypatch.setattr(ab._ConPtyTerminal, "_create_process", lambda *_: (6, 123))
    terminal = ab._ConPtyTerminal(["test.exe"], ".", {}, 60, 180)
    terminal.close()
    called = []
    kernel.GetExitCodeProcess = _Api(lambda *_: called.append("released handle") or 0)
    terminal.terminate()
    assert not terminal.isalive()
    assert called == []


def test_ending_the_launcher_also_ends_its_background_children(monkeypatch):
    kernel, _ = _kernel(monkeypatch)
    monkeypatch.setattr(ab._ConPtyTerminal, "_create_process", lambda *_: (6, 123))
    terminal = ab._ConPtyTerminal(["test.exe"], ".", {}, 60, 180)
    monkeypatch.setattr(terminal, "isalive", lambda: True)
    called = []
    monkeypatch.setattr(ab, "_end_windows_tree", lambda pid: called.append(("tree", pid)))
    kernel.TerminateProcess = _Api(lambda *_: called.append(("process", 123)))
    terminal.terminate()
    assert called == [("tree", 123), ("process", 123)]


@pytest.mark.skipif(platform.system() != "Windows", reason="ConPTY is Windows-only")
@pytest.mark.parametrize("name", ["with spaces", "A&B", "%PATH%", "bang!", "caret^", "(brackets)"])
def test_batch_launcher_in_a_spaced_directory(monkeypatch, tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    script = folder / "probe.cmd"
    script.write_text('@echo spaced-launcher-works\r\n@echo "%~2"\r\n', encoding="utf-8")
    monkeypatch.setattr(ab, "hide_console_windows", lambda *_: 0)
    ended = threading.Event()
    terminal, read = ab._spawn_freebuff_pty([str(script), "--cwd", str(folder)], str(folder), ended)
    output = ""
    try:
        while not ended.is_set():
            output += read(1)
        while chunk := read(0):
            output += chunk
    finally:
        ab.end_hidden_terminal(terminal)
    assert "spaced-launcher-works" in output
    assert str(folder) in output
