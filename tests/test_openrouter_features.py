"""Chat mode's OpenRouter extras: server tools, thinking, and searched sources.

Everything covered here runs at OpenRouter rather than on this machine. A tool
the model calls is executed there and its result handed back to the model, so
the chat window sends the request, reads what comes back, and runs nothing
itself. These tests pin the request bodies to the shapes OpenRouter documents,
and pin what the window does with the three new things a response can carry:
thinking, tool calls, and citations.
"""

from __future__ import annotations

import json
import sqlite3
from threading import Event

import httpx
import pytest

from accessible_ai.models import (
    Account,
    GenerationSettings,
    OpenRouterFeatures,
    PROVIDER_OPENROUTER,
    Profile,
)
from accessible_ai.providers.openrouter import OpenRouterProvider
from accessible_ai.providers.protocols import (
    SourceCollector,
    ToolCallCollector,
    reasoning_text,
)
from accessible_ai.storage.database import SCHEMA, Database


# ----- The request these settings come to -----


def test_server_tools_are_sent_in_the_order_they_are_offered():
    """The tools array is what asks OpenRouter to run a tool on the model's behalf."""
    features = OpenRouterFeatures(
        server_tools=["openrouter:datetime", "openrouter:web_search"],
        search_max_results=8,
        search_context="high",
    )
    assert features.request_tools() == [
        {
            "type": "openrouter:web_search",
            "parameters": {"max_results": 8, "search_context_size": "high"},
        },
        {"type": "openrouter:datetime"},
    ]


def test_a_tool_with_nothing_configured_is_sent_bare():
    features = OpenRouterFeatures(server_tools=["openrouter:web_search"])
    assert features.request_tools() == [{"type": "openrouter:web_search"}]


def test_no_tools_chosen_sends_no_tools_array():
    assert OpenRouterFeatures().request_tools() == []


def test_thinking_effort_and_budget_become_the_reasoning_object():
    features = OpenRouterFeatures(reasoning_effort="high", reasoning_max_tokens=4000)
    assert features.request_reasoning() == {"effort": "high", "max_tokens": 4000}


def test_thinking_can_be_asked_for_without_being_sent_back():
    """Hiding the thinking is a request to the provider, not a filter here.

    The model still thinks; the tokens are still paid for. `exclude` is what
    keeps the thinking out of the response.
    """
    features = OpenRouterFeatures(reasoning_effort="high", show_reasoning=False)
    assert features.request_reasoning() == {"effort": "high", "exclude": True}


def test_turning_thinking_off_is_not_an_effort_of_none():
    """OpenRouter switches thinking off through `enabled`, not an effort."""
    assert OpenRouterFeatures(reasoning_effort="none").request_reasoning() == {"enabled": False}


def test_saying_nothing_about_thinking_leaves_the_model_alone():
    assert OpenRouterFeatures().request_reasoning() is None


def test_the_pdf_reader_is_the_only_plugin():
    assert OpenRouterFeatures(pdf_engine="mistral-ocr").request_plugins() == [
        {"id": "file-parser", "pdf": {"engine": "mistral-ocr"}}
    ]
    assert OpenRouterFeatures().request_plugins() == []


def test_settings_written_by_a_newer_release_do_not_stop_a_profile_opening():
    """Every field falls back to its default rather than raising."""
    features = OpenRouterFeatures.from_dict(
        {
            "server_tools": ["openrouter:web_search", "openrouter:not_a_tool", 7],
            "reasoning_effort": "stupendous",
            "search_context": "enormous",
            "pdf_engine": "telepathy",
            "search_max_results": "many",
            "reasoning_max_tokens": -5,
        }
    )
    assert features.server_tools == ["openrouter:web_search"]
    assert features.reasoning_effort == ""
    assert features.search_context == ""
    assert features.pdf_engine == ""
    assert features.search_max_results is None
    assert features.reasoning_max_tokens is None
    assert OpenRouterFeatures.from_dict("not a mapping") == OpenRouterFeatures()


def test_settings_survive_being_written_to_the_profile_and_read_back(tmp_path):
    db = Database(tmp_path / "chat.sqlite3")
    features = OpenRouterFeatures(
        server_tools=["openrouter:web_search", "openrouter:advisor"],
        search_max_results=5,
        search_context="medium",
        reasoning_effort="medium",
        reasoning_max_tokens=2048,
        show_reasoning=False,
        pdf_engine="native",
    )
    profile_id = db.save_profile(Profile(name="Research", openrouter=features))
    assert db.get_profile(profile_id).openrouter == features


