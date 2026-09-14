"""Command Code's Provider API: one host, every top model, two protocols.

Command Code serves OpenAI-shaped models on `/chat/completions` and Claude on
`/messages`, and rejects a model sent to the wrong one with a 400. These tests
pin the per-model routing, the built-in connection settings, and the way file
attachments travel on both protocols now that every chat account takes them.
"""

from __future__ import annotations

import json
from threading import Event
from types import SimpleNamespace

import httpx

from accessible_ai.models import (
    API_MODE_AUTO,
    API_MODE_CHAT,
    API_MODE_MESSAGES,
    API_MODE_RESPONSES,
    Account,
    GenerationSettings,
    Message,
    MessageAttachment,
    PROVIDER_COMMAND_CODE,
    PROVIDER_DEEPSEEK,
    PROVIDER_LABELS,
)
from accessible_ai.providers.command_code import CommandCodeProvider
from accessible_ai.providers.config import (
    BUILTIN_PROVIDER_DEFAULTS,
    apply_builtin_provider_defaults,
)
from accessible_ai.providers.factory import create_provider
from accessible_ai.ui.accounts import BUILTIN_PROVIDER_NOTES, PROVIDER_ORDER


# ----- The provider and its routing -----

PROVIDER_BASE_URL = "https://api.commandcode.ai/provider/v1"


def _provider(monkeypatch, account: Account | None = None) -> CommandCodeProvider:
    account = account or Account(id=1, name="Command Code", provider=PROVIDER_COMMAND_CODE)
    # A forced API mode is a deliberate setting, so the defaults repair only
    # runs for an account that left it on Automatic (or a blank old row).
    if account.api_mode not in {API_MODE_CHAT, API_MODE_MESSAGES, API_MODE_RESPONSES}:
        apply_builtin_provider_defaults(account)
    provider = CommandCodeProvider(account, credentials=object())  # type: ignore[arg-type]
    monkeypatch.setattr(CommandCodeProvider, "api_key", lambda _self: "test-key")
    return provider


def test_claude_models_are_routed_to_the_messages_protocol(monkeypatch):
    provider = _provider(monkeypatch)
    assert provider.protocol_for_model("claude-sonnet-4-6") == API_MODE_MESSAGES
    assert provider.protocol_for_model("claude-opus-5") == API_MODE_MESSAGES


def test_an_anthropic_prefixed_model_is_routed_to_the_messages_protocol(monkeypatch):
    provider = _provider(monkeypatch)
    assert provider.protocol_for_model("anthropic/claude-sonnet-4-6") == API_MODE_MESSAGES


def test_every_other_model_is_routed_to_chat_completions(monkeypatch):
    provider = _provider(monkeypatch)
    assert provider.protocol_for_model("deepseek/deepseek-v4-flash") == API_MODE_CHAT
    assert provider.protocol_for_model("gpt-5.6") == API_MODE_CHAT
    assert provider.protocol_for_model("gemini-3-pro") == API_MODE_CHAT


def test_an_api_mode_chosen_in_the_account_beats_the_routing(monkeypatch):
    """Routing is what an account left on Automatic falls back to."""
    forced = Account(id=1, name="CC", provider=PROVIDER_COMMAND_CODE, api_mode=API_MODE_MESSAGES)
    provider = _provider(monkeypatch, forced)
    assert provider.protocol_for_model("deepseek/deepseek-v4-flash") == API_MODE_MESSAGES

    chat_only = Account(id=2, name="CC", provider=PROVIDER_COMMAND_CODE, api_mode=API_MODE_CHAT)
    provider = _provider(monkeypatch, chat_only)
    assert provider.protocol_for_model("claude-sonnet-4-6") == API_MODE_CHAT

    automatic = Account(id=3, name="CC", provider=PROVIDER_COMMAND_CODE, api_mode=API_MODE_AUTO)
    provider = _provider(monkeypatch, automatic)
    assert provider.protocol_for_model("claude-sonnet-4-6") == API_MODE_MESSAGES


