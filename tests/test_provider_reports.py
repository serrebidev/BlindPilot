"""Provider usage reports, Ollama, and Chat compaction."""

from __future__ import annotations


from accessible_ai.models import (
    PROVIDER_OLLAMA,
    PROVIDER_OPENROUTER,
    Account,
    Conversation,
    Message,
    MessageAttachment,
)
from accessible_ai.providers.config import apply_builtin_provider_defaults
from accessible_ai.providers.factory import create_provider
from accessible_ai.providers.ollama import OllamaProvider
from accessible_ai.providers.provider_reports import (
    format_report,
    ollama_report,
    openrouter_report,
    usage_report,
)
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database


class FakeCredentials(CredentialStore):
    def get_api_key(self, account_id: int) -> str:
        return "secret-key"


def _account(tmp_path, provider=PROVIDER_OPENROUTER):
    db = Database(tmp_path / "chat.sqlite3")
    account = Account(name="Acct", provider=provider, base_url="https://openrouter.ai/api/v1")
    account.id = db.save_account(account)
    return account


class FakeResponse:
    status_code = 200

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class FakeClient:
    """One shared payload queue across instances, like a real server."""

    def __init__(self, payloads, error=None):
        self._payloads = payloads
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        if self._error:
            raise self._error
        return FakeResponse(self._payloads.pop(0))


def test_openrouter_report_reads_key_and_credits(tmp_path, monkeypatch):
    from accessible_ai.providers import provider_reports

    queue = [
        {"data": {"usage": 1.5, "limit_remaining": 8.5, "is_free_tier": False}},
        {"data": 10.0},
    ]
    monkeypatch.setattr(
        provider_reports.httpx,
        "Client",
        lambda **kwargs: FakeClient(queue),
    )
    report = openrouter_report(_account(tmp_path), FakeCredentials())
    assert report.usage == 1.5
    assert report.limit_remaining == 8.5
    assert report.balance == 10.0
    assert report.is_free_tier is False
    assert report.error == ""


def test_openrouter_report_failure_is_not_zeros(tmp_path, monkeypatch):
    import httpx

    from accessible_ai.providers import provider_reports

    monkeypatch.setattr(
        provider_reports.httpx,
        "Client",
        lambda **kwargs: FakeClient([], error=httpx.ConnectError("down", request=None)),
    )
    report = openrouter_report(_account(tmp_path), FakeCredentials())
    assert report.usage is None
    assert "down" in report.error


def test_ollama_report_has_no_usage(tmp_path):
    report = ollama_report(_account(tmp_path, PROVIDER_OLLAMA), FakeCredentials())
    assert report.usage is None and report.balance is None
    assert "no usage" in report.note
    assert "Account:" in format_report(report)


def test_usage_report_unknown_provider(tmp_path):
    report = usage_report(_account(tmp_path, "custom"), FakeCredentials())
    assert report.usage is None
    assert "does not publish" in report.note


def test_ollama_factory_and_defaults(tmp_path):
    account = _account(tmp_path, PROVIDER_OLLAMA)
    provider = create_provider(account, FakeCredentials())
    assert isinstance(provider, OllamaProvider)
    apply_builtin_provider_defaults(account)
    assert account.base_url == "http://localhost:11434"
    assert account.chat_endpoint == "/v1/chat/completions"


def test_ollama_model_discovery_parses_tags(tmp_path, monkeypatch):
    account = _account(tmp_path, PROVIDER_OLLAMA)
    provider = OllamaProvider(account, FakeCredentials())

    class StreamClient:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url):
            return self

        def raise_for_status(self):
            return None

        def read(self):
            import json

            return json.dumps(
                {"models": [{"name": "llama3.1"}, {"name": "llama3.1"}, {"name": "qwen2"}]}
            ).encode()

    monkeypatch.setattr(provider, "client", lambda: StreamClient())
    assert provider.list_models() == ["llama3.1", "qwen2"]


def test_compaction_preserves_original_and_keeps_newest(tmp_path):
    db = Database(tmp_path / "chat.sqlite3")
    source_id = db.create_conversation(Conversation(title="Long chat"))
    for index in range(8):
        db.add_message(
            Message(
                conversation_id=source_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"msg-{index}",
                attachments=[
                    MessageAttachment(
                        filename=f"a{index}.txt", mime_type="text/plain", data=bytes([index])
                    )
                ],
            )
        )
    new_id = db.compact_conversation(source_id, 2, "SUMMARY TEXT", "Long chat (compacted)")
    # Original untouched.
    assert len(db.list_messages(source_id)) == 8
    # New conversation has the summary snapshot plus exactly the newest two.
    conversation = db.get_conversation(new_id)
    assert conversation is not None
    assert conversation.system_prompt_snapshot == "SUMMARY TEXT"
    messages = db.list_messages(new_id)
    assert [m.content for m in messages] == ["msg-6", "msg-7"]
    assert messages[0].attachments[0].data == bytes([6])


def test_compacted_conversation_defaults_to_source_snapshot_when_summary_empty(tmp_path):
    db = Database(tmp_path / "chat.sqlite3")
    source_id = db.create_conversation(
        Conversation(title="T", system_prompt_snapshot="ORIGINAL PROMPT")
    )
    db.add_message(Message(conversation_id=source_id, role="user", content="one"))
    new_id = db.compact_conversation(source_id, 0, "  ", "T (compacted)")
    conversation = db.get_conversation(new_id)
    assert conversation is not None
    assert conversation.system_prompt_snapshot == "ORIGINAL PROMPT"
    assert db.list_messages(new_id) == []