def test_a_database_from_before_the_tools_existed_gains_the_column(tmp_path):
    """CREATE TABLE IF NOT EXISTS leaves an old table alone, so it is altered."""
    path = tmp_path / "old.sqlite3"
    older_schema = SCHEMA.replace(",\n    openrouter_json TEXT NOT NULL DEFAULT '{}'", "")
    assert "openrouter_json" not in older_schema
    conn = sqlite3.connect(path)
    conn.executescript(older_schema)
    conn.execute("INSERT INTO profiles (name, system_prompt) VALUES ('Old', 'hello')")
    conn.commit()
    conn.close()

    profile = Database(path).list_profiles()[0]
    assert profile.name == "Old"
    assert profile.system_prompt == "hello"
    # Everything off is what that profile was already doing.
    assert profile.openrouter == OpenRouterFeatures()


# ----- What actually goes over the wire -----


class _Recorder:
    """Answers one chat completion and keeps the request body it was sent."""

    def __init__(self, chunks: list[dict] | None = None):
        self.body: dict = {}
        self._chunks = chunks or [{"choices": [{"delta": {"content": "hi"}}]}]

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.body = json.loads(request.content)
            lines = [f"data: {json.dumps(chunk)}" for chunk in self._chunks]
            lines.append("data: [DONE]")
            return httpx.Response(200, text="\n\n".join(lines) + "\n\n")

        return httpx.MockTransport(handle)


def _provider(recorder: _Recorder, monkeypatch) -> OpenRouterProvider:
    account = Account(id=1, name="OpenRouter", provider=PROVIDER_OPENROUTER)
    account.base_url = "https://openrouter.ai/api/v1"
    provider = OpenRouterProvider(account, credentials=object())  # type: ignore[arg-type]
    monkeypatch.setattr(OpenRouterProvider, "api_key", lambda _self: "test-key")
    monkeypatch.setattr(
        OpenRouterProvider,
        "client",
        lambda _self: httpx.Client(transport=recorder.transport()),
    )
    return provider


def _settings(**extra) -> GenerationSettings:
    return GenerationSettings(
        model="anthropic/claude-sonnet-5",
        messages=[{"role": "user", "content": "hello"}],
        **extra,
    )


def test_the_request_carries_tools_thinking_and_plugins_when_asked_for(monkeypatch):
    recorder = _Recorder()
    provider = _provider(recorder, monkeypatch)
    features = OpenRouterFeatures(
        server_tools=["openrouter:web_search"],
        reasoning_effort="high",
        pdf_engine="native",
    )
    settings = _settings(
        tools=features.request_tools(),
        reasoning=features.request_reasoning(),
        plugins=features.request_plugins(),
    )
    list(provider.generate(settings, Event()))
    assert recorder.body["tools"] == [{"type": "openrouter:web_search"}]
    assert recorder.body["reasoning"] == {"effort": "high"}
    assert recorder.body["plugins"] == [{"id": "file-parser", "pdf": {"engine": "native"}}]


def test_a_request_that_asked_for_nothing_extra_sends_no_extra_keys(monkeypatch):
    """A provider that rejects a key it does not know must never be sent one."""
    recorder = _Recorder()
    provider = _provider(recorder, monkeypatch)
    list(provider.generate(_settings(), Event()))
    for key in ("tools", "reasoning", "plugins"):
        assert key not in recorder.body


