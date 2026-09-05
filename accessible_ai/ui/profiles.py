from __future__ import annotations

import sqlite3

import wx

from accessible_ai.models import (
    Account,
    OPENROUTER_SERVER_TOOLS,
    OpenRouterFeatures,
    PDF_ENGINE_LABELS,
    PDF_ENGINE_OFF,
    PDF_ENGINES,
    Profile,
    REASONING_DEFAULT,
    REASONING_EFFORT_LABELS,
    REASONING_EFFORTS,
    SEARCH_CONTEXT_DEFAULT,
    SEARCH_CONTEXT_LABELS,
    SEARCH_CONTEXT_SIZES,
)
from accessible_ai.storage.database import Database

# Sizer borders in device independent pixels. Every one goes through FromDIP.
PAD = 8
PAD_DIALOG = 12

EDITOR_SIZE = wx.Size(760, 700)
SERVER_TOOLS_HEIGHT = 150
LIST_DIALOG_SIZE = wx.Size(620, 460)


# Each picker offers "leave it alone" first, so a profile that says nothing
# about a setting is the one a person lands on without choosing anything.
_REASONING_VALUES = (REASONING_DEFAULT, *REASONING_EFFORTS)
_SEARCH_CONTEXT_VALUES = (SEARCH_CONTEXT_DEFAULT, *SEARCH_CONTEXT_SIZES)
_PDF_ENGINE_VALUES = (PDF_ENGINE_OFF, *PDF_ENGINES)


def _index_of(values: tuple[str, ...], wanted: str) -> int:
    """Where a saved value sits in a picker, or the first row if it is gone."""
    return values.index(wanted) if wanted in values else 0


def _optional_count(text: str, field_name: str) -> int | None:
    """A whole number above zero, or None for a box left empty."""
    text = text.strip()
    if not text:
        return None
    number = int(text)
    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return number


class ProfileEditorDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, db: Database, profile: Profile | None = None):
        super().__init__(
            parent,
            title="Conversation Profile",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetSize(self.FromDIP(EDITOR_SIZE))
        self.db = db
        self.profile = profile or Profile()
        self.accounts = db.list_accounts()

        panel = wx.Panel(self)
        pad = panel.FromDIP(PAD)
        pad_dialog = panel.FromDIP(PAD_DIALOG)
        outer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=pad, hgap=pad_dialog)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label="Profile name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.name = wx.TextCtrl(panel)
        self.name.SetName("Profile name")
        grid.Add(self.name, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Default account:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.account = wx.Choice(
            panel, choices=["Use current account"] + [a.name for a in self.accounts]
        )
        self.account.SetName("Default account")
        grid.Add(self.account, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Default model:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.model = wx.ComboBox(panel, style=wx.CB_DROPDOWN)
        self.model.SetName("Default model")
        grid.Add(self.model, 1, wx.EXPAND)

        grid.Add(
            wx.StaticText(panel, label="Temperature, blank for provider default:"),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.temperature = wx.TextCtrl(panel)
        self.temperature.SetName("Temperature")
        grid.Add(self.temperature, 1, wx.EXPAND)

        grid.Add(
            wx.StaticText(panel, label="Maximum output tokens, blank for provider default:"),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.max_tokens = wx.TextCtrl(panel)
        self.max_tokens.SetName("Maximum output tokens")
        grid.Add(self.max_tokens, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Streaming preference:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.streaming = wx.Choice(
            panel, choices=["Use account setting", "Stream responses", "Do not stream"]
        )
        self.streaming.SetName("Streaming preference")
        grid.Add(self.streaming, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Thinking effort:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.reasoning_effort = wx.Choice(
            panel, choices=[REASONING_EFFORT_LABELS[value] for value in _REASONING_VALUES]
        )
        self.reasoning_effort.SetName("Thinking effort")
        self.reasoning_effort.SetToolTip(
            "How long a reasoning model thinks before it answers. Models that do not think "
            "ignore this."
        )
        grid.Add(self.reasoning_effort, 1, wx.EXPAND)

        grid.Add(
            wx.StaticText(panel, label="Thinking token budget, blank for effort-based:"),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.reasoning_tokens = wx.TextCtrl(panel)
        self.reasoning_tokens.SetName("Thinking token budget")
        grid.Add(self.reasoning_tokens, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Send the thinking back:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.show_reasoning = wx.CheckBox(panel, label="Read the thinking as it arrives")
        self.show_reasoning.SetName("Send the thinking back")
        self.show_reasoning.SetToolTip(
            "Off still lets the model think. It just does not send the thinking back, which "
            "keeps the response shorter and cheaper to read."
        )
        grid.Add(self.show_reasoning, 1, wx.EXPAND)

        grid.Add(
            wx.StaticText(panel, label="Web search results per search:"),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.search_max_results = wx.TextCtrl(panel)
        self.search_max_results.SetName("Web search results per search")
        grid.Add(self.search_max_results, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="Web search depth:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.search_context = wx.Choice(
            panel, choices=[SEARCH_CONTEXT_LABELS[value] for value in _SEARCH_CONTEXT_VALUES]
        )
        self.search_context.SetName("Web search depth")
        self.search_context.SetToolTip("How much of each page found is given to the model.")
        grid.Add(self.search_context, 1, wx.EXPAND)

        grid.Add(
            wx.StaticText(panel, label="Read attached PDFs with:"), 0, wx.ALIGN_CENTER_VERTICAL
        )
        self.pdf_engine = wx.Choice(
            panel, choices=[PDF_ENGINE_LABELS[value] for value in _PDF_ENGINE_VALUES]
        )
        self.pdf_engine.SetName("Read attached PDFs with")
        self.pdf_engine.SetToolTip(
            "Turns an attached PDF into text OpenRouter can hand to any model, rather than "
            "only the models that read one themselves."
        )
        grid.Add(self.pdf_engine, 1, wx.EXPAND)

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, pad_dialog)

        outer.Add(
            wx.StaticText(panel, label="OpenRouter tools the model may call:"),
            0,
            wx.LEFT | wx.RIGHT,
            pad_dialog,
        )
        self.server_tools = wx.CheckListBox(
            panel,
            choices=[
                f"{label} - {description}" for _name, label, description in OPENROUTER_SERVER_TOOLS
            ],
            size=wx.Size(-1, panel.FromDIP(SERVER_TOOLS_HEIGHT)),
        )
        self.server_tools.SetName("OpenRouter tools")
        self.server_tools.SetToolTip(
            "OpenRouter runs each of these itself and gives the model the result. Nothing here "
            "runs on this computer, and nothing stops to ask permission. They apply to "
            "OpenRouter accounts; other providers ignore them."
        )
        outer.Add(self.server_tools, 0, wx.EXPAND | wx.ALL, pad_dialog)
        outer.Add(wx.StaticText(panel, label="System prompt:"), 0, wx.LEFT | wx.RIGHT, pad_dialog)
        self.system_prompt = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_RICH2)
        self.system_prompt.SetName("System prompt")
        outer.Add(self.system_prompt, 1, wx.EXPAND | wx.ALL, pad_dialog)

        buttons = wx.StdDialogButtonSizer()
        ok_button = wx.Button(panel, wx.ID_OK)
        cancel_button = wx.Button(panel, wx.ID_CANCEL)
        buttons.AddButton(ok_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        ok_button.SetDefault()
        outer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)
        panel.SetSizer(outer)

        self.account.Bind(wx.EVT_CHOICE, self.on_account_changed)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self._load()
        self.CentreOnParent()

    def _load(self) -> None:
        self.name.SetValue(self.profile.name)
        account_index = 0
        if self.profile.default_account_id is not None:
            for index, account in enumerate(self.accounts, start=1):
                if account.id == self.profile.default_account_id:
                    account_index = index
                    break
        self.account.SetSelection(account_index)
        self._load_models_for_selected_account()
        self.model.SetValue(self.profile.default_model)
        self.temperature.SetValue(
            "" if self.profile.temperature is None else str(self.profile.temperature)
        )
        self.max_tokens.SetValue(
            "" if self.profile.max_output_tokens is None else str(self.profile.max_output_tokens)
        )
        if self.profile.streaming is None:
            self.streaming.SetSelection(0)
        elif self.profile.streaming:
            self.streaming.SetSelection(1)
        else:
            self.streaming.SetSelection(2)
        self.system_prompt.SetValue(self.profile.system_prompt)
        self._load_openrouter()

    def _load_openrouter(self) -> None:
        features = self.profile.openrouter
        self.reasoning_effort.SetSelection(_index_of(_REASONING_VALUES, features.reasoning_effort))
        self.reasoning_tokens.SetValue(
            "" if features.reasoning_max_tokens is None else str(features.reasoning_max_tokens)
        )
        self.show_reasoning.SetValue(features.show_reasoning)
        self.search_max_results.SetValue(
            "" if features.search_max_results is None else str(features.search_max_results)
        )
        self.search_context.SetSelection(_index_of(_SEARCH_CONTEXT_VALUES, features.search_context))
        self.pdf_engine.SetSelection(_index_of(_PDF_ENGINE_VALUES, features.pdf_engine))
        for index, (name, _label, _description) in enumerate(OPENROUTER_SERVER_TOOLS):
            self.server_tools.Check(index, name in features.server_tools)

    def _selected_account(self) -> Account | None:
        index = self.account.GetSelection()
        if index <= 0:
            return None
        return self.accounts[index - 1]

    def _load_models_for_selected_account(self) -> None:
        account = self._selected_account()
        current = self.model.GetValue() if hasattr(self, "model") else ""
        models = (
            self.db.get_cached_models(int(account.id)) if account and account.id is not None else []
        )
        self.model.Set(models)
        if current:
            self.model.SetValue(current)
        elif account and account.default_model:
            self.model.SetValue(account.default_model)

    def on_account_changed(self, event: wx.CommandEvent) -> None:
        self._load_models_for_selected_account()
        event.Skip()

    def on_ok(self, event: wx.CommandEvent) -> None:
        name = self.name.GetValue().strip()
        if not name:
            wx.MessageBox(
                "Profile name is required.", "Conversation Profile", wx.OK | wx.ICON_ERROR, self
            )
            self.name.SetFocus()
            return

        temperature_text = self.temperature.GetValue().strip()
        max_tokens_text = self.max_tokens.GetValue().strip()
        try:
            temperature = None if not temperature_text else float(temperature_text)
            if temperature is not None and temperature < 0:
                raise ValueError("Temperature cannot be negative.")
            max_tokens = None if not max_tokens_text else int(max_tokens_text)
            if max_tokens is not None and max_tokens <= 0:
                raise ValueError("Maximum output tokens must be greater than zero.")
            reasoning_tokens = _optional_count(
                self.reasoning_tokens.GetValue(), "Thinking token budget"
            )
            search_results = _optional_count(
                self.search_max_results.GetValue(), "Web search results per search"
            )
        except ValueError as exc:
            wx.MessageBox(
                f"Invalid generation setting: {exc}",
                "Conversation Profile",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        selected_account = self._selected_account()
        streaming_selection = self.streaming.GetSelection()
        streaming = None if streaming_selection == 0 else streaming_selection == 1

        self.profile.name = name
        self.profile.system_prompt = self.system_prompt.GetValue()
        self.profile.default_account_id = selected_account.id if selected_account else None
        self.profile.default_model = self.model.GetValue().strip()
        self.profile.temperature = temperature
        self.profile.max_output_tokens = max_tokens
        self.profile.streaming = streaming
        self.profile.openrouter = OpenRouterFeatures(
            server_tools=[
                name
                for index, (name, _label, _description) in enumerate(OPENROUTER_SERVER_TOOLS)
                if self.server_tools.IsChecked(index)
            ],
            search_max_results=search_results,
            search_context=_SEARCH_CONTEXT_VALUES[max(self.search_context.GetSelection(), 0)],
            reasoning_effort=_REASONING_VALUES[max(self.reasoning_effort.GetSelection(), 0)],
            reasoning_max_tokens=reasoning_tokens,
            show_reasoning=self.show_reasoning.GetValue(),
            pdf_engine=_PDF_ENGINE_VALUES[max(self.pdf_engine.GetSelection(), 0)],
        )

        try:
            self.db.save_profile(self.profile)
        except sqlite3.IntegrityError:
            wx.MessageBox(
                "A profile with that name already exists.",
                "Conversation Profile",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.name.SetFocus()
            return
        self.EndModal(wx.ID_OK)


class ProfilesDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, db: Database):
        super().__init__(
            parent,
            title="Conversation Profiles",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetSize(self.FromDIP(LIST_DIALOG_SIZE))
        self.db = db
        self.profiles: list[Profile] = []

        panel = wx.Panel(self)
        pad = panel.FromDIP(PAD)
        pad_dialog = panel.FromDIP(PAD_DIALOG)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(panel, label="Conversation profiles:"),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            pad_dialog,
        )
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName("Conversation profiles")
        outer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, pad_dialog)

        # One row. The actions sit on the left; Close goes through the
        # standard button sizer so it lands where the platform puts it.
        row = wx.BoxSizer(wx.HORIZONTAL)
        add = wx.Button(panel, label="&Add")
        edit = wx.Button(panel, label="&Edit")
        duplicate = wx.Button(panel, label="D&uplicate")
        delete = wx.Button(panel, label="&Delete")
        for button in [add, edit, duplicate, delete]:
            row.Add(button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, pad)
        row.AddStretchSpacer()
        close = wx.Button(panel, wx.ID_CLOSE, "Close")
        buttons = wx.StdDialogButtonSizer()
        buttons.AddButton(close)
        buttons.Realize()
        row.Add(buttons, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)
        panel.SetSizer(outer)

        add.Bind(wx.EVT_BUTTON, self.on_add)
        edit.Bind(wx.EVT_BUTTON, self.on_edit)
        duplicate.Bind(wx.EVT_BUTTON, self.on_duplicate)
        delete.Bind(wx.EVT_BUTTON, self.on_delete)
        close.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        # Escape presses the Close button, as it does in every other dialog.
        self.SetEscapeId(wx.ID_CLOSE)
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit)
        self.reload()
        self.CentreOnParent()

    def reload(self) -> None:
        self.profiles = self.db.list_profiles()
        self.listbox.Set([p.name for p in self.profiles])
        if self.profiles:
            self.listbox.SetSelection(0)

    def selected(self) -> Profile | None:
        index = self.listbox.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.profiles):
            return None
        return self.profiles[index]

    def _edit_dialog(self, profile: Profile | None) -> None:
        dialog = ProfileEditorDialog(self, self.db, profile)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.reload()
        finally:
            dialog.Destroy()

    def on_add(self, event: wx.CommandEvent) -> None:
        self._edit_dialog(None)

    def on_edit(self, event: wx.CommandEvent) -> None:
        profile = self.selected()
        if profile:
            self._edit_dialog(profile)

    def on_duplicate(self, event: wx.CommandEvent) -> None:
        source = self.selected()
        if not source:
            return
        duplicate = Profile(
            name=f"{source.name} Copy",
            system_prompt=source.system_prompt,
            default_account_id=source.default_account_id,
            default_model=source.default_model,
            temperature=source.temperature,
            max_output_tokens=source.max_output_tokens,
            streaming=source.streaming,
        )
        self._edit_dialog(duplicate)

    def on_delete(self, event: wx.CommandEvent) -> None:
        profile = self.selected()
        if not profile or profile.id is None:
            return
        answer = wx.MessageBox(
            f"Delete profile '{profile.name}'?",
            "Delete Profile",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if answer == wx.YES:
            self.db.delete_profile(int(profile.id))
            self.reload()
