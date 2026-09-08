"""Pick a past Chat conversation and carry on with it.

Chat mode wrote every conversation to the database and could open none of them
again: each launch started a new one, and the ones before it were reachable
only by reading the file. This is the way back in.

Laid out like the Accounts and Profiles dialogs rather than like Agent mode's
Recent Conversations, because it belongs to the same package and is read by the
same people: a label, a list whose rows say enough to tell two conversations
apart out loud, and a row of buttons ending in Close.
"""

from __future__ import annotations

import datetime

import wx

from accessible_ai.models import ConversationSummary
from accessible_ai.storage.database import Database

# Sizer borders in device independent pixels. Every one goes through FromDIP.
PAD = 8
PAD_DIALOG = 12

LIST_DIALOG_SIZE = wx.Size(680, 460)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _when(updated_at: str) -> str:
    """The stored timestamp, in this machine's own time rather than the file's.

    SQLite writes CURRENT_TIMESTAMP in UTC, so the raw column is hours away
    from what the clock on the wall said when the conversation was last
    touched. A value that will not parse is passed through untouched: a row
    somebody edited by hand is still a row, and refusing to show it would lose
    the conversation rather than the timestamp.
    """
    text = (updated_at or "").strip()
    if not text:
        return ""
    try:
        stamp = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text
    local = stamp.replace(tzinfo=datetime.timezone.utc).astimezone()
    return local.strftime("%a %d %b %H:%M")


class ConversationsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, db: Database):
        super().__init__(
            parent,
            title="Recent Conversations",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetSize(self.FromDIP(LIST_DIALOG_SIZE))
        self.db = db
        self.conversations: list[ConversationSummary] = []
        self.shown: list[ConversationSummary] = []
        # What the caller opens once this closes with OK.
        self.chosen: ConversationSummary | None = None

        panel = wx.Panel(self)
        pad = panel.FromDIP(PAD)
        pad_dialog = panel.FromDIP(PAD_DIALOG)
        outer = wx.BoxSizer(wx.VERTICAL)

        outer.Add(
            wx.StaticText(panel, label="&Filter:"), 0, wx.LEFT | wx.RIGHT | wx.TOP, pad_dialog
        )
        self.filter_box = wx.TextCtrl(panel)
        self.filter_box.SetName("Filter conversations")
        self.filter_box.SetHint("Type part of a conversation's first message")
        outer.Add(self.filter_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad_dialog)

        outer.Add(
            wx.StaticText(panel, label="Con&versations:"),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            pad_dialog,
        )
        self.listbox = wx.ListBox(panel)
        self.listbox.SetName("Conversations")
        outer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, pad_dialog)

        self.summary = wx.StaticText(panel, label="")
        self.summary.SetName("Summary")
        outer.Add(self.summary, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)

        row = wx.BoxSizer(wx.HORIZONTAL)
        self.open_button = wx.Button(panel, wx.ID_OK, "&Open")
        self.delete_button = wx.Button(panel, label="&Delete")
        for button in (self.open_button, self.delete_button):
            row.Add(button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, pad)
        row.AddStretchSpacer()
        self.close_button = wx.Button(panel, wx.ID_CLOSE, "Close")
        buttons = wx.StdDialogButtonSizer()
        buttons.AddButton(self.close_button)
        buttons.Realize()
        row.Add(buttons, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, pad_dialog)
        panel.SetSizer(outer)

        self.filter_box.Bind(wx.EVT_TEXT, lambda _event: self._refresh())
        self.listbox.Bind(wx.EVT_LISTBOX, lambda _event: self._sync_buttons())
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open)
        self.open_button.Bind(wx.EVT_BUTTON, self.on_open)
        self.delete_button.Bind(wx.EVT_BUTTON, self.on_delete)
        self.close_button.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_CLOSE))
        # Escape presses Close, as it does in every other dialog here.
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_OK)

        self.reload()
        self.filter_box.SetFocus()
        self.CentreOnParent()

    # ----- The list -----

    @staticmethod
    def _row_label(conversation: ConversationSummary) -> str:
        """One row, read in the order somebody listening needs it.

        The title first, because that is what is being looked for; then the
        things that separate two conversations opening with the same words.
        """
        parts = [" ".join(conversation.title.split()) or "Untitled"]
        parts.append(_plural(conversation.message_count, "message"))
        if conversation.profile_name:
            parts.append(f"{conversation.profile_name} profile")
        else:
            parts.append("no profile")
        if conversation.account_name:
            parts.append(conversation.account_name)
        when = _when(conversation.updated_at)
        if when:
            parts.append(when)
        return ", ".join(parts)

    def reload(self, select_id: int | None = None) -> None:
        self.conversations = self.db.list_conversations()
        self._refresh(select_id)

    def _refresh(self, select_id: int | None = None) -> None:
        needle = self.filter_box.GetValue().strip().casefold()
        self.shown = [
            conversation
            for conversation in self.conversations
            if not needle or needle in conversation.title.casefold()
        ]
        self.listbox.Set([self._row_label(conversation) for conversation in self.shown])
        if self.shown:
            selection = 0
            if select_id is not None:
                for index, conversation in enumerate(self.shown):
                    if conversation.id == select_id:
                        selection = index
                        break
            self.listbox.SetSelection(selection)
        # Said in words rather than left to be counted: a filter that matches
        # nothing is otherwise a silent list.
        if not self.conversations:
            self.summary.SetLabel("No conversations yet.")
        elif not self.shown:
            self.summary.SetLabel("No conversations match that filter.")
        else:
            self.summary.SetLabel(f"{_plural(len(self.shown), 'conversation')} listed.")
        self._sync_buttons()

    def selected(self) -> ConversationSummary | None:
        index = self.listbox.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self.shown):
            return None
        return self.shown[index]

    def _sync_buttons(self) -> None:
        has_one = self.selected() is not None
        self.open_button.Enable(has_one)
        self.delete_button.Enable(has_one)

    # ----- What the buttons do -----

    def on_open(self, event: wx.CommandEvent) -> None:
        conversation = self.selected()
        if conversation is None:
            return
        self.chosen = conversation
        self.EndModal(wx.ID_OK)

    def on_delete(self, event: wx.CommandEvent) -> None:
        conversation = self.selected()
        if conversation is None:
            return
        answer = wx.MessageBox(
            f"Delete '{conversation.title}' and its "
            f"{_plural(conversation.message_count, 'message')}?",
            "Delete Conversation",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if answer != wx.YES:
            return
        self.db.delete_conversation(conversation.id)
        self.reload()
        self.listbox.SetFocus()
