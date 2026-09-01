from __future__ import annotations

import logging
import mimetypes
import threading
from pathlib import Path
from threading import Event
from typing import Any

import wx

from accessible_ai.models import (
    Account,
    Conversation,
    GenerationSettings,
    Message,
    MessageAttachment,
    Profile,
    PROVIDER_OPENROUTER,
)
from accessible_ai.services.generation_service import GenerationService
from accessible_ai.services.model_service import ModelService
from accessible_ai.storage.credentials import CredentialStore
from accessible_ai.storage.database import Database
from accessible_ai.ui.accounts import AccountsDialog
from accessible_ai.ui.diagnostics import DiagnosticsDialog
from accessible_ai.ui.profiles import ProfilesDialog


logger = logging.getLogger(__name__)

MAX_TOTAL_ATTACHMENT_BYTES = 100 * 1024 * 1024
RESPONSE_ANNOUNCEMENT_INTERVAL_MS = 300


class _ActionState:
    """Small MenuItem-compatible state holder used by the embedded panel."""

    def __init__(self, checked: bool = False):
        self.enabled = True
        self.checked = checked

    def Enable(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def Check(self, checked: bool = True) -> None:
        self.checked = checked

    def IsChecked(self) -> bool:
        return self.checked


class ChatPanel(wx.Panel):
    """AccessibleAI's provider chat experience embedded in BlindPilot."""

    def __init__(
        self,
        parent: wx.Window,
        db: Database,
        credentials: CredentialStore,
        model_service: ModelService,
        generation_service: GenerationService,
        set_status,
        speak,
    ):
        super().__init__(parent)
        self.db = db
        self.credentials = credentials
        self.model_service = model_service
        self.generation_service = generation_service
        self._set_status = set_status
        self._speak = speak

        self.accounts: list[Account] = []
        self.profiles: list[Profile] = []
        self.current_conversation_id: int | None = None
        self.current_system_prompt = ""
        self.current_profile_id: int | None = None
        self.generation_cancel: Event | None = None
        self.generating = False
        self.assistant_buffer = ""
        self.last_generation_status = ""
        self.assistant_announcement_buffer = ""
        self.assistant_announcement_timer: wx.CallLater | None = None
        self.pending_attachments: list[MessageAttachment] = []
        self.history_entries: list[Message] = []
        self.history_view = "list"
        self.regenerating_message_id: int | None = None
        self.closing = False

        self.regenerate_item = _ActionState()
        self.refresh_models_item = _ActionState()
        self.history_list_view_item = _ActionState(checked=True)
        self.history_text_view_item = _ActionState()
        self._build_ui()
        self.SetStatusText("Ready")
        self.reload_accounts()
        self.reload_profiles()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        self.message_input.SetFocus()

    def SetStatusText(self, text: str) -> None:
        self._set_status(text)

    def shutdown(self) -> None:
        self.closing = True
        self._cancel_response_announcement_timer()
        self.assistant_announcement_buffer = ""
        if self.generation_cancel:
            self.generation_cancel.set()

    def _build_ui(self) -> None:
        panel = self
        outer = wx.BoxSizer(wx.VERTICAL)

        selectors = wx.FlexGridSizer(cols=4, vgap=6, hgap=8)
        selectors.AddGrowableCol(1, 1)
        selectors.AddGrowableCol(3, 1)

        selectors.Add(wx.StaticText(panel, label="Profile:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.profile_choice = wx.Choice(panel)
        self.profile_choice.SetName("Conversation profile")
        selectors.Add(self.profile_choice, 1, wx.EXPAND)

        selectors.Add(wx.StaticText(panel, label="Account:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.account_choice = wx.Choice(panel)
        self.account_choice.SetName("Account")
        selectors.Add(self.account_choice, 1, wx.EXPAND)

        outer.Add(selectors, 0, wx.EXPAND | wx.ALL, 10)

        transcript_label = wx.StaticText(panel, label="History:")
        outer.Add(transcript_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.history_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.history_list.SetName("History")
        outer.Add(self.history_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.transcript = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        self.transcript.SetName("History")
        outer.Add(self.transcript, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.transcript.Hide()

        input_label = wx.StaticText(panel, label="Message:")
        outer.Add(input_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.message_input = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_RICH2, size=(-1, 90))
        self.message_input.SetName("Message")
        outer.Add(self.message_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        model_row = wx.BoxSizer(wx.HORIZONTAL)
        model_row.Add(
            wx.StaticText(panel, label="Model:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8
        )
        self.model_combo = wx.ComboBox(panel, style=wx.CB_DROPDOWN)
        self.model_combo.SetName("Model")
        model_row.Add(self.model_combo, 1, wx.EXPAND)
        outer.Add(model_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.model_combo.MoveAfterInTabOrder(self.message_input)

        self.attachment_label = wx.StaticText(panel, label="Attachments:")
        outer.Add(self.attachment_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.attachment_list = wx.ListBox(panel, style=wx.LB_EXTENDED, size=(-1, 62))
        outer.Add(self.attachment_list, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        self.attachment_label.Hide()
        self.attachment_list.Hide()

        attachment_row = wx.BoxSizer(wx.HORIZONTAL)
        self.add_files_button = wx.Button(panel, label="&Add files...")
        self.remove_files_button = wx.Button(panel, label="Re&move selected")
        self.clear_files_button = wx.Button(panel, label="&Clear all")
        self.remove_files_button.Disable()
        self.clear_files_button.Disable()
        attachment_row.Add(self.add_files_button, 0, wx.RIGHT, 8)
        attachment_row.Add(self.remove_files_button, 0, wx.RIGHT, 8)
        attachment_row.Add(self.clear_files_button, 0)
        outer.Add(attachment_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.send_button = wx.Button(panel, label="&Send")
        self.regenerate_button = wx.Button(panel, label="&Regenerate response")
        self.stop_button = wx.Button(panel, label="S&top generation")
        self.new_button = wx.Button(panel, label="&New conversation")
        self.regenerate_button.Disable()
        self.stop_button.Disable()
        button_row.Add(self.send_button, 0, wx.RIGHT, 8)
        button_row.Add(self.regenerate_button, 0, wx.RIGHT, 8)
        button_row.Add(self.stop_button, 0, wx.RIGHT, 8)
        button_row.Add(self.new_button, 0)
        outer.Add(button_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(outer)

        self.send_button.Bind(wx.EVT_BUTTON, self.on_send)
        self.regenerate_button.Bind(wx.EVT_BUTTON, self.on_regenerate)
        self.stop_button.Bind(wx.EVT_BUTTON, self.on_stop)
        self.new_button.Bind(wx.EVT_BUTTON, self.on_new_conversation)
        self.add_files_button.Bind(wx.EVT_BUTTON, self.on_add_files)
        self.remove_files_button.Bind(wx.EVT_BUTTON, self.on_remove_files)
        self.clear_files_button.Bind(wx.EVT_BUTTON, self.on_clear_files)
        self.attachment_list.Bind(wx.EVT_LISTBOX, self.on_attachment_selection)
        self.account_choice.Bind(wx.EVT_CHOICE, self.on_account_changed)
        self.profile_choice.Bind(wx.EVT_CHOICE, self.on_profile_changed)
        self.transcript.Bind(wx.EVT_SET_FOCUS, self.on_history_focus)
        self.history_list.Bind(wx.EVT_SET_FOCUS, self.on_history_focus)
        self.history_list.Bind(wx.EVT_CONTEXT_MENU, self.on_history_context_menu)
        self.history_list.Bind(wx.EVT_KEY_DOWN, self.on_history_list_key_down)

    def on_history_list_view(self, event: wx.CommandEvent | None) -> None:
        self._set_history_view("list")

    def on_history_text_view(self, event: wx.CommandEvent | None) -> None:
        self._set_history_view("text")

    def _set_history_view(self, view: str) -> None:
        if view not in {"list", "text"}:
            raise ValueError(f"Unknown history view: {view}")
        old_control = self.history_list if self.history_view == "list" else self.transcript
        new_control = self.history_list if view == "list" else self.transcript
        move_focus = old_control.HasFocus()
        self.history_view = view
        self.history_list.Show(view == "list")
        self.transcript.Show(view == "text")
        self.history_list_view_item.Check(view == "list")
        self.history_text_view_item.Check(view == "text")
        new_control.GetParent().Layout()
        if move_focus:
            new_control.SetFocus()

    @staticmethod
    def _history_role_label(role: str) -> str:
        if role == "user":
            return "You"
        if role == "assistant":
            return "Assistant"
        return role.title()

    def _history_entry_copy_text(self, entry: Message) -> str:
        return f"{entry.content}{self._attachment_display_lines(entry.attachments)}".strip()

    def _history_list_label(self, entry: Message) -> str:
        label = self._history_role_label(entry.role)
        text = " ".join(self._history_entry_copy_text(entry).split())
        if not text:
            text = "(waiting for response)" if entry.role == "assistant" else "(empty message)"
        return f"{label}: {text}"

    def _replace_history_entries(self, entries: list[Message]) -> None:
        previous_index = self.history_list.GetSelection()
        previous_id = None
        if 0 <= previous_index < len(self.history_entries):
            previous_id = self.history_entries[previous_index].id
        reviewing_list = self.history_list.HasFocus()
        self.history_entries = list(entries)
        self.history_list.Set([self._history_list_label(entry) for entry in self.history_entries])
        if not self.history_entries:
            return
        selection = wx.NOT_FOUND
        if previous_id is not None:
            for index, entry in enumerate(self.history_entries):
                if entry.id == previous_id:
                    selection = index
                    break
        if selection == wx.NOT_FOUND and reviewing_list and previous_index != wx.NOT_FOUND:
            selection = min(previous_index, len(self.history_entries) - 1)
        if selection == wx.NOT_FOUND:
            selection = len(self.history_entries) - 1
        self.history_list.SetSelection(selection)

    def _append_history_entry(self, entry: Message) -> None:
        self.history_entries.append(entry)
        self.history_list.Append(self._history_list_label(entry))
        if not self.history_list.HasFocus():
            self.history_list.SetSelection(len(self.history_entries) - 1)

    def _record_history_status(self, text: str) -> None:
        """Write a status line into History while a response is still coming.

        The unsaved assistant entry has to stay last, because that is the one
        the streamed text is written into, so this goes in front of it rather
        than on the end. Whatever was selected stays selected.
        """
        entry = Message(role="status", content=text)
        index = len(self.history_entries)
        last = self.history_entries[-1] if self.history_entries else None
        if last is not None and last.role == "assistant" and last.id is None:
            index -= 1
        selection = self.history_list.GetSelection()
        self.history_entries.insert(index, entry)
        self.history_list.Insert(self._history_list_label(entry), index)
        if selection != wx.NOT_FOUND:
            # An item inserted in front of the selected one moves it down a
            # place. Some builds follow it on their own; make sure of it either
            # way rather than leaving the selection on a different item.
            wanted = selection + 1 if selection >= index else selection
            if self.history_list.GetSelection() != wanted:
                self.history_list.SetSelection(wanted)
        self._append_history_text(f"[{text}]\r\n")

    def _update_streaming_history_entry(self) -> None:
        if not self.history_entries:
            return
        index = len(self.history_entries) - 1
        entry = self.history_entries[index]
        if entry.role != "assistant" or entry.id is not None:
            return
        entry.content = self.assistant_buffer
        self.history_list.SetString(index, self._history_list_label(entry))

    def _selected_history_entry(self) -> tuple[int, Message] | None:
        index = self.history_list.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.history_entries):
            return None
        return index, self.history_entries[index]

    def on_history_context_menu(self, event: wx.ContextMenuEvent) -> None:
        position = event.GetPosition()
        if position.x >= 0 and position.y >= 0:
            index = self.history_list.HitTest(self.history_list.ScreenToClient(position))
            if index != wx.NOT_FOUND:
                self.history_list.SetSelection(index)
        selected = self._selected_history_entry()
        if selected is None:
            self.SetStatusText("There is no history item to copy or edit.")
            return

        menu = wx.Menu()
        copy_item = menu.Append(wx.ID_COPY, "&Copy")
        edit_item = menu.Append(wx.ID_EDIT, "&Edit...")
        _, entry = selected
        edit_item.Enable(entry.id is not None and entry.role != "status" and not self.generating)
        menu.Bind(wx.EVT_MENU, self.on_copy_history_item, copy_item)
        menu.Bind(wx.EVT_MENU, self.on_edit_history_item, edit_item)
        try:
            self.history_list.PopupMenu(menu)
        finally:
            menu.Destroy()

    def on_history_list_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (ord("C"), ord("c")) and event.ControlDown():
            self.on_copy_history_item(None)
            return
        event.Skip()

    def on_copy_history_item(self, event: wx.CommandEvent | None) -> None:
        selected = self._selected_history_entry()
        if selected is None:
            self.SetStatusText("There is no history item to copy.")
            return
        _, entry = selected
        data = wx.TextDataObject(self._history_entry_copy_text(entry))
        if not wx.TheClipboard.Open():
            self.SetStatusText("Could not open the clipboard.")
            return
        copied = False
        try:
            copied = wx.TheClipboard.SetData(data)
            if copied:
                wx.TheClipboard.Flush()
        finally:
            wx.TheClipboard.Close()
        self.SetStatusText("History item copied." if copied else "Could not copy the history item.")

    def _prompt_for_history_edit(self, entry: Message) -> str | None:
        label = self._history_role_label(entry.role)
        dialog = wx.Dialog(self, title=f"Edit {label}", size=(650, 450))
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(
            wx.StaticText(dialog, label=f"{label} text:"),
            0,
            wx.LEFT | wx.TOP | wx.RIGHT,
            10,
        )
        text = wx.TextCtrl(dialog, style=wx.TE_MULTILINE | wx.TE_RICH2)
        text.SetName(f"{label} text")
        text.SetValue(entry.content)
        outer.Add(text, 1, wx.EXPAND | wx.LEFT | wx.TOP | wx.RIGHT, 10)
        buttons = dialog.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
        if buttons is not None:
            outer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
        dialog.SetSizer(outer)
        dialog.SetMinSize((500, 300))
        dialog.CentreOnParent()
        text.SetFocus()
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return text.GetValue()
        finally:
            dialog.Destroy()

    def on_edit_history_item(self, event: wx.CommandEvent | None) -> None:
        selected = self._selected_history_entry()
        if selected is None:
            self.SetStatusText("There is no history item to edit.")
            return
        _, entry = selected
        if entry.id is None or entry.role == "status":
            self.SetStatusText("This history item cannot be edited.")
            return
        if self.generating:
            self.SetStatusText("Stop generation before editing history.")
            return
        replacement = self._prompt_for_history_edit(entry)
        if replacement is None:
            self.history_list.SetFocus()
            return
        message_id = int(entry.id)
        self.db.update_message_content(message_id, replacement)
        self._render_conversation()
        for new_index, candidate in enumerate(self.history_entries):
            if candidate.id == message_id:
                self.history_list.SetSelection(new_index)
                break
        if self.history_view == "list":
            self.history_list.SetFocus()
        self.SetStatusText("History item updated.")

    @staticmethod
    def _format_file_size(size: int) -> str:
        value = float(size)
        for unit in ("bytes", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} bytes"

    def _refresh_attachment_list(self) -> None:
        labels = []
        for attachment in self.pending_attachments:
            location = attachment.source_path or attachment.filename
            labels.append(f"{location}, {self._format_file_size(len(attachment.data))}")
        self.attachment_list.Set(labels)
        has_attachments = bool(self.pending_attachments)
        visibility_changed = self.attachment_list.IsShown() != has_attachments
        self.attachment_label.Show(has_attachments)
        self.attachment_list.Show(has_attachments)
        self.clear_files_button.Enable(has_attachments and not self.generating)
        self.remove_files_button.Enable(False)
        if visibility_changed:
            self.attachment_list.GetParent().Layout()

    def on_add_files(self, event: wx.CommandEvent) -> None:
        dialog = wx.FileDialog(
            self,
            message="Attach files",
            wildcard="All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                self.add_files_button.SetFocus()
                return
            selected_paths = dialog.GetPaths()
        finally:
            dialog.Destroy()

        existing_paths = {
            attachment.source_path.casefold() for attachment in self.pending_attachments
        }
        total_size = sum(len(attachment.data) for attachment in self.pending_attachments)
        added = 0
        errors: list[str] = []
        for selected_path in selected_paths:
            path = Path(selected_path)
            resolved = str(path.resolve())
            if resolved.casefold() in existing_paths:
                continue
            try:
                size = path.stat().st_size
                if total_size + size > MAX_TOTAL_ATTACHMENT_BYTES:
                    errors.append(
                        f"{path.name}: adding this file would exceed the 100 MB total attachment limit."
                    )
                    continue
                data = path.read_bytes()
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.pending_attachments.append(
                MessageAttachment(
                    filename=path.name,
                    mime_type=mime_type,
                    data=data,
                    source_path=resolved,
                )
            )
            existing_paths.add(resolved.casefold())
            total_size += len(data)
            added += 1

        self._refresh_attachment_list()
        if self.pending_attachments:
            self.attachment_list.SetSelection(len(self.pending_attachments) - 1)
            self.on_attachment_selection(None)
            self.attachment_list.SetFocus()
        else:
            self.add_files_button.SetFocus()
        if added:
            self.SetStatusText(f"Added {added} files; {len(self.pending_attachments)} attached.")
        if errors:
            wx.MessageBox("\n".join(errors), "Could Not Attach Files", wx.OK | wx.ICON_ERROR, self)

    def on_attachment_selection(self, event: wx.CommandEvent | None) -> None:
        self.remove_files_button.Enable(
            bool(self.attachment_list.GetSelections()) and not self.generating
        )
        if event is not None:
            event.Skip()

    def on_remove_files(self, event: wx.CommandEvent) -> None:
        selections = list(self.attachment_list.GetSelections())
        if not selections:
            self.attachment_list.SetFocus()
            return
        next_index = min(selections[0], len(self.pending_attachments) - len(selections) - 1)
        for index in reversed(selections):
            del self.pending_attachments[index]
        self._refresh_attachment_list()
        if self.pending_attachments:
            next_index = max(0, min(next_index, len(self.pending_attachments) - 1))
            self.attachment_list.SetSelection(next_index)
            self.on_attachment_selection(None)
            self.attachment_list.SetFocus()
        else:
            self.add_files_button.SetFocus()
        self.SetStatusText(f"{len(self.pending_attachments)} files attached.")

    def on_clear_files(self, event: wx.CommandEvent) -> None:
        self.pending_attachments.clear()
        self._refresh_attachment_list()
        self.add_files_button.SetFocus()
        self.SetStatusText("Attachments cleared.")

    def selected_account(self) -> Account | None:
        index = self.account_choice.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.accounts):
            return None
        return self.accounts[index]

    def selected_profile(self) -> Profile | None:
        index = self.profile_choice.GetSelection()
        if index <= 0:
            return None
        profile_index = index - 1
        if profile_index >= len(self.profiles):
            return None
        return self.profiles[profile_index]

    def reload_accounts(self) -> None:
        previous_id = (
            self.selected_account().id if self.accounts and self.selected_account() else None
        )
        self.accounts = self.db.list_accounts()
        self.account_choice.Set([account.name for account in self.accounts])
        selection = wx.NOT_FOUND
        if previous_id is not None:
            for index, account in enumerate(self.accounts):
                if account.id == previous_id:
                    selection = index
                    break
        if selection == wx.NOT_FOUND and self.accounts:
            selection = 0
        if selection != wx.NOT_FOUND:
            self.account_choice.SetSelection(selection)
            self.load_cached_models()
        else:
            self.model_combo.Set([])
            self.model_combo.SetValue("")

    def reload_profiles(self) -> None:
        previous = self.selected_profile().id if self.profiles and self.selected_profile() else None
        self.profiles = self.db.list_profiles()
        self.profile_choice.Set(["No profile"] + [profile.name for profile in self.profiles])
        selection = 0
        if previous is not None:
            for index, profile in enumerate(self.profiles, start=1):
                if profile.id == previous:
                    selection = index
                    break
        self.profile_choice.SetSelection(selection)

    def load_cached_models(self, preferred_model: str = "") -> None:
        account = self.selected_account()
        if not account or account.id is None:
            self.model_combo.Set([])
            self.model_combo.SetValue("")
            return
        models = self.model_service.cached_models(account)
        self.model_combo.Set(models)
        preferred = preferred_model or account.default_model
        if preferred:
            self.model_combo.SetValue(preferred)
        elif models:
            self.model_combo.SetSelection(0)

    def on_account_changed(self, event: wx.CommandEvent) -> None:
        self.load_cached_models()
        event.Skip()

    def on_profile_changed(self, event: wx.CommandEvent) -> None:
        profile = self.selected_profile()
        if profile and profile.default_account_id is not None:
            for index, account in enumerate(self.accounts):
                if account.id == profile.default_account_id:
                    self.account_choice.SetSelection(index)
                    self.load_cached_models(profile.default_model)
                    break
        elif profile and profile.default_model:
            self.model_combo.SetValue(profile.default_model)
        if self.current_conversation_id is not None:
            self.SetStatusText("Profile selection will apply to the next new conversation.")
        event.Skip()

    def on_accounts(self, event: wx.CommandEvent) -> None:
        dialog = AccountsDialog(self, self.db, self.credentials, self.model_service)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        self.reload_accounts()
        self.reload_profiles()

    def on_profiles(self, event: wx.CommandEvent) -> None:
        dialog = ProfilesDialog(self, self.db)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
        self.reload_profiles()

    def on_diagnostics(self, event: wx.CommandEvent) -> None:
        dialog = DiagnosticsDialog(self)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def on_refresh_models(self, event: wx.CommandEvent) -> None:
        account = self.selected_account()
        if not account:
            wx.MessageBox(
                "Add or select an account first.",
                "Refresh Models",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self.SetStatusText("Refreshing models...")
        self.refresh_models_item.Enable(False)

        def work() -> None:
            try:
                models = self.model_service.refresh_models(account)
                if not self.closing:
                    wx.CallAfter(self._models_refreshed, account.id, models, None)
            except Exception as exc:
                logger.exception("Model refresh failed for account name=%s", account.name)
                if not self.closing:
                    wx.CallAfter(self._models_refreshed, account.id, [], exc)

        threading.Thread(target=work, daemon=True).start()

    def _models_refreshed(
        self, account_id: int | None, models: list[str], error: Exception | None
    ) -> None:
        if self.closing:
            return
        self.refresh_models_item.Enable(True)
        current = self.selected_account()
        if error:
            self.SetStatusText(f"Model refresh failed: {error}")
            wx.MessageBox(str(error), "Refresh Models", wx.OK | wx.ICON_ERROR, self)
            return
        if current and current.id == account_id:
            previous = self.model_combo.GetValue()
            self.model_combo.Set(models)
            if previous:
                self.model_combo.SetValue(previous)
            elif current.default_model:
                self.model_combo.SetValue(current.default_model)
            elif models:
                self.model_combo.SetSelection(0)
        self.SetStatusText(f"Model list refreshed. {len(models)} models available.")

    def on_new_conversation(self, event: wx.CommandEvent | None) -> None:
        if self.generating:
            wx.MessageBox(
                "Stop the current generation before starting a new conversation.",
                "New Conversation",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self.current_conversation_id = None
        self.current_system_prompt = ""
        self.current_profile_id = None
        self.transcript.Clear()
        self._replace_history_entries([])
        self.message_input.Clear()
        self.pending_attachments.clear()
        self._refresh_attachment_list()
        self.regenerating_message_id = None
        self._update_regenerate_enabled()
        self.SetStatusText("New conversation")
        self.message_input.SetFocus()

    def _ensure_conversation(self, first_message: str, account: Account, model: str) -> None:
        if self.current_conversation_id is not None:
            return
        profile = self.selected_profile()
        self.current_system_prompt = profile.system_prompt if profile else ""
        self.current_profile_id = profile.id if profile else None
        title = " ".join(first_message.split())[:80] or "New conversation"
        conversation = Conversation(
            title=title,
            profile_id=self.current_profile_id,
            account_id=account.id,
            model=model,
            system_prompt_snapshot=self.current_system_prompt,
        )
        self.current_conversation_id = self.db.create_conversation(conversation)

    def _generation_settings(
        self,
        account: Account,
        model: str,
        exclude_message_id: int | None = None,
    ) -> GenerationSettings:
        if self.current_conversation_id is None:
            raise RuntimeError("Conversation has not been created")
        messages: list[dict[str, Any]] = []
        if self.current_system_prompt.strip():
            messages.append({"role": "system", "content": self.current_system_prompt})
        for message in self.db.list_messages(self.current_conversation_id):
            if message.id == exclude_message_id:
                continue
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.attachments:
                item["attachments"] = message.attachments
            messages.append(item)

        profile = None
        if self.current_profile_id is not None:
            profile = self.db.get_profile(self.current_profile_id)
        streaming = (
            account.streaming if not profile or profile.streaming is None else profile.streaming
        )
        return GenerationSettings(
            model=model,
            messages=messages,
            temperature=profile.temperature if profile else None,
            max_output_tokens=profile.max_output_tokens if profile else None,
            streaming=streaming,
        )

    def _last_assistant_message(self) -> Message | None:
        if self.current_conversation_id is None:
            return None
        messages = self.db.list_messages(self.current_conversation_id)
        if not messages or messages[-1].role != "assistant" or messages[-1].id is None:
            return None
        return messages[-1]

    def _update_regenerate_enabled(self) -> None:
        enabled = not self.generating and self._last_assistant_message() is not None
        self.regenerate_button.Enable(enabled)
        self.regenerate_item.Enable(enabled)

    @staticmethod
    def _attachment_display_lines(attachments: list[MessageAttachment]) -> str:
        if not attachments:
            return ""
        return "".join(
            f"\r\n[Attached: {attachment.filename}, {ChatPanel._format_file_size(len(attachment.data))}]"
            for attachment in attachments
        )

    def _render_conversation(
        self, exclude_message_id: int | None = None, add_assistant_heading: bool = False
    ) -> None:
        if self.current_conversation_id is None:
            self._replace_history_text("")
            self._replace_history_entries([])
            return
        rendered: list[str] = []
        history_entries: list[Message] = []
        for message in self.db.list_messages(self.current_conversation_id):
            if message.id == exclude_message_id:
                continue
            label = self._history_role_label(message.role)
            rendered.append(
                f"{label}:\r\n{message.content}{self._attachment_display_lines(message.attachments)}"
            )
            history_entries.append(message)
        if add_assistant_heading:
            rendered.append("Assistant:\r\n")
            history_entries.append(
                Message(
                    conversation_id=self.current_conversation_id,
                    role="assistant",
                    content="",
                )
            )
        self._replace_history_text("\r\n\r\n".join(rendered))
        self._replace_history_entries(history_entries)

    def _history_review_selection(self) -> tuple[int, int] | None:
        if not self.transcript.HasFocus():
            return None
        return self.transcript.GetSelection()

    def _restore_history_review_selection(self, selection: tuple[int, int] | None) -> None:
        if selection is None:
            self.transcript.SetInsertionPointEnd()
            return
        last_position = self.transcript.GetLastPosition()
        start, end = selection
        self.transcript.SetSelection(min(start, last_position), min(end, last_position))

    def _append_history_text(self, text: str) -> None:
        selection = self._history_review_selection()
        self.transcript.AppendText(text)
        self._restore_history_review_selection(selection)

    def _replace_history_text(self, text: str) -> None:
        selection = self._history_review_selection()
        self.transcript.SetValue(text)
        self._restore_history_review_selection(selection)

    def on_send(self, event: wx.CommandEvent | None) -> None:
        account = self.selected_account()
        model = self.model_combo.GetValue().strip()
        user_text = self.message_input.GetValue().strip()
        logger.info(
            "Send invoked account_selected=%s model_selected=%s message_characters=%d generating=%s",
            account is not None,
            bool(model),
            len(user_text),
            self.generating,
        )
        if self.generating:
            self.SetStatusText("A response is already being generated.")
            return
        if not account:
            wx.MessageBox(
                "Add or select an account first.", "Send", wx.OK | wx.ICON_INFORMATION, self
            )
            return
        if not model:
            wx.MessageBox(
                "Select or enter a model first.", "Send", wx.OK | wx.ICON_INFORMATION, self
            )
            self.model_combo.SetFocus()
            return
        if not user_text and not self.pending_attachments:
            wx.MessageBox(
                "Enter a message or attach a file first.", "Send", wx.OK | wx.ICON_INFORMATION, self
            )
            self.message_input.SetFocus()
            return
        if self.pending_attachments and account.provider != PROVIDER_OPENROUTER:
            wx.MessageBox(
                "File attachments are currently supported for OpenRouter accounts. Select an OpenRouter account "
                "or clear the pending attachments.",
                "Attachments",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.account_choice.SetFocus()
            return

        self._ensure_conversation(user_text, account, model)
        assert self.current_conversation_id is not None
        attachments = list(self.pending_attachments)
        user_message = Message(
            conversation_id=self.current_conversation_id,
            role="user",
            content=user_text,
            attachments=attachments,
        )
        self.db.add_message(user_message)
        settings = self._generation_settings(account, model)

        if self.transcript.GetValue():
            self._append_history_text("\r\n\r\n")
        self._append_history_text(
            f"You:\r\n{user_text}{self._attachment_display_lines(attachments)}\r\n\r\nAssistant:\r\n"
        )
        self._append_history_entry(user_message)
        self._append_history_entry(
            Message(
                conversation_id=self.current_conversation_id,
                role="assistant",
                content="",
            )
        )
        self.message_input.Clear()
        self.pending_attachments.clear()
        self._refresh_attachment_list()
        self.regenerating_message_id = None
        self._start_generation(account, model, settings, regenerating=False)

    def on_regenerate(self, event: wx.CommandEvent | None) -> None:
        if self.generating:
            self.SetStatusText("A response is already being generated.")
            return
        account = self.selected_account()
        model = self.model_combo.GetValue().strip()
        if not account:
            wx.MessageBox(
                "Add or select an account first.", "Regenerate", wx.OK | wx.ICON_INFORMATION, self
            )
            self.account_choice.SetFocus()
            return
        if not model:
            wx.MessageBox(
                "Select or enter a model first.", "Regenerate", wx.OK | wx.ICON_INFORMATION, self
            )
            self.model_combo.SetFocus()
            return
        previous = self._last_assistant_message()
        if previous is None or previous.id is None:
            wx.MessageBox(
                "There is no completed assistant response to regenerate.",
                "Regenerate",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            self.message_input.SetFocus()
            return

        self.regenerating_message_id = int(previous.id)
        settings = self._generation_settings(
            account, model, exclude_message_id=self.regenerating_message_id
        )
        self._render_conversation(
            exclude_message_id=self.regenerating_message_id, add_assistant_heading=True
        )
        self._start_generation(account, model, settings, regenerating=True)

    def _start_generation(
        self,
        account: Account,
        model: str,
        settings: GenerationSettings,
        regenerating: bool,
    ) -> None:

        self.generating = True
        self.assistant_buffer = ""
        self.last_generation_status = ""
        self._cancel_response_announcement_timer()
        self.assistant_announcement_buffer = ""
        self.generation_cancel = Event()
        self.regenerate_button.Disable()
        self.regenerate_item.Enable(False)
        self.add_files_button.Disable()
        self.remove_files_button.Disable()
        self.clear_files_button.Disable()
        self.stop_button.Enable()
        action = "Regenerating fresh response" if regenerating else "Generating"
        self.SetStatusText(f"{action} with {account.name}, {model}...")
        logger.info(
            "%s started provider=%s account=%s model=%s",
            "Regeneration" if regenerating else "Generation",
            account.provider,
            account.name,
            model,
        )

        def work() -> None:
            error: Exception | None = None
            try:
                for stream_event in self.generation_service.generate(
                    account, settings, self.generation_cancel
                ):
                    if stream_event.kind == "status":
                        if stream_event.text:
                            wx.CallAfter(
                                self._report_generation_status,
                                stream_event.text,
                                bool(stream_event.metadata.get("record")),
                                bool(stream_event.metadata.get("quiet")),
                            )
                        continue
                    if self.generation_cancel.is_set():
                        break
                    if stream_event.kind == "headers":
                        cache_status = self._header_value(
                            stream_event.metadata, "x-openrouter-cache-status"
                        )
                        if cache_status:
                            logger.info("OpenRouter cache status=%s", cache_status)
                    elif stream_event.kind == "text" and stream_event.text:
                        wx.CallAfter(self._append_assistant_text, stream_event.text)
            except Exception as exc:
                error = exc
                logger.exception("Generation failed")
            if not self.closing:
                wx.CallAfter(self._generation_finished, error)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _header_value(headers: dict, name: str) -> str:
        wanted = name.casefold()
        for key, value in headers.items():
            if str(key).casefold() == wanted:
                return str(value)
        return ""

    def _report_generation_status(self, text: str, record: bool, quiet: bool = False) -> None:
        """Report progress that arrives while a response is still being waited for.

        A batch request can take minutes, so the status bar is kept current and
        the active screen reader says each change without focus moving. Only the
        events that name a batch still running at the provider are also written
        into History, where they can be read back afterwards. A quiet event -- a
        repeated "still waiting" -- is shown but not spoken, so a long wait does
        not turn into a nag.
        """
        if self.closing:
            return
        logger.info("Generation status: %s", text)
        self.last_generation_status = text
        self.SetStatusText(text)
        if record:
            self._record_history_status(text)
        if not quiet and self._response_announcements_allowed():
            self._speak(text)

    def _append_assistant_text(self, text: str) -> None:
        if self.closing:
            return
        self.assistant_buffer += text
        self._append_history_text(text)
        self._update_streaming_history_entry()
        self._queue_response_announcement(text)

    def _response_announcements_allowed(self) -> bool:
        return (
            self.GetTopLevelParent().IsActive()
            and self.IsShownOnScreen()
            and not self.GetTopLevelParent().IsIconized()
            and not self._history_has_focus()
        )

    def _history_has_focus(self) -> bool:
        return self.transcript.HasFocus() or self.history_list.HasFocus()

    def on_history_focus(self, event: wx.FocusEvent) -> None:
        self._cancel_response_announcement_timer()
        self.assistant_announcement_buffer = ""
        event.Skip()

    def _cancel_response_announcement_timer(self) -> None:
        timer = self.assistant_announcement_timer
        self.assistant_announcement_timer = None
        if timer is not None and timer.IsRunning():
            timer.Stop()

    def _queue_response_announcement(self, text: str) -> None:
        if not self._response_announcements_allowed():
            self._cancel_response_announcement_timer()
            self.assistant_announcement_buffer = ""
            return
        self.assistant_announcement_buffer += text
        timer = self.assistant_announcement_timer
        if timer is None or not timer.IsRunning():
            self.assistant_announcement_timer = wx.CallLater(
                RESPONSE_ANNOUNCEMENT_INTERVAL_MS,
                self._flush_response_announcement,
            )

    def _flush_response_announcement(self) -> None:
        self._cancel_response_announcement_timer()
        text = self.assistant_announcement_buffer.strip()
        self.assistant_announcement_buffer = ""
        if text and self._response_announcements_allowed():
            self._speak(text)

    def _generation_finished(self, error: Exception | None) -> None:
        if self.closing:
            return
        was_cancelled = bool(self.generation_cancel and self.generation_cancel.is_set())
        regenerated_message_id = self.regenerating_message_id
        was_regenerating = regenerated_message_id is not None
        if (
            self.current_conversation_id is not None
            and self.assistant_buffer
            and not error
            and not was_cancelled
        ):
            if regenerated_message_id is not None:
                self.db.update_message_content(regenerated_message_id, self.assistant_buffer)
            else:
                assistant_message = Message(
                    conversation_id=self.current_conversation_id,
                    role="assistant",
                    content=self.assistant_buffer,
                )
                self.db.add_message(assistant_message)
                if self.history_entries and self.history_entries[-1].id is None:
                    self.history_entries[-1] = assistant_message
                    self.history_list.SetString(
                        len(self.history_entries) - 1,
                        self._history_list_label(assistant_message),
                    )
        self.generating = False
        self.generation_cancel = None
        self.regenerating_message_id = None
        self.stop_button.Disable()
        self.add_files_button.Enable()
        self._refresh_attachment_list()
        self._update_regenerate_enabled()
        self._flush_response_announcement()

        if error:
            if was_regenerating:
                self._render_conversation()
            else:
                self._append_history_text(f"\r\n\r\n[Error: {error}]")
                self._append_history_entry(Message(role="status", content=f"Error: {error}"))
            action = "Regeneration" if was_regenerating else "Generation"
            error_text = str(error)
            if was_regenerating and "cached" in error_text.casefold():
                error_text = "Cached response rejected; the previous response was preserved."
            self.SetStatusText(f"{action} failed: {error_text}")
            wx.MessageBox(error_text, f"{action} Error", wx.OK | wx.ICON_ERROR, self)
        elif was_cancelled:
            if was_regenerating:
                self._render_conversation()
                self.SetStatusText("Regeneration stopped; the previous response was preserved.")
            else:
                self._append_history_text("\r\n\r\n[Generation stopped]")
                self._append_history_entry(Message(role="status", content="Generation stopped"))
                self.SetStatusText(self.last_generation_status or "Generation stopped")
        elif was_regenerating:
            self._render_conversation()
            self.SetStatusText("Response regenerated with a fresh, non-cached result.")
        else:
            self.SetStatusText("Response complete")

    def on_stop(self, event: wx.CommandEvent | None) -> None:
        if self.generation_cancel:
            self.generation_cancel.set()
            self.stop_button.Disable()
            self.SetStatusText("Stopping generation...")

    def on_char_hook(self, event: wx.KeyEvent) -> None:
        key = event.GetKeyCode()
        modifiers = event.GetModifiers()
        if key == wx.WXK_F6 and modifiers == wx.MOD_NONE:
            self._cycle_focus()
            return
        if key == wx.WXK_ESCAPE and self.generating:
            self.on_stop(None)
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and modifiers == wx.MOD_CONTROL:
            if self.message_input.HasFocus():
                self.on_send(None)
                return
        event.Skip()

    def _cycle_focus(self) -> None:
        controls: list[wx.Window] = [
            self.profile_choice,
            self.account_choice,
            self.history_list if self.history_view == "list" else self.transcript,
            self.message_input,
            self.model_combo,
            self.attachment_list,
            self.add_files_button,
            self.remove_files_button,
            self.clear_files_button,
            self.send_button,
            self.regenerate_button,
            self.stop_button,
            self.new_button,
        ]
        controls = [control for control in controls if control.IsShown() and control.IsEnabled()]
        if not controls:
            return
        current_index = -1
        for index, control in enumerate(controls):
            if control.HasFocus():
                current_index = index
                break
        controls[(current_index + 1) % len(controls)].SetFocus()
