"""Export selected conversations without replacing existing files."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tempfile
from threading import Event
from typing import Callable

from markdown_rows import markdown_page


@dataclass(frozen=True)
class ExportEntry:
    title: str
    read: Callable[[], list[tuple[str, str]]]


@dataclass
class ExportResult:
    paths: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def _filename(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    name = " ".join(name.split()).strip(" .")[:100].rstrip(" .") or "Conversation"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name):
        name = f"Conversation {name}"
    return name


def export_many(
    entries: list[ExportEntry],
    destination: Path,
    fmt: str,
    cancel: Event,
    on_progress: Callable[[int, str], None],
) -> ExportResult:
    if fmt not in {"md", "html", "txt"}:
        raise ValueError("Choose Markdown, HTML or plain text.")
    if not destination.is_dir():
        raise ValueError("Choose an existing destination folder.")
    result = ExportResult()
    for number, entry in enumerate(entries, 1):
        if cancel.is_set():
            break
        temporary = None
        try:
            messages = entry.read()
            if not messages:
                raise ValueError("No readable messages were found.")
            if fmt == "txt":
                content = "\n\n".join(f"{role}:\n{text}" for role, text in messages)
            else:
                content = "\n\n".join(
                    f"## {role} {index}\n\n{text}" for index, (role, text) in enumerate(messages, 1)
                )
            if fmt == "html":
                content = markdown_page(content, entry.title)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination,
                prefix=".blindpilot-export-",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(content.rstrip("\n") + "\n")
            if cancel.is_set():
                break
            base = _filename(entry.title)
            for suffix in range(1, 10001):
                name = base if suffix == 1 else f"{base} ({suffix})"
                path = destination / f"{name}.{fmt}"
                try:
                    # Windows rename refuses existing files; POSIX needs a hard link for that guarantee.
                    if os.name == "nt":
                        os.rename(temporary, path)
                    else:
                        os.link(temporary, path)
                except FileExistsError:
                    continue
                result.paths.append(path)
                break
            else:
                raise OSError("Too many existing files with that name.")
        except Exception as exc:
            result.errors.append(f"{entry.title}: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        on_progress(number, entry.title)
    result.cancelled = cancel.is_set()
    return result


def load_export_entries() -> list[ExportEntry]:
    from accessible_ai.storage.database import Database
    from chat_integration import database_path, ensure_legacy_import
    from conversation_recovery import source_identity
    from session_history import HistoryEntry, list_history, load_turns

    entries = []
    for source in list_history(limit=1000):
        identity = source_identity(source)

        def read_agent(entry=source, expected=identity):
            if expected is not None and source_identity(entry) != expected:
                raise ValueError("Conversation changed. Reopen Export Conversations to refresh it.")
            turns = load_turns(entry)
            if expected is not None and source_identity(entry) != expected:
                raise ValueError("Conversation changed while it was being read.")
            return [
                (role, text)
                for turn in turns
                for role, text in (("User", turn.prompt), ("Assistant", turn.response))
                if text
            ]

        entries.append(ExportEntry(f"{source.title} [{source.backend}]", read_agent))
    ensure_legacy_import()
    db = Database(database_path())
    reference = HistoryEntry("chat", "", "", str(db.path), 0)
    identity = source_identity(reference)
    for conversation in db.list_conversations():

        def read_chat(conversation_id=conversation.id, expected=identity):
            if source_identity(reference) != expected:
                raise ValueError("Chat history changed. Reopen Export Conversations to refresh it.")
            messages = db.list_messages(conversation_id)
            if source_identity(reference) != expected:
                raise ValueError("Chat history changed while it was being read.")
            return [
                (
                    message.role.title(),
                    message.content
                    + "".join(
                        f"\n[Attached: {attachment.filename}]" for attachment in message.attachments
                    ),
                )
                for message in messages
            ]

        entries.append(ExportEntry(f"{conversation.title} [Chat]", read_chat))
    return entries


def show_export_dialog(parent, speak) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class ExportDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent,
                title="Export Conversations",
                style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            )
            self.SetSize(self.FromDIP(wx.Size(700, 500)))
            self._tasks = TaskSlot(wx.CallAfter)
            self._cancel = Event()
            self._entries = []
            self._shown = []
            self._closed = False
            self._busy = False
            pad = self.FromDIP(8)
            outer = wx.BoxSizer(wx.VERTICAL)
            outer.Add(wx.StaticText(self, label="&Filter conversations:"), 0, wx.ALL, pad)
            self.filter = wx.TextCtrl(self)
            self.filter.SetName("Filter conversations to export")
            outer.Add(self.filter, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            outer.Add(
                wx.StaticText(self, label="&Conversations (select one or more):"), 0, wx.ALL, pad
            )
            self.list = wx.ListBox(self, style=wx.LB_EXTENDED)
            self.list.SetName("Conversations to export")
            outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            outer.Add(wx.StaticText(self, label="F&ormat:"), 0, wx.ALL, pad)
            self.format = wx.Choice(self, choices=["Markdown", "Web page (HTML)", "Plain text"])
            self.format.SetName("Export format")
            self.format.SetSelection(0)
            outer.Add(self.format, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.status = wx.StaticText(self, label="Loading conversations…")
            outer.Add(self.status, 0, wx.ALL, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.export = wx.Button(self, label="&Export selected…")
            self.cancel = wx.Button(self, label="&Cancel export")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            for button in (self.export, self.cancel, close):
                row.Add(button, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.SetEscapeId(wx.ID_CANCEL)
            self.export.Disable()
            self.cancel.Disable()
            self.filter.Bind(wx.EVT_TEXT, self._filter)
            self.list.Bind(
                wx.EVT_LISTBOX, lambda event: self.export.Enable(bool(self.list.GetSelections()))
            )
            self.export.Bind(wx.EVT_BUTTON, self._export)
            self.cancel.Bind(wx.EVT_BUTTON, self._stop)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self._tasks.start(load_export_entries, self._loaded)
            self.filter.SetFocus()
            self.CentreOnParent()

        def _loaded(self, result, error):
            if error:
                self._status(f"Could not list conversations: {error}")
                return
            self._entries = result
            self._filter(None)
            self._status(f"{len(result)} conversations. Select those you want to export.")

        def _filter(self, event):
            needle = self.filter.GetValue().casefold()
            self._shown = [entry for entry in self._entries if needle in entry.title.casefold()]
            self.list.Set([entry.title for entry in self._shown])
            self.export.Disable()

        def _status(self, text):
            self.status.SetLabel(text)
            speak(text)

        def _export(self, event):
            selected = [self._shown[index] for index in self.list.GetSelections()]
            if not selected or self._busy:
                return
            with wx.DirDialog(self, "Choose a folder for exported conversations") as picker:
                if picker.ShowModal() != wx.ID_OK:
                    return
                destination = Path(picker.GetPath())
            fmt = ("md", "html", "txt")[self.format.GetSelection()]
            self._busy = True
            self._cancel = Event()
            cancel = self._cancel
            for control in (self.filter, self.list, self.format, self.export):
                control.Disable()
            self.cancel.Enable()
            self._status(f"Exporting {len(selected)} conversations.")

            def progress(number, title):
                wx.CallAfter(self._progress, number, len(selected))

            self._tasks.start(
                lambda: export_many(selected, destination, fmt, cancel, progress), self._done
            )

        def _progress(self, number, total):
            if not self._closed:
                self.status.SetLabel(f"Exported or checked {number} of {total} conversations.")

        def _done(self, result, error):
            self._busy = False
            for control in (self.filter, self.list, self.format, self.export):
                control.Enable()
            self.cancel.Disable()
            if error:
                self._status(f"Export failed: {error}")
                return
            prefix = "Export cancelled. " if result.cancelled else ""
            self._status(f"{prefix}{len(result.paths)} exported, {len(result.errors)} failed.")
            if result.errors:
                wx.MessageBox(
                    "\n".join(result.errors),
                    "Conversations not exported",
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )

        def _stop(self, event):
            self._cancel.set()
            self.cancel.Disable()
            self._status("Cancelling export. Completed files are kept.")

        def _close(self, event):
            self._closed = True
            self._cancel.set()
            self._tasks.close()
            self.EndModal(wx.ID_CANCEL)

    dialog = ExportDialog()
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()
