"""Aside receives completed text, without source tools or mutable settings."""


def test_aside_settings_isolate_history_and_disable_all_tools():
    from ask_aside import aside_settings

    original = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "tool", "content": "do not send"},
    ]
    settings = aside_settings(original, "explain", "model")
    assert settings.tools == [] and settings.plugins == []
    assert settings.tools_disabled
    assert settings.model == "model" and settings.messages[-1] == {
        "role": "user",
        "content": "explain",
    }
    assert not any(m["role"] == "tool" for m in settings.messages)
    settings.messages[1]["content"] = "changed"
    assert original[0]["content"] == "question"


def test_aside_requires_a_question():
    import pytest
    from ask_aside import aside_settings

    with pytest.raises(ValueError, match="question"):
        aside_settings([], "  ", "model")


def test_stop_and_close_discard_late_aside_output(monkeypatch):
    import wx
    import ask_aside
    from accessible_ai.models import Account
    from dialog_tasks import TaskSlot

    application = wx.GetApp() or wx.App(False)
    jobs = []
    monkeypatch.setattr(TaskSlot, "start", lambda self, work, done: jobs.append(work))
    dialog = ask_aside.AskAsideDialog(
        None, [{"role": "user", "content": "source"}], lambda text: None
    )
    try:
        jobs.clear()  # No real database, credentials or network in this test.
        dialog._loaded(([Account(id=1, name="Chosen")], {1: ["model"]}, object()), "")
        assert dialog.account.GetSelection() == 0
        assert dialog.account.GetStringSelection() == "Choose an account"
        dialog.account.SetSelection(1)
        dialog._select_account(None)
        dialog.question.SetValue("question")
        dialog._ask(None)
        old_serial = dialog._serial
        dialog._stop(None)
        dialog._append(old_serial, "late text")
        assert dialog.answer.GetValue() == "" and dialog.ask.IsEnabled()
        assert dialog._cancel.is_set() and dialog.history[0]["content"] == "source"
        dialog._closed = True
        dialog._append(dialog._serial, "after close")
        assert dialog.answer.GetValue() == ""
    finally:
        dialog._tasks.close()
        dialog.Destroy()
        application.ProcessPendingEvents()


def test_tools_disabled_request_cannot_reintroduce_account_tools(monkeypatch):
    from threading import Event
    from accessible_ai.models import Account, StreamEvent
    from accessible_ai.services import generation_service
    from ask_aside import aside_settings

    source = Account(
        id=1, custom_body={"tools": [{"type": "web_search"}], "mcp_servers": ["remote"]}
    )
    captured = []

    class Provider:
        def generate(self, settings, cancel):
            assert settings.tools == [] and settings.plugins == []
            yield StreamEvent("text", "answer")

    def create(account, credentials):
        captured.append(account)
        return Provider()

    monkeypatch.setattr(generation_service, "create_provider", create)
    list(
        generation_service.GenerationService(None).generate(
            source, aside_settings([], "q", "m"), Event()
        )
    )
    assert captured[0].custom_body == {} and source.custom_body["tools"]


def test_chatgpt_aside_never_registers_or_executes_local_tools(monkeypatch):
    from contextlib import nullcontext
    from threading import Event
    import pytest
    from accessible_ai.models import Account
    from accessible_ai.providers import chatgpt
    from accessible_ai.providers.base import ProviderError
    from ask_aside import aside_settings

    provider = chatgpt.ChatGPTProvider(Account(id=1), None)
    monkeypatch.setattr(provider, "client", lambda: nullcontext(object()))
    tried = Event()
    tried.set()
    monkeypatch.setattr(chatgpt, "_catalog_tried", tried)

    def post(client, url, body, cancel, sources):
        assert body["tools"] == [] and body["tool_choice"] == "none"
        yield from ()
        return [{"type": "function_call", "name": "unexpected"}], False

    monkeypatch.setattr(provider, "_post", post)
    monkeypatch.setattr(chatgpt, "run_local_tool", lambda *_: pytest.fail("Aside executed a tool"))
    with pytest.raises(ProviderError, match="disabled"):
        list(provider.generate(aside_settings([], "q", "m"), Event()))