def test_the_command_code_prefix_is_stripped_from_a_model_before_sending(monkeypatch):
    """A model picked as `command-code/...` reaches the API under its bare id."""

    class _Recorder:
        def __init__(self):
            self.body: dict = {}

        def transport(self) -> httpx.MockTransport:
            def handle(request: httpx.Request) -> httpx.Response:
                self.body = json.loads(request.content)
                lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]})]
                lines.append("data: [DONE]")
                return httpx.Response(200, text="\n\n".join(lines) + "\n\n")

            return httpx.MockTransport(handle)

    recorder = _Recorder()
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        CommandCodeProvider, "client", lambda _self: httpx.Client(transport=recorder.transport())
    )
    settings = GenerationSettings(
        model="command-code/deepseek/deepseek-v4-flash",
        messages=[{"role": "user", "content": "hello"}],
    )
    list(provider.generate(settings, Event()))
    assert recorder.body["model"] == "deepseek/deepseek-v4-flash"


def _messages_transport(bodies: list[dict]) -> httpx.MockTransport:
    """Answers one Anthropic-shaped turn, keeping the request body."""

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text=_anthropic_text())

    return httpx.MockTransport(handle)


def _with_transport(monkeypatch, provider: CommandCodeProvider, transport: httpx.MockTransport):
    monkeypatch.setattr(
        CommandCodeProvider, "client", lambda _self: httpx.Client(transport=transport)
    )


def test_a_claude_model_goes_to_messages_and_an_open_model_to_chat_completions(monkeypatch):
    """The two protocols, on the wire, from one account."""
    provider = _provider(monkeypatch)
    seen_urls: list[str] = []

    def transport_for(_model: str):
        def handle(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            if str(request.url).endswith("/messages"):
                chunk = {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "hi"},
                }
                text = f"event: content_block_delta\ndata: {json.dumps(chunk)}\n\n"
            else:
                chunk = {"choices": [{"delta": {"content": "hi"}}]}
                text = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text)

        return httpx.MockTransport(handle)

    for model in ("claude-sonnet-4-6", "deepseek/deepseek-v4-flash"):
        _with_transport(monkeypatch, provider, transport_for(model))
        settings = GenerationSettings(model=model, messages=[{"role": "user", "content": "hello"}])
        list(provider.generate(settings, Event()))

    assert [url.split("/provider/v1/")[-1] for url in seen_urls] == [
        "messages",
        "chat/completions",
    ]
    assert seen_urls[0].startswith(PROVIDER_BASE_URL)


def test_the_messages_body_carries_what_anthropic_requires(monkeypatch):
    provider = _provider(monkeypatch)
    bodies: list[dict] = []
    _with_transport(monkeypatch, provider, _messages_transport(bodies))
    settings = GenerationSettings(
        model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hello"}]
    )
    list(provider.generate(settings, Event()))
    assert bodies[0]["model"] == "claude-sonnet-4-6"
    assert bodies[0]["max_tokens"] == 4096
    assert bodies[0]["messages"] == [{"role": "user", "content": "hello"}]


# ----- Built-in connection settings and wiring -----


def test_command_codes_connection_settings_are_built_in():
    defaults = BUILTIN_PROVIDER_DEFAULTS[PROVIDER_COMMAND_CODE]
    assert defaults["base_url"] == PROVIDER_BASE_URL
    assert defaults["api_mode"] == API_MODE_AUTO
    assert defaults["models_endpoint"] == "/models"
    assert defaults["chat_endpoint"] == "/chat/completions"
    assert defaults["messages_endpoint"] == "/messages"


def test_the_factory_builds_the_command_code_provider(tmp_path):
    from accessible_ai.storage.credentials import CredentialStore

    account = Account(id=1, name="Command Code", provider=PROVIDER_COMMAND_CODE)
    apply_builtin_provider_defaults(account)
    provider = create_provider(account, CredentialStore())
    assert isinstance(provider, CommandCodeProvider)


def test_the_account_dialog_offers_command_code_with_its_own_note():
    assert PROVIDER_COMMAND_CODE in PROVIDER_ORDER
    assert PROVIDER_LABELS[PROVIDER_COMMAND_CODE] == "Command Code"
    note = BUILTIN_PROVIDER_NOTES[PROVIDER_COMMAND_CODE]
    assert "Command Code" in note and "Studio" in note


def test_an_old_account_row_is_repaired_with_the_built_in_addresses():
    account = Account(
        id=1,
        name="stale",
        provider=PROVIDER_COMMAND_CODE,
        base_url="",
        api_mode="",
    )
    apply_builtin_provider_defaults(account)
    assert account.base_url == PROVIDER_BASE_URL
    assert account.api_mode == API_MODE_AUTO
    assert account.chat_endpoint == "/chat/completions"


