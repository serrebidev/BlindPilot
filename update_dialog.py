"""Accessible, single-window BlindPilot software update flow."""

from __future__ import annotations

import threading
import sys
from pathlib import Path
from typing import Callable, Optional

import wx

from app_updater import (
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    download_update,
    fetch_latest_release,
    schedule_install,
)


ANNOUNCE_EVERY_PERCENT = 10


def _call_after(callback, *args) -> None:
    """wx.CallAfter, unless the application has already gone.

    The workers below run under a 20 second HTTP timeout. Close the dialog and
    quit inside it and the thread finishes after wx.App is destroyed, where
    wx.CallAfter asserts and the thread hook logs a critical line for a
    message nobody is left to read.
    """
    if wx.GetApp() is None:
        return
    wx.CallAfter(callback, *args)


class UpdateDialog(wx.Dialog):
    """Check, download, verify, and hand off an update without changing windows."""

    def __init__(
        self,
        parent: wx.Window,
        current_version: str,
        speak: Callable[[str], None],
        check: Callable[[str], Optional[ReleaseInfo]] = fetch_latest_release,
        start_check: bool = True,
    ):
        super().__init__(
            parent,
            title="BlindPilot Software Update",
            size=wx.Size(640, 470),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.current_version = current_version
        self.speak = speak
        self.check = check
        self.release: Optional[ReleaseInfo] = None
        self.archive: Optional[Path] = None
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.restart_pending = False
        self._closing = False
        self._last_announced_percent = -1

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        self.status = wx.StaticText(panel, label="")
        self.status.SetName("Update status")
        outer.Add(self.status, 0, wx.EXPAND | wx.ALL, 12)

        self.notes_label = wx.StaticText(panel, label="What is new:")
        self.notes_label.SetName("Release notes label")
        outer.Add(self.notes_label, 0, wx.LEFT | wx.RIGHT, 12)
        self.notes = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        self.notes.SetName("Release notes")
        outer.Add(self.notes, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.progress_label = wx.StaticText(panel, label="Download progress:")
        self.progress_label.SetName("Download progress label")
        outer.Add(self.progress_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.gauge = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
        self.gauge.SetName("Download progress")
        outer.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.primary_button = wx.Button(panel, wx.ID_OK, "&Install update")
        self.primary_button.SetName("Update action")
        self.secondary_button = wx.Button(panel, wx.ID_CANCEL, "&Close")
        self.secondary_button.SetName("Close update dialog")
        buttons.Add(self.primary_button, 0, wx.RIGHT, 8)
        buttons.Add(self.secondary_button, 0)
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        panel.SetSizer(outer)

        self.primary_button.Bind(wx.EVT_BUTTON, self.on_primary)
        self.secondary_button.Bind(wx.EVT_BUTTON, self.on_secondary)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self._show_checking()
        if start_check:
            self._start(self._check_worker)

    def _set_state(
        self,
        message: str,
        *,
        primary: Optional[str],
        secondary: str,
        notes: str = "",
        show_progress: bool = False,
        announce: bool = True,
    ) -> None:
        self.status.SetLabel(message)
        self.status.Wrap(590)
        has_notes = bool(notes)
        self.notes.SetValue(notes)
        self.notes.SetInsertionPoint(0)
        self.notes_label.Show(has_notes)
        self.notes.Show(has_notes)
        self.progress_label.Show(show_progress)
        self.gauge.Show(show_progress)
        self.primary_button.Show(primary is not None)
        if primary is not None:
            self.primary_button.SetLabel(primary)
        self.secondary_button.SetLabel(secondary)
        self.Layout()
        target = self.primary_button if primary is not None else self.secondary_button
        target.SetDefault()
        target.SetFocus()
        if announce:
            self.speak(message)

    def _show_checking(self) -> None:
        self._set_state(
            f"Checking for updates. BlindPilot {self.current_version} is installed.",
            primary=None,
            secondary="&Cancel",
        )

    def _show_up_to_date(self) -> None:
        self._set_state(
            f"BlindPilot {self.current_version} is the latest version.",
            primary=None,
            secondary="&Close",
        )

    def _show_available(self, release: ReleaseInfo) -> None:
        size = _format_size(release.asset_size)
        size_text = f" The download is {size}." if size else ""
        source_run = not getattr(sys, "frozen", False)
        if source_run:
            message = (
                f"BlindPilot {release.version} is available. You have {self.current_version}. "
                "This copy runs from source, so it cannot replace itself."
            )
            primary = "&Open release page"
            secondary = "&Close"
        else:
            message = (
                f"BlindPilot {release.version} is available. "
                f"You have {self.current_version}.{size_text}"
            )
            primary = "&Install update"
            secondary = "&Not now"
        self._set_state(
            message,
            primary=primary,
            secondary=secondary,
            notes=release.notes or "This release published no notes.",
        )

    def _show_downloading(self, release: ReleaseInfo) -> None:
        self.gauge.SetValue(0)
        self._last_announced_percent = -1
        self._set_state(
            f"Downloading and verifying BlindPilot {release.version}.",
            primary=None,
            secondary="&Cancel",
            show_progress=True,
        )

    def _show_ready(self, release: ReleaseInfo) -> None:
        self._set_state(
            f"BlindPilot {release.version} is downloaded and verified. "
            "Choose Restart now to install it; BlindPilot will reopen when installation finishes.",
            primary="&Restart now",
            secondary="&Later",
        )

    def _show_error(self, message: str) -> None:
        self._set_state(message, primary=None, secondary="&Close")

    def _start(self, target) -> None:
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _check_worker(self) -> None:
        try:
            release = self.check(self.current_version)
        except UpdateError as exc:
            _call_after(self._check_failed, str(exc))
            return
        except Exception as exc:
            _call_after(self._check_failed, f"Checking for updates failed: {exc}")
            return
        _call_after(self._check_finished, release)

    def _check_finished(self, release: Optional[ReleaseInfo]) -> None:
        if self._closing:
            return
        if release is None or not release.is_newer_than(self.current_version):
            self._show_up_to_date()
            return
        self.release = release
        self._show_available(release)

    def _check_failed(self, message: str) -> None:
        if not self._closing:
            self._show_error(message)

    def _download_worker(self, release: ReleaseInfo) -> None:
        try:
            archive = download_update(
                release,
                self.current_version,
                progress=lambda done, total: _call_after(self._on_progress, done, total),
                cancel=self.cancel_event,
            )
        except UpdateCancelled:
            _call_after(self._download_cancelled)
            return
        except UpdateError as exc:
            _call_after(self._download_failed, str(exc))
            return
        except Exception as exc:
            _call_after(self._download_failed, f"The update could not be prepared: {exc}")
            return
        _call_after(self._download_finished, archive)

    def _on_progress(self, done: int, total: int) -> None:
        if self._closing:
            return
        if total <= 0:
            self.gauge.Pulse()
            return
        percent = min(100, int(done * 100 / total))
        self.gauge.SetValue(percent)
        step = percent - (percent % ANNOUNCE_EVERY_PERCENT)
        if step > self._last_announced_percent:
            self._last_announced_percent = step
            self.speak(f"{step} percent")

    def _download_cancelled(self) -> None:
        if not self._closing:
            self._show_error("The update was cancelled. Nothing was changed.")

    def _download_failed(self, message: str) -> None:
        if not self._closing:
            self._show_error(message)

    def _download_finished(self, archive: Path) -> None:
        if self._closing:
            archive.unlink(missing_ok=True)
            return
        self.archive = archive
        self.gauge.SetValue(100)
        if self.release is not None:
            self._show_ready(self.release)

    def on_primary(self, event: wx.CommandEvent) -> None:
        label = self.primary_button.GetLabel()
        if "Open release page" in label and self.release is not None:
            wx.LaunchDefaultBrowser(self.release.page_url)
            return
        if "Install update" in label and self.release is not None:
            self.cancel_event.clear()
            self._show_downloading(self.release)
            self._start(lambda: self._download_worker(self.release))
            return
        if "Restart now" in label:
            self._install_and_restart()

    def _install_and_restart(self) -> None:
        if self.archive is None:
            self._show_error("The update is not ready. Check for updates again.")
            return
        try:
            schedule_install(self.archive)
        except UpdateError as exc:
            self._show_error(str(exc))
            return
        self.restart_pending = True
        self.archive = None
        self.speak("Installing the update. BlindPilot will reopen shortly.")
        self.EndModal(wx.ID_OK)

    def on_secondary(self, event: wx.CommandEvent) -> None:
        self.Close()

    def on_close(self, event: wx.CloseEvent) -> None:
        self._closing = True
        self.cancel_event.set()
        if self.archive is not None:
            self.archive.unlink(missing_ok=True)
            self.archive = None
        if self.IsModal():
            self.EndModal(wx.ID_OK if self.restart_pending else wx.ID_CANCEL)
        else:
            event.Skip()


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    megabytes = size_bytes / (1024 * 1024)
    if megabytes >= 1:
        return f"{megabytes:.1f} MB"
    return f"{max(1, size_bytes // 1024)} KB"
