from __future__ import annotations

import sqlite3

import wx

from accessible_ai.models import Account, Profile
from accessible_ai.storage.database import Database


class ProfileEditorDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, db: Database, profile: Profile | None = None):
        super().__init__(
            parent,
            title="Conversation Profile",
            size=(760, 700),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.db = db
        self.profile = profile or Profile()
        self.accounts = db.list_accounts()

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=12)
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

        outer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        outer.Add(wx.StaticText(panel, label="System prompt:"), 0, wx.LEFT | wx.RIGHT, 12)
        self.system_prompt = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_RICH2)
        self.system_prompt.SetName("System prompt")
        outer.Add(self.system_prompt, 1, wx.EXPAND | wx.ALL, 12)

        buttons = wx.StdDialogButtonSizer()
        ok_button = wx.Button(panel, wx.ID_OK)
        cancel_button = wx.Button(panel, wx.ID_CANCEL)
        buttons.AddButton(ok_button)
        buttons.AddButton(cancel_button)
        buttons.Realize()
        ok_button.SetDefault()
        outer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(outer)

        self.account.Bind(wx.EVT_CHOICE, self.on_account_changed)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self._load()

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
            size=(620, 460),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.db = db
        self.profiles: list[Profile] = []

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(panel, label="Conversation profiles:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12
        )
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName("Conversation profiles")
        outer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 12)

        row = wx.BoxSizer(wx.HORIZONTAL)
        add = wx.Button(panel, label="&Add")
        edit = wx.Button(panel, label="&Edit")
        duplicate = wx.Button(panel, label="D&uplicate")
        delete = wx.Button(panel, label="&Delete")
        for button in [add, edit, duplicate, delete]:
            row.Add(button, 0, wx.RIGHT, 8)
        outer.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        close = wx.Button(panel, wx.ID_CLOSE, "Close")
        outer.Add(close, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        panel.SetSizer(outer)

        add.Bind(wx.EVT_BUTTON, self.on_add)
        edit.Bind(wx.EVT_BUTTON, self.on_edit)
        duplicate.Bind(wx.EVT_BUTTON, self.on_duplicate)
        delete.Bind(wx.EVT_BUTTON, self.on_delete)
        close.Bind(wx.EVT_BUTTON, lambda evt: self.EndModal(wx.ID_CLOSE))
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit)
        self.reload()

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
