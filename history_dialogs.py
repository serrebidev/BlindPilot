"""Dialogs for backing up, restoring, importing and keeping history."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from typing import Callable

from accessible_ai.storage.database import Database
from accessible_ai.storage.paths import app_data_dir, database_path

import history_backup as backup
from history_backup import HistoryBackupError, KeptTranscripts


def _kept_dir() -> Path:
    return app_data_dir() / "kept_transcripts"


def _config_path() -> Path:
    from agent_backends import blindpilot_config_dir

    return blindpilot_config_dir() / "config.json"


def _keeping_enabled() -> bool:
    try:
        from accessible_ai.storage import preferences

        cfg = preferences.load_config([_config_path()])
        return bool(cfg.get("keep_transcripts", True))
    except Exception:
        return True


def maybe_keep_transcript(path: str, speak: Callable[[str], None] | None = None) -> None:
    """Keep one opened transcript in the background, when keeping is on.

    Never raises into the GUI: a failed first copy is announced rather than
    claimed as protection, and any failure to even start copying is skipped.
    """
    if not _keeping_enabled() or not path:
        return
    source = Path(path)
    if not source.is_file():
        return
    kept = KeptTranscripts(_kept_dir())

    def run() -> None:
        try:
            ok, error = kept.keep(source), ""
        except Exception as exc:
            ok, error = False, str(exc)
        if speak is not None and (not ok or error):
            import wx

            wx.CallAfter(speak, "That conversation could not be kept as a protected copy.")

    import threading

    threading.Thread(target=run, daemon=True).start()


def maybe_keep_current_session(
    backend: str,
    session_id: str,
    cwd: str,
    speak: Callable[[str], None] | None = None,
) -> None:
    """Keep the current session's transcript after a local turn.

    Resolves its transcript path on the background thread (listing is the
    same scan the Recent Conversations picker does), then keeps it when
    keeping is on. Never raises into the GUI.
    """
    if not _keeping_enabled() or not session_id:
        return

    def run() -> None:
        try:
            from session_history import list_history

            path = ""
            for entry in list_history(backend=backend or None, cwd=cwd or None):
                if entry.session_id == session_id:
                    path = entry.path
                    break
            if not path:
                return
            ok = KeptTranscripts(_kept_dir()).keep(Path(path))
        except Exception:
            ok = False
        if speak is not None and not ok:
            import wx

            wx.CallAfter(speak, "That conversation could not be kept as a protected copy.")

    import threading

    threading.Thread(target=run, daemon=True).start()


def show_backup_dialog(parent, speak: Callable[[str], None]) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class BackupDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Back Up BlindPilot", style=wx.DEFAULT_DIALOG_STYLE)
            self._tasks = TaskSlot(wx.CallAfter)
            self._cancel = Event()
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(8)
            self.include = wx.CheckBox(
                self, label="Include kept transcript copies (they can be sensitive)"
            )
            self.include.SetName("Include kept transcripts in this backup")
            outer.Add(self.include, 0, wx.ALL, pad)
            outer.Add(
                wx.StaticText(self, label="Backups never include API keys or tokens."),
                0,
                wx.LEFT | wx.RIGHT | wx.BOTTOM,
                pad,
            )
            self.status = wx.StaticText(self, label="")
            outer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.save = wx.Button(self, label="&Choose where to save…")
            self.cancel = wx.Button(self, label="&Cancel backup")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            for button in (self.save, self.cancel, close):
                row.Add(button, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.cancel.Disable()
            self.save.Bind(wx.EVT_BUTTON, self._save)
            self.cancel.Bind(wx.EVT_BUTTON, self._stop)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.save.SetFocus()
            self.CentreOnParent()

        def _save(self, _event) -> None:
            default = f"blindpilot-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
            dialog = wx.FileDialog(
                self,
                "Save backup to",
                defaultDir=str(Path.home()),
                defaultFile=default,
                style=wx.FD_SAVE,
                wildcard="Backup files (*.zip)|*.zip",
            )
            try:
                if dialog.ShowModal() != wx.ID_OK or not dialog.GetPath():
                    return
                destination = Path(dialog.GetPath())
            finally:
                dialog.Destroy()
            self._cancel.clear()
            self.save.Disable()
            self.cancel.Enable()
            self.status.SetLabel(f"Backing up to {destination.name}…")
            include = bool(self.include.GetValue())

            def work() -> str:
                backup.create_backup(
                    destination,
                    database_path(),
                    _config_path(),
                    _kept_dir(),
                    include,
                    cancel=self._cancel,
                )
                return destination.name

            self._tasks.start(
                work,
                lambda name, error: self._done(destination, name, error),
            )

        def _done(self, destination, name, error) -> None:
            self.save.Enable()
            self.cancel.Disable()
            if error:
                self.status.SetLabel(f"Backup failed: {error}")
                speak(f"Backup failed: {error}")
                return
            self.status.SetLabel(f"Backup saved as {name}.")
            speak(f"Backup saved as {name}.")

        def _stop(self, _event) -> None:
            self._cancel.set()

        def _close(self, _event) -> None:
            self._cancel.set()
            self._tasks.close()
            self.Destroy()

    BackupDialog().ShowModal()


def show_restore_dialog(parent, speak: Callable[[str], None]) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class RestoreDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Restore from Backup", style=wx.DEFAULT_DIALOG_STYLE)
            self._tasks = TaskSlot(wx.CallAfter)
            self._archive: Path | None = None
            self._preview: backup.BackupPreview | None = None
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(8)
            outer.Add(wx.StaticText(self, label="&Backup archive:"), 0, wx.ALL, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.path = wx.TextCtrl(self)
            self.path.SetName("Backup archive path")
            row.Add(self.path, 1, wx.RIGHT, pad)
            self.choose = wx.Button(self, label="&Choose…")
            row.Add(self.choose, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.detail = wx.StaticText(self, label="Choose a backup to preview it first.")
            outer.Add(self.detail, 0, wx.ALL, pad)
            buttons = wx.BoxSizer(wx.HORIZONTAL)
            self.restore = wx.Button(self, label="&Restore this backup")
            self.restore.Disable()
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            buttons.Add(self.restore, 0, wx.RIGHT, pad)
            buttons.Add(close, 0, wx.RIGHT, pad)
            outer.Add(buttons, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.choose.Bind(wx.EVT_BUTTON, self._choose)
            self.restore.Bind(wx.EVT_BUTTON, self._restore)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.CentreOnParent()
            self.choose.SetFocus()

        def _choose(self, _event) -> None:
            dialog = wx.FileDialog(
                self,
                "Choose a backup",
                defaultDir=str(Path.home()),
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                wildcard="Backup files (*.zip)|*.zip",
            )
            try:
                if dialog.ShowModal() != wx.ID_OK or not dialog.GetPath():
                    return
                self._archive = Path(dialog.GetPath())
            finally:
                dialog.Destroy()
            self.path.SetValue(str(self._archive))
            self.restore.Disable()
            self.detail.SetLabel("Reading the backup…")
            try:
                self._tasks.start(lambda: backup.preview_backup(self._archive), self._previewed)
            except HistoryBackupError as exc:
                self.detail.SetLabel(f"Could not read that backup: {exc}")

        def _previewed(self, preview, error) -> None:
            if error:
                self.detail.SetLabel(f"Could not read that backup: {error}")
                speak(f"Could not read that backup: {error}")
                return
            self._preview = preview
            self.detail.SetLabel(
                f"Created {preview.created_at} by BlindPilot {preview.app_version}: "
                f"{preview.accounts} accounts, {preview.profiles} profiles, "
                f"{preview.conversations} conversations, {preview.messages} messages."
                + (" Includes kept transcripts." if preview.includes_kept_transcripts else "")
                + " Your current history is kept aside before restoring."
            )
            self.restore.Enable()

        def _restore(self, _event) -> None:
            if self._archive is None:
                return
            self.restore.Disable()
            archive = self._archive

            def work() -> str:
                backup.restore_backup(archive, database_path(), _config_path(), _kept_dir())
                return "done"

            self._tasks.start(
                work,
                lambda _result, error: self._restored(error),
            )

        def _restored(self, error) -> None:
            if error:
                self.detail.SetLabel(f"Restore failed: {error}")
                speak(f"Restore failed: {error}")
                return
            self.detail.SetLabel("Restored. Your previous history was kept aside.")
            speak("Restore finished. Your previous history was kept aside.")

        def _close(self, _event) -> None:
            self._tasks.close()
            self.Destroy()

    RestoreDialog().ShowModal()


def show_import_dialog(parent, speak: Callable[[str], None]) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class ImportDialog(wx.Dialog):
        def __init__(self):
            super().__init__(parent, title="Import Conversation", style=wx.DEFAULT_DIALOG_STYLE)
            self._tasks = TaskSlot(wx.CallAfter)
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(8)
            outer.Add(
                wx.StaticText(
                    self,
                    label="Import a Basilisk (.bskc) or BlindPilot interchange (.json) conversation.",
                ),
                0,
                wx.ALL,
                pad,
            )
            self.status = wx.StaticText(self, label="")
            outer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.choose = wx.Button(self, label="&Choose a file…")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            row.Add(self.choose, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.choose.Bind(wx.EVT_BUTTON, self._choose)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.choose.SetFocus()
            self.CentreOnParent()

        def _choose(self, _event) -> None:
            dialog = wx.FileDialog(
                self,
                "Import conversation",
                defaultDir=str(Path.home()),
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                wildcard="Basilisk conversations (*.bskc)|*.bskc|"
                "BlindPilot interchange (*.json)|*.json|All files|*.*",
            )
            try:
                if dialog.ShowModal() != wx.ID_OK or not dialog.GetPath():
                    return
                path = Path(dialog.GetPath())
            finally:
                dialog.Destroy()
            self.choose.Disable()
            self.status.SetLabel(f"Importing {path.name}…")

            def work() -> backup.ImportReport:
                database = Database(database_path())
                if path.suffix.lower() == ".json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return backup.import_interchange(data, database)
                return backup.import_bskc(path, database)

            self._tasks.start(work, self._imported)

        def _imported(self, report, error) -> None:
            self.choose.Enable()
            if error:
                self.status.SetLabel(f"Import failed: {error}")
                speak(f"Import failed: {error}")
                return
            note = ""
            if report.unsupported:
                note = " Not carried over: " + "; ".join(report.unsupported) + "."
            self.status.SetLabel(
                f"Imported {report.title}: {report.messages} messages, "
                f"{report.attachments} attachments.{note}"
            )
            speak(f"Imported {report.title}, {report.messages} messages.{note}")

        def _close(self, _event) -> None:
            self._tasks.close()
            self.Destroy()

    ImportDialog().ShowModal()


def show_kept_dialog(parent, speak: Callable[[str], None]) -> None:
    import wx
    from dialog_tasks import TaskSlot

    class KeptDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent, title="Kept Transcripts", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            )
            self.SetSize(self.FromDIP(wx.Size(700, 420)))
            self._tasks = TaskSlot(wx.CallAfter)
            self._entries: list[backup.KeptEntry] = []
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(8)
            outer.Add(wx.StaticText(self, label="&Kept copies:"), 0, wx.ALL, pad)
            self.list = wx.ListBox(self, style=wx.LB_EXTENDED)
            self.list.SetName("Kept transcript copies")
            outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            self.storage = wx.StaticText(self, label="Loading…")
            outer.Add(self.storage, 0, wx.ALL, pad)
            self.keep_check = wx.CheckBox(self, label="Keep opened conversations automatically")
            self.keep_check.SetName("Keep opened conversations automatically")
            self.keep_check.SetValue(_keeping_enabled())
            outer.Add(self.keep_check, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.delete = wx.Button(self, label="&Delete selected copies…")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            row.Add(self.delete, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            self.delete.Bind(wx.EVT_BUTTON, self._delete)
            self.keep_check.Bind(wx.EVT_CHECKBOX, self._toggle)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self._tasks.start(KeptTranscripts(_kept_dir()).entries, self._loaded)
            self.list.SetFocus()
            self.CentreOnParent()

        def _loaded(self, entries, error) -> None:
            if error:
                self.storage.SetLabel(f"Could not list kept copies: {error}")
                return
            self._entries = list(entries or [])
            self.list.Clear()
            for entry in self._entries:
                self.list.Append(
                    f"{Path(entry.source_path).name} ({entry.size} bytes, kept {entry.kept_at})"
                )
            count, total = KeptTranscripts(_kept_dir()).storage_report()
            self.storage.SetLabel(
                f"{count} copies use {total} bytes. Copies never expire by themselves; "
                "deleting a copy never deletes the original."
            )

        def _toggle(self, _event) -> None:
            from accessible_ai.storage import preferences

            preferences.update_config(
                _config_path(), {"keep_transcripts": bool(self.keep_check.GetValue())}
            )
            speak(
                "Kept transcripts keeping is on."
                if self.keep_check.GetValue()
                else "Kept transcripts keeping is off."
            )

        def _delete(self, _event) -> None:
            selections = self.list.GetSelections()
            if not selections:
                speak("Select the copies to delete first.")
                return
            names = [self._entries[index].copy_name for index in selections]
            confirm = wx.MessageBox(
                f"Delete {len(names)} kept copies? The original transcripts are not deleted.",
                "Delete kept copies",
                wx.YES_NO | wx.ICON_QUESTION,
                self,
            )
            if confirm != wx.YES:
                return
            removed = KeptTranscripts(_kept_dir()).delete(names)
            speak(f"Deleted {removed} kept copies.")
            self._tasks.start(KeptTranscripts(_kept_dir()).entries, self._loaded)

        def _close(self, _event) -> None:
            self._tasks.close()
            self.Destroy()

    KeptDialog().ShowModal()
