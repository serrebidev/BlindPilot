"""Non-speech audio cues, and how the working cue repeats.

The four cues live here rather than in the window: a one-shot when a prompt is
sent, a looping or periodic cue while a request is in flight, a one-shot when
the response arrives, and the system sound when something goes wrong. Split out
of ``blindpilot_app.py`` (issue #35) so the window holds the cues rather than
defines them. The files they resolve to and the settings keys they read are
unchanged by the move.

The progress mode is read from the settings object handed to the class, so this
module needs nothing from the window: a player built without one behaves as the
application always has, a periodic cue every ten seconds.

Copyright (c) 2026 doubletaponair and BlindPilot contributors.
Based on the original Claude Code Reader application by doubletaponair:
https://github.com/doubletaponair/claude-code-reader
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from agent_backends import BACKEND_PITCH_ORDER


CUE_LOOP = "loop"
CUE_PERIODIC = "periodic"
CUE_OFF = "off"
PROGRESS_CUES = (CUE_LOOP, CUE_PERIODIC, CUE_OFF)

# How often the periodic working cue plays, and the range the setting allows. A
# cue every couple of seconds is barely different from the loop; one every few
# minutes stops answering "is it still working".
CUE_SECONDS_DEFAULT = 10
CUE_SECONDS_MIN = 2
CUE_SECONDS_MAX = 120


def _valid_progress_cue(value: object) -> str:
    """One of the three cue behaviours, whatever the config file holds.

    A config written by a newer version, or edited by hand, must not stop the
    app from starting, so anything unrecognised falls back to the default.
    """
    text = str(value or "").strip().lower()
    return text if text in PROGRESS_CUES else CUE_PERIODIC


def _faster_wav(path: str, factor: float) -> str:
    """A copy of a WAV that plays ``factor`` times faster, so higher.

    Only the header's sample rate changes; the player does the resampling.
    Written once to the temp folder and reused.
    """
    source_key = hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:12]
    out = os.path.join(
        tempfile.gettempdir(), f"blindpilot-{Path(path).stem}-{source_key}-{factor:.3f}.wav"
    )
    if os.path.isfile(out):
        return out
    with wave.open(path, "rb") as src:
        params = src.getparams()
        frames = src.readframes(params.nframes)
    with wave.open(out, "wb") as dst:
        dst.setparams(params._replace(framerate=round(params.framerate * factor)))
        dst.writeframes(frames)
    return out


class Earcons:
    """Non-speech audio cues.

    Four cues: a one-shot when a prompt is sent, a looping or periodic cue
    while a request is in flight, a one-shot when the response arrives, and
    the system sound when something goes wrong. Uses only players the platform
    has, so there is no third-party audio dependency: ``winsound`` on Windows,
    ``afplay`` on macOS, and paplay, aplay or ffplay on Linux, looped by
    re-spawning in a daemon thread. Missing files are silently ignored.
    """

    def __init__(
        self,
        folder: str,
        enabled: bool = True,
        cues: Optional[dict] = None,
        settings: Optional[object] = None,
    ):
        self._folder = folder
        # Whatever carries the progress-cue settings: the window passes its
        # live settings object. Absent means the defaults below, so a player
        # built on its own -- in a test, or before a window exists -- still
        # plays the cue this application always has.
        self._settings = settings
        self._system = platform.system()
        self.enabled = bool(enabled)
        # Which cues the master switch turns on. Absent means on, so an
        # Earcons built without them behaves exactly as it did before.
        self.cues = dict(cues or {})
        self.send = self._resolve("send")
        self.received = self._resolve("received", "Recieved")
        self.in_progress = self._resolve("in-progress", "in_progress")
        self._loop_stop = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_proc: Optional[subprocess.Popen] = None
        # Whether a progress cue of ours has been started and not yet stopped.
        # It matters because the Windows sound API is stopped by purging every
        # sound this process started, which would also cut a one-shot that had
        # just begun. So the purge only runs while there is a cue to stop.
        self._cue_sounding = False
        # One-shot players still running. They stay referenced until reaped so
        # the garbage collector never runs `Popen.__del__` on a live process -
        # that raises an unraisable exception, which CI's `-W error` turns
        # into a failed build.
        self._reaping: list[subprocess.Popen] = []
        self.per_backend_send = False
        self._send_variants: dict[str, Optional[str]] = {}

    def _resolve(self, *basenames: str) -> Optional[str]:
        for name in basenames:
            for ext in (".wav", ".ogg", ".aiff", ".aif", ".mp3"):
                path = os.path.join(self._folder, name + ext)
                if os.path.isfile(path):
                    return path
        return None

    def _unix_player(self) -> Optional[list]:
        if self._system == "Darwin":
            return ["afplay"]
        for player in ("paplay", "aplay", "ffplay"):
            found = shutil.which(player)
            if found:
                return [found] + (["-nodisp", "-autoexit"] if player == "ffplay" else [])
        return None

    def _play_once(self, path: Optional[str]) -> None:
        if not self.enabled or not path:
            return
        try:
            if self._system == "Windows":
                import winsound

                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                player = self._unix_player()
                if player:
                    self._spawn(player + [path])
        except Exception:
            pass

    def _wanted(self, cue: str) -> bool:
        """Whether this cue sounds: the master switch, and then its own."""
        return self.enabled and self.cues.get(cue, True)

    def _spawn(self, argv: list) -> None:
        """Start a one-shot player and reap it in a daemon thread.

        The process stays reachable through `self._reaping` until it has been
        waited on. Dropping the Popen straight away lets `__del__` run while
        `afplay` is still playing, which becomes an unraisable exception under
        pytest's `-W error`.
        """
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._reaping.append(proc)

        def _reap() -> None:
            try:
                proc.wait()
            except Exception:
                pass
            finally:
                try:
                    self._reaping.remove(proc)
                except ValueError:
                    pass

        threading.Thread(target=_reap, daemon=True).start()

    def play_send(self, backend: str = "") -> None:
        """The send cue, pitched per backend while tabs use more than one.

        The frame turns ``per_backend_send`` on when the open tabs do not all
        use the same backend, so a message sent to the wrong one sounds wrong
        before anything is spoken. With one backend the cue stays the one the
        person already knows.
        """
        if self._wanted("send"):
            path = self.send
            if self.per_backend_send and backend:
                path = self._send_for(backend)
            self._play_once(path)

    def _send_for(self, backend: str) -> Optional[str]:
        if backend not in self._send_variants:
            # Keyed on the fixed order backends were added in, not the
            # alphabetical menu order, so each backend keeps its pitch.
            index = BACKEND_PITCH_ORDER.index(backend) if backend in BACKEND_PITCH_ORDER else 0
            path = self.send
            if index and path and path.lower().endswith(".wav"):
                try:
                    # Two semitones higher per backend, from Claude's own pitch.
                    path = _faster_wav(path, 2 ** (index * 2 / 12))
                except Exception:
                    path = self.send
            self._send_variants[backend] = path
        return self._send_variants[backend]

    def _play_system_error(self) -> None:
        """The platform's own error sound, rather than an asset of our own.

        `EarCons/` ships three files and authoring a fourth is not something to
        fake. This is also the sound the person already associates with
        something having gone wrong on this machine, which is worth more than
        one that matches the other three.
        """
        if self._system == "Windows":
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND)
            return
        if self._system == "Darwin":
            self._spawn(["afplay", "/System/Library/Sounds/Basso.aiff"])
            return
        # Linux has no single answer here, and a wrong guess is worse than
        # nothing: the error is spoken either way.

    def play_error(self) -> None:
        """Say a turn failed before saying why.

        An error was spoken at the back of a queue a fan-out can make minutes
        deep, and there was no failure cue at all. Interrupting was the other
        option and was rejected - it purges the reader's whole queue, including
        other applications' speech. A sound costs nobody else anything.
        """
        if not self._wanted("error"):
            return
        try:
            self._play_system_error()
        except Exception:
            # A missing cue is never worth losing the error message over.
            pass

    def play_received(self) -> None:
        # Stopping the loop stays unconditional: it has to end when the turn
        # does, whatever any of these are set to.
        self.stop_progress()
        if self._wanted("received"):
            self._play_once(self.received)

    def start_progress(self) -> None:
        """Signal that a turn is running, in whichever way Options asks for.

        Looping a cue under a second long means dozens of repeats per turn, on
        top of the answer being spoken, so the periodic mode plays it once and
        then only every few seconds. ``off`` plays nothing: send and received
        still mark the boundaries of the turn. A player with no settings object
        has no choice to read, so it plays the periodic default.
        """
        self.stop_progress()
        if not self._wanted("working") or not self.in_progress:
            return
        mode = getattr(self._settings, "progress_cue", CUE_PERIODIC)
        if mode == CUE_OFF:
            return
        if mode == CUE_PERIODIC:
            every_seconds = getattr(self._settings, "progress_cue_seconds", CUE_SECONDS_DEFAULT)
            self._cue_sounding = True
            self._start_periodic(max(CUE_SECONDS_MIN, every_seconds))
            return
        if self._system == "Windows":
            try:
                import winsound

                winsound.PlaySound(
                    self.in_progress,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
                self._cue_sounding = True
            except Exception:
                pass
            return
        # A fresh event per run, never the previous one reset: the thread this
        # replaces may still be inside `wait()`, and clearing the event it is
        # watching would set it looping again alongside the new one. Turn by
        # turn that is how one progress cue becomes several playing at once.
        self._cue_sounding = True
        stop = threading.Event()
        self._loop_stop = stop
        self._loop_thread = threading.Thread(target=self._loop_unix, args=(stop,), daemon=True)
        self._loop_thread.start()

    def _start_periodic(self, every_seconds: int) -> None:
        """Play the working cue now, then again every ``every_seconds``.

        One timer thread, woken by the stop event rather than by sleeping in
        fixed steps, so ``stop_progress`` ends it immediately instead of after
        the current interval. The event is created fresh here for the same
        reason as in the looping path above.
        """
        stop = threading.Event()
        self._loop_stop = stop
        self._loop_thread = threading.Thread(
            target=self._periodic, args=(every_seconds, stop), daemon=True
        )
        self._loop_thread.start()

    def _periodic(self, every_seconds: int, stop: threading.Event) -> None:
        while not stop.is_set():
            self._play_once(self.in_progress)
            # Returns True the moment the turn ends, so the wait is not a
            # fixed sleep the stop has to outlast.
            if stop.wait(every_seconds):
                return

    def _loop_unix(self, stop: threading.Event) -> None:
        player = self._unix_player()
        if not player:
            return
        while not stop.is_set():
            started = time.monotonic()
            try:
                proc = subprocess.Popen(
                    player + [self.in_progress],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if stop.is_set():
                    proc.kill()
                    return
                self._loop_proc = proc
                proc.wait()
            except Exception:
                return
            # A player that cannot play the file returns at once, and looping
            # on that spawns processes as fast as the machine allows. One cue
            # that never sounds is a bug; a fork bomb behind it is a hang.
            if not stop.is_set() and time.monotonic() - started < 0.05:
                return

    def stop_progress(self) -> None:
        # The periodic thread exists on every platform, so its stop is set
        # first: on Windows the looping cue is stopped by the sound API, but a
        # periodic cue is a thread of ours and would otherwise keep playing
        # after the answer arrived.
        self._loop_stop.set()
        sounding = self._cue_sounding
        self._cue_sounding = False
        if self._system == "Windows":
            # Purging is per process, not per sound: it silences whatever this
            # application is playing, including a one-shot that started a
            # moment ago. So it runs only when a cue of ours is actually
            # playing. Without this guard, ending a turn purged the 'received'
            # cue it had just started -- and the faster the turn ended, the
            # less of that cue was left to hear.
            if not sounding:
                return
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
            return
        proc = self._loop_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self._loop_proc = None
        self._loop_thread = None

    def set_enabled(self, enabled: bool) -> None:
        """Enable or mute cues, stopping the progress loop when muted."""
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop_progress()

    def set_cues(self, cues: dict) -> None:
        """Switch individual cues on or off.

        The progress loop stops the moment its own cue goes, rather than at
        the end of the turn: somebody reaching for that switch means now.
        """
        self.cues.update({key: bool(value) for key, value in cues.items()})
        if not self._wanted("working"):
            self.stop_progress()
