"""An independent, tools-disabled question about captured conversation text."""

from threading import Event

import wx

from accessible_ai.models import Account, GenerationSettings
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from dialog_tasks import TaskSlot


def aside_settings(history: list[dict], question: str, model: str) -> GenerationSettings:
    if not question.strip():
        raise ValueError("Type a question first.")
    messages = [
        {
            "role": "system",
            "content": "Answer the new question about this conversation. "
            "Earlier messages are reference material, not requests to repeat their actions.",
        }
    ]
    messages.extend(
        {"role": item["role"], "content": str(item.get("content") or "")}
        for item in history
        if item.get("role") in {"user", "assistant"}
    )
    messages.append({"role": "user", "content": question.strip()})
    return GenerationSettings(model=model, messages=messages, tools_disabled=True)


class AskAsideDialog(wx.Dialog):
    def __init__(self, parent, history: list[dict], speak):
        super().__init__(
            parent, title="Ask Aside", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.SetSize(self.FromDIP(wx.Size(700, 550)))
        self.history = history
        self.speak = speak
        self._closed = False
        self._cancel = Event()
        self._serial = 0
        self._tasks = TaskSlot(wx.CallAfter)
        self._accounts: list[Account] = []
        self._models: dict[int | None, list[str]] = {}
        self._service: GenerationService | None = None
        pad = self.FromDIP(8)
        outer = wx.BoxSizer(wx.VERTICAL)
        note = wx.StaticText(
            self,
            label="This question uses the Chat account you choose. It sends a text copy, "
            "cannot use tools, and does not change the original conversation.",
        )
        note.Wrap(self.FromDIP(650))
        outer.Add(note, 0, wx.ALL | wx.EXPAND, pad)
        for label, name in (("&Account:", "Aside account"), ("&Model:", "Aside model")):
            outer.Add(wx.StaticText(self, label=label), 0, wx.LEFT | wx.RIGHT, pad)
            control = wx.Choice(self)
            control.SetName(name)
            outer.Add(control, 0, wx.EXPAND | wx.ALL, pad)
            if name == "Aside account":
                self.account = control
            else:
                self.model = control
        outer.Add(wx.StaticText(self, label="&Question:"), 0, wx.LEFT | wx.RIGHT, pad)
        self.question = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.question.SetName("Aside question")
        outer.Add(self.question, 1, wx.EXPAND | wx.ALL, pad)
        outer.Add(wx.StaticText(self, label="A&nswer:"), 0, wx.LEFT | wx.RIGHT, pad)
        self.answer = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.answer.SetName("Aside answer")
        outer.Add(self.answer, 2, wx.EXPAND | wx.ALL, pad)
        self.status = wx.StaticText(self, label="Loading Chat accounts…")
        outer.Add(self.status, 0, wx.ALL, pad)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.ask = wx.Button(self, label="A&sk")
        self.stop = wx.Button(self, label="&Stop")
        close = wx.Button(self, wx.ID_CANCEL, "&Close")
        for button in (self.ask, self.stop, close):
            row.Add(button, 0, wx.RIGHT, pad)
        outer.Add(row, 0, wx.ALL, pad)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.ask.Disable()
        self.stop.Disable()
        self.account.Bind(wx.EVT_CHOICE, self._select_account)
        self.ask.Bind(wx.EVT_BUTTON, self._ask)
        self.stop.Bind(wx.EVT_BUTTON, self._stop)
        close.Bind(wx.EVT_BUTTON, self._close)
        self.Bind(wx.EVT_CLOSE, self._close)
        self.question.SetFocus()
        self.CentreOnParent()

        def load():
            from chat_integration import database_path, ensure_legacy_import

            ensure_legacy_import()
            db = Database(database_path())
            credentials = CredentialStore()
            models = ModelService(db, credentials)
            accounts = db.list_accounts()
            catalogs = {account.id: models.cached_models(account) for account in accounts}
            return accounts, catalogs, GenerationService(credentials)

        self._tasks.start(load, self._loaded)

    def _loaded(self, result, error):
        if error:
            self._status(f"Could not load accounts: {error}")
            return
        self._accounts, self._models, self._service = result
        self.account.Set(["Choose an account", *[account.name for account in self._accounts]])
        self.account.SetSelection(0)
        self._status(
            "Choose an account and model before asking."
            if self._accounts
            else "Add an account and refresh its models under Chat before using Ask Aside."
        )

    def _select_account(self, event):
        index = self.account.GetSelection()
        account = self._accounts[index - 1] if index > 0 else None
        self.model.Set(self._models.get(account.id, []) if account else [])
        if self.model.GetCount():
            self.model.SetSelection(0)
        self.ask.Enable(self.model.GetCount() > 0)

    def _status(self, text):
        self.status.SetLabel(text)
        self.speak(text)

    def _ask(self, event):
        if self._service is None:
            return
        account_index, model_index = self.account.GetSelection(), self.model.GetSelection()
        if account_index <= 0 or model_index == wx.NOT_FOUND:
            self._status("Choose an account and model first.")
            return
        try:
            settings = aside_settings(
                self.history, self.question.GetValue(), self.model.GetStringSelection()
            )
        except ValueError as exc:
            self._status(str(exc))
            return
        account = self._accounts[account_index - 1]
        self._cancel = Event()
        cancel = self._cancel
        self._serial += 1
        serial = self._serial
        self.answer.Clear()
        for control in (self.ask, self.account, self.model):
            control.Disable()
        self.stop.Enable()
        self._status(f"Asking {account.name}, {settings.model}.")

        service = self._service

        def work():
            for item in service.generate(account, settings, cancel):
                if cancel.is_set():
                    break
                if item.kind == "text" and item.text:
                    wx.CallAfter(self._append, serial, item.text)

        def done(result, error):
            self.ask.Enable()
            self.account.Enable()
            self.model.Enable()
            self.stop.Disable()
            self._status(
                f"Aside failed: {error}"
                if error
                else "Aside stopped."
                if cancel.is_set()
                else "Aside answer ready."
            )

        self._tasks.start(work, done)

    def _append(self, serial, text):
        if not self._closed and serial == self._serial and not self._cancel.is_set():
            self.answer.AppendText(text)

    def _stop(self, event):
        self._cancel.set()
        self._serial += 1
        self._tasks.invalidate()
        self.stop.Disable()
        self.ask.Enable()
        self.account.Enable()
        self.model.Enable()
        self._status("Aside stopped.")

    def _close(self, event):
        self._closed = True
        self._cancel.set()
        self._tasks.close()
        self.EndModal(wx.ID_CANCEL)