# ----- Attachments on every protocol -----


PNG_BYTES = b"\x89PNG-not-really-a-picture"


def _png() -> MessageAttachment:
    return MessageAttachment(filename="shot.png", mime_type="image/png", data=PNG_BYTES)


def _pdf() -> MessageAttachment:
    return MessageAttachment(filename="paper.pdf", mime_type="application/pdf", data=b"%PDF-1.4")


def _notes() -> MessageAttachment:
    return MessageAttachment(filename="notes.txt", mime_type="text/plain", data=b"the notes")


def _message_settings(attachments: list[MessageAttachment]) -> GenerationSettings:
    return GenerationSettings(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "read this", "attachments": attachments}],
    )


def _anthropic_text() -> str:
    chunk = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
    return f"event: content_block_delta\ndata: {json.dumps(chunk)}\n\n"


def test_an_attached_image_travels_as_a_messages_source_block(monkeypatch):
    provider = _provider(monkeypatch)
    bodies: list[dict] = []
    _with_transport(monkeypatch, provider, _messages_transport(bodies))
    list(provider.generate(_message_settings([_png()]), Event()))

    content = bodies[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "read this"}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": __import__("base64").b64encode(PNG_BYTES).decode("ascii"),
        },
    }


def test_an_attached_pdf_travels_as_a_messages_document_block(monkeypatch):
    provider = _provider(monkeypatch)
    bodies: list[dict] = []
    _with_transport(monkeypatch, provider, _messages_transport(bodies))
    list(provider.generate(_message_settings([_pdf()]), Event()))

    content = bodies[0]["messages"][-1]["content"]
    assert content[1]["type"] == "document"
    assert content[1]["source"]["media_type"] == "application/pdf"


def test_an_attached_text_file_goes_in_as_its_text_on_messages(monkeypatch):
    """A file with no block of its own travels as one more text block."""
    provider = _provider(monkeypatch)
    bodies: list[dict] = []
    _with_transport(monkeypatch, provider, _messages_transport(bodies))
    list(provider.generate(_message_settings([_notes()]), Event()))

    content = bodies[0]["messages"][-1]["content"]
    assert content[1] == {"type": "text", "text": "[Attached file: notes.txt]\nthe notes"}


def test_a_message_without_attachments_stays_a_plain_string_on_messages(monkeypatch):
    """A body that was fine before the attachments work stays exactly as it was."""
    provider = _provider(monkeypatch)
    bodies: list[dict] = []
    _with_transport(monkeypatch, provider, _messages_transport(bodies))
    settings = GenerationSettings(
        model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hello"}]
    )
    list(provider.generate(settings, Event()))
    assert bodies[0]["messages"] == [{"role": "user", "content": "hello"}]


def test_attachments_reach_the_chat_completions_wire_too(monkeypatch):
    """The OpenAI shape carries the same image as an image_url part."""
    provider = _provider(monkeypatch)
    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        chunk = {"choices": [{"delta": {"content": "hi"}}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    _with_transport(monkeypatch, provider, httpx.MockTransport(handle))
    settings = GenerationSettings(
        model="deepseek/deepseek-v4-flash",
        messages=[{"role": "user", "content": "read this", "attachments": [_png()]}],
    )
    list(provider.generate(settings, Event()))

    content = bodies[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "read this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_attachments_are_built_into_a_request_for_any_account(tmp_path):
    """The window packs files into the request whatever the provider is."""
    from accessible_ai.models import Conversation
    from accessible_ai.storage.database import Database
    from accessible_ai.ui.chat_panel import ChatPanel

    db = Database(tmp_path / "chat.sqlite3")

    conversation_id = db.create_conversation(Conversation(title="files"))
    db.add_message(
        Message(
            conversation_id=conversation_id,
            role="user",
            content="read this",
            attachments=[_png()],
        )
    )
    panel = SimpleNamespace(
        current_conversation_id=conversation_id,
        current_system_prompt="",
        current_profile=None,
        db=db,
    )
    for provider in (PROVIDER_DEEPSEEK, PROVIDER_COMMAND_CODE):
        account = Account(id=1, name="a", provider=provider)
        settings = ChatPanel._generation_settings(panel, account, "some/model")
        assert settings.messages[-1]["attachments"], provider