def _panel_for(profile: Profile):
    """A stand-in for the chat panel, for the parts that build a request.

    Building a real one needs a window; these are the fields it reads to turn
    a profile and an account into one request.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        current_conversation_id=1,
        current_system_prompt="",
        current_profile_id=profile.id,
        db=SimpleNamespace(list_messages=lambda _id: [], get_profile=lambda _id: profile),
    )


def test_a_non_openrouter_account_is_never_given_openrouters_keys():
    """Another provider sent an unknown key rejects the whole request."""
    from accessible_ai.models import PROVIDER_DEEPSEEK
    from accessible_ai.ui.chat_panel import ChatPanel

    profile = Profile(
        id=1,
        name="Research",
        openrouter=OpenRouterFeatures(
            server_tools=["openrouter:web_search"], reasoning_effort="high", pdf_engine="native"
        ),
    )
    panel = _panel_for(profile)

    openrouter = ChatPanel._generation_settings(
        panel, Account(id=1, name="r", provider=PROVIDER_OPENROUTER), "some/model"
    )
    assert openrouter.tools and openrouter.reasoning and openrouter.plugins

    deepseek = ChatPanel._generation_settings(
        panel, Account(id=2, name="d", provider=PROVIDER_DEEPSEEK), "some/model"
    )
    assert deepseek.tools == []
    assert deepseek.reasoning is None
    assert deepseek.plugins == []


# ----- What comes back -----


def test_thinking_is_read_from_either_shape_a_provider_uses():
    assert reasoning_text({"reasoning": "step one"}) == "step one"
    assert (
        reasoning_text(
            {
                "reasoning_details": [
                    {"type": "reasoning.text", "text": "step "},
                    {"type": "reasoning.summary", "summary": "two"},
                ]
            }
        )
        == "step two"
    )


def test_sealed_thinking_has_nothing_to_show():
    """An encrypted block is for the provider to read back, not for a person."""
    assert (
        reasoning_text({"reasoning_details": [{"type": "reasoning.encrypted", "data": "xx"}]}) == ""
    )


def test_thinking_arrives_as_its_own_kind_not_as_the_answer(monkeypatch):
    recorder = _Recorder(
        [
            {"choices": [{"delta": {"reasoning": "let me think"}}]},
            {"choices": [{"delta": {"content": "the answer"}}]},
        ]
    )
    provider = _provider(recorder, monkeypatch)
    events = [(e.kind, e.text) for e in provider.generate(_settings(), Event())]
    assert ("reasoning", "let me think") in events
    assert ("text", "the answer") in events


def test_a_tool_call_is_announced_once_however_many_deltas_it_takes():
    """Arguments stream in piece by piece; the name arrives once, at the start."""
    tools = ToolCallCollector()
    first = tools.absorb(
        {
            "tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "openrouter:web_search"}}
            ]
        }
    )
    later = tools.absorb(
        {"tool_calls": [{"index": 0, "id": "call_1", "function": {"arguments": '{"q"'}}]}
    )
    assert first == ["Running web search"]
    assert later == []


def test_two_different_tools_are_both_announced():
    tools = ToolCallCollector()
    tools.absorb({"tool_calls": [{"id": "a", "function": {"name": "openrouter:web_search"}}]})
    said = tools.absorb({"tool_calls": [{"id": "b", "function": {"name": "openrouter:datetime"}}]})
    assert said == ["Running datetime"]


def test_a_tool_openrouter_does_not_run_is_named_as_one_this_window_cannot():
    tools = ToolCallCollector()
    said = tools.absorb({"tool_calls": [{"id": "a", "function": {"name": "read_file"}}]})
    assert said == ["The model asked to run read_file"]
    assert tools.client_side_names() == {"read_file"}


def test_openrouters_own_tools_are_never_counted_as_this_windows_work():
    tools = ToolCallCollector()
    tools.absorb({"tool_calls": [{"id": "a", "function": {"name": "openrouter:shell"}}]})
    assert tools.client_side_names() == set()


def test_an_answerless_turn_that_wanted_a_local_tool_says_so(monkeypatch):
    """ "Returned no text" is a poor account of a model waiting on a tool."""
    recorder = _Recorder(
        [{"choices": [{"delta": {"tool_calls": [{"id": "a", "function": {"name": "read_file"}}]}}]}]
    )
    provider = _provider(recorder, monkeypatch)
    with pytest.raises(Exception) as caught:
        list(provider.generate(_settings(), Event()))
    message = str(caught.value)
    assert "read_file" in message
    assert "OpenRouter" in message


def test_sources_are_collected_once_each_and_listed_at_the_end():
    """A streamed response repeats its annotations, so the same page arrives often."""
    sources = SourceCollector()
    annotation = {
        "type": "url_citation",
        "url_citation": {"url": "https://example.com/a", "title": "A page"},
    }
    sources.absorb({"annotations": [annotation]})
    sources.absorb({"annotations": [annotation]})
    sources.absorb({"annotations": [{"url": "https://example.com/b"}]})
    assert sources.as_list() == [
        {"url": "https://example.com/a", "title": "A page"},
        {"url": "https://example.com/b", "title": ""},
    ]
    listing = sources.listing()
    assert listing.splitlines() == [
        "Sources (2):",
        "1. A page - https://example.com/a",
        "2. https://example.com/b",
    ]


def test_an_answer_that_cited_nothing_has_no_sources_line():
    assert SourceCollector().listing() == ""


def test_citations_come_back_as_their_own_event_after_the_answer(monkeypatch):
    recorder = _Recorder(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "content": "Today's news",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://news.example",
                                        "title": "News",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    )
    provider = _provider(recorder, monkeypatch)
    events = list(provider.generate(_settings(), Event()))
    kinds = [event.kind for event in events]
    assert kinds.index("text") < kinds.index("sources") < kinds.index("done")
    sources = next(event for event in events if event.kind == "sources")
    assert sources.metadata["sources"] == [{"url": "https://news.example", "title": "News"}]


def test_an_answer_cut_off_at_the_length_limit_says_so(monkeypatch):
    """A sentence that stops mid-word needs a reason a listener can hear."""
    recorder = _Recorder(
        [
            {"choices": [{"delta": {"content": "Once upon a"}}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ]
    )
    provider = _provider(recorder, monkeypatch)
    events = list(provider.generate(_settings(), Event()))
    kinds = [event.kind for event in events]
    cut_off = next(event for event in events if event.kind == "status")
    assert cut_off.text == "Response cut off at the model's length limit"
    assert cut_off.metadata.get("record") is True
    assert kinds.index("text") < kinds.index("status") < kinds.index("done")


def test_a_finished_answer_reports_no_cut_off(monkeypatch):
    recorder = _Recorder(
        [{"choices": [{"delta": {"content": "All of it."}, "finish_reason": "stop"}]}]
    )
    provider = _provider(recorder, monkeypatch)
    assert not any(event.kind == "status" for event in provider.generate(_settings(), Event()))


def test_an_error_openrouter_reports_inside_a_choice_is_raised_as_one(monkeypatch):
    """OpenRouter puts a mid-stream failure in choices[0].error, not at the top."""
    recorder = _Recorder(
        [
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "error",
                        "error": {"code": 502, "message": "the upstream provider fell over"},
                    }
                ]
            }
        ]
    )
    provider = _provider(recorder, monkeypatch)
    with pytest.raises(Exception) as caught:
        list(provider.generate(_settings(), Event()))
    assert "the upstream provider fell over" in str(caught.value)
    assert "without returning any text" not in str(caught.value)


# ----- The profile editor -----


def test_the_profile_editor_shows_and_saves_every_openrouter_choice(tmp_path):
    """Each control is labelled and holds what the profile stored.

    The editor is the only place these settings can be reached, so a control
    that does not load what was saved, or does not save what was set, silently
    changes what a conversation costs and what it is allowed to do.
    """
    import wx

    from accessible_ai.ui.profiles import ProfileEditorDialog

    owns_app = wx.GetApp() is None
    app = wx.GetApp() or wx.App(False)
    db = Database(tmp_path / "chat.sqlite3")
    stored = OpenRouterFeatures(
        server_tools=["openrouter:web_search", "openrouter:subagent"],
        search_max_results=7,
        search_context="high",
        reasoning_effort="medium",
        reasoning_max_tokens=3000,
        show_reasoning=False,
        pdf_engine="mistral-ocr",
    )
    profile_id = db.save_profile(Profile(name="Research", openrouter=stored))

    frame = wx.Frame(None)
    try:
        dialog = ProfileEditorDialog(frame, db, db.get_profile(profile_id))
        try:
            assert dialog.reasoning_effort.GetStringSelection() == "Medium"
            assert dialog.reasoning_tokens.GetValue() == "3000"
            assert dialog.show_reasoning.GetValue() is False
            assert dialog.search_max_results.GetValue() == "7"
            assert dialog.search_context.GetStringSelection() == "Thorough"
            assert dialog.pdf_engine.GetStringSelection().startswith("Mistral OCR")
            checked = {
                index
                for index in range(dialog.server_tools.GetCount())
                if dialog.server_tools.IsChecked(index)
            }
            assert len(checked) == 2
            # Every control announces itself; a blind user has only its name.
            for control, name in (
                (dialog.reasoning_effort, "Thinking effort"),
                (dialog.reasoning_tokens, "Thinking token budget"),
                (dialog.show_reasoning, "Send the thinking back"),
                (dialog.search_max_results, "Web search results per search"),
                (dialog.search_context, "Web search depth"),
                (dialog.pdf_engine, "Read attached PDFs with"),
                (dialog.server_tools, "OpenRouter tools"),
            ):
                assert control.GetName() == name

            dialog.server_tools.Check(0, False)
            dialog.reasoning_effort.SetSelection(0)
            dialog.reasoning_tokens.SetValue("")
            try:
                dialog.on_ok(None)
            except wx.wxAssertionError:
                # EndModal, because this dialog was never shown modally. The
                # save it does first is the part under test.
                pass
            saved = db.get_profile(profile_id).openrouter
            assert saved.server_tools == ["openrouter:subagent"]
            assert saved.reasoning_effort == ""
            assert saved.reasoning_max_tokens is None
            assert saved.search_max_results == 7
        finally:
            dialog.Destroy()
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
        wx.Yield()
        if owns_app:
            app.Destroy()


# ----- What the window does with the thinking -----


def _streaming_panel():
    """A stand-in for the chat panel, recording what a turn writes where.

    The panel's own controls are wx widgets; these are the calls the streaming
    path makes against them, which is what the ordering below is about.
    """
    from types import SimpleNamespace

    from accessible_ai.models import Message
    from accessible_ai.ui.chat_panel import ChatPanel

    panel = SimpleNamespace(
        closing=False,
        assistant_buffer="",
        assistant_reasoning="",
        _reasoning_entry=None,
        transcript_writes=[],
        entries=[],
        spoken=[],
    )
    panel._append_history_text = panel.transcript_writes.append
    panel._insert_history_entry_before_response = panel.entries.append
    panel._refresh_history_entry = lambda _entry: None
    panel._update_streaming_history_entry = lambda: None
    panel._queue_response_announcement = lambda _text: None
    panel._response_announcements_allowed = lambda: True
    panel._speak = panel.spoken.append
    panel._append_assistant_text = lambda text: ChatPanel._append_assistant_text(panel, text)
    panel.Message = Message
    return panel


def test_the_thinking_is_kept_out_of_the_answer_and_announced_once():
    """Reading the thinking aloud as it streams would bury the answer."""
    from accessible_ai.ui.chat_panel import ChatPanel

    panel = _streaming_panel()
    ChatPanel._append_assistant_reasoning(panel, "weighing ")
    ChatPanel._append_assistant_reasoning(panel, "the options")
    ChatPanel._append_assistant_text(panel, "The answer.")

    assert panel.assistant_reasoning == "weighing the options"
    # The answer alone is what gets saved as the message.
    assert panel.assistant_buffer == "The answer."
    assert panel.spoken == ["Thinking"]
    # One entry for the whole of the thinking, not one per delta.
    assert len(panel.entries) == 1
    assert panel.entries[0].role == "thinking"
    assert panel.entries[0].content == "weighing the options"
    assert panel.transcript_writes == [
        "Thinking:\r\n",
        "weighing ",
        "the options",
        "\r\n\r\nAnswer:\r\n",
        "The answer.",
    ]


def test_an_answer_with_no_thinking_gets_no_extra_heading():
    from accessible_ai.ui.chat_panel import ChatPanel

    panel = _streaming_panel()
    ChatPanel._append_assistant_text(panel, "Straight to it.")
    assert panel.transcript_writes == ["Straight to it."]
    assert panel.spoken == []
    assert panel.entries == []


def test_sources_are_written_into_the_answer_so_they_are_saved_with_it():
    """Unlike the thinking, what an answer cites is part of the answer."""
    from accessible_ai.ui.chat_panel import ChatPanel

    panel = _streaming_panel()
    ChatPanel._append_assistant_text(panel, "Rain tomorrow.")
    ChatPanel._append_assistant_sources(panel, "Sources (1):\n1. Met - https://met.example")
    assert panel.assistant_buffer == (
        "Rain tomorrow.\n\nSources (1):\n1. Met - https://met.example"
    )


def test_the_thinking_line_says_how_much_there_is_rather_than_reading_it():
    """The arrow keys pass over this line; it must not be the whole essay."""
    from accessible_ai.models import Message
    from accessible_ai.ui.chat_panel import ChatPanel

    from types import SimpleNamespace

    panel = SimpleNamespace()
    panel._history_role_label = ChatPanel._history_role_label
    panel._history_entry_copy_text = lambda entry: entry.content
    label = ChatPanel._history_list_label(
        panel, Message(role="thinking", content="one two three four")
    )
    assert label == "Thinking: 4 words, Ctrl+C to copy"
    assert (
        ChatPanel._history_list_label(panel, Message(role="thinking", content="alone"))
        == "Thinking: 1 word, Ctrl+C to copy"
    )
