"""Two ways FreeBuff 0.0.168 loses a message without ever saying so.

The first was found on a real welcome screen: the release stopped drawing the
``›`` focus marker its predecessor used, so the adapter's reader found no
focused row, never trusted the highlight it could not see, pressed Enter on
the recommended card regardless, and ran the turn on GPT-5.6 Luna when GLM
5.3 Flash had been chosen. The welcome screen in ``V168_WELCOME`` below is
that screen, captured from the release itself.

The second was found in the release's own logs: a brand-new chat whose first
line reads "Freebuff session over; holding queued messages until rejoin".
The CLI stays up and paints its composer, the message is accepted, and
nothing ever answers it. On screen there is nothing to see, so the turn sat
for its whole hour. The chat log is the only place the drop is written down.
"""

from __future__ import annotations

import agent_backends
from agent_backends import FreebuffWorker, _freebuff_picker_options, _freebuff_run_status

CATALOG = [
    "z-ai/glm-5.3-flash",
    "openai/gpt-5.6-luna",
    "deepseek/deepseek-v4-flash",
    "mimo/mimo-v2.5",
    "upstage/solar-pro4",
]

# FreeBuff 0.0.168's welcome screen, captured live on darwin-arm64. Five
# model cards, no ``›`` anywhere, and the highlight sitting on the first card.
V168_WELCOME = """
Start coding for free   5 day streak  ●●●●●○○
┌───────────────────────────────────────────────────────────────────────────┐
│   GPT-5.6 Luna             Strong all-around · Reasoning: high · Images   │
└───────────────────────────────────────────────────────────────────────────┘
UNLIMITED
┌───────────────────────────────────────────────────────────────────────────┐
│   DeepSeek V4 Flash 07/31  Smart & Fast · Reasoning: high · NEW           │
│                       May use data for AI training                        │
└───────────────────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────────────┐
│   GLM 5.3 Flash            Deep reasoning · Reasoning: max · Images · NEW │
└───────────────────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────────────┐
│   MiMo 2.5                 Balanced · Images                              │
└───────────────────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────────────┐
│   Solar Pro 4              Limited-time trial · TEST                      │
└───────────────────────────────────────────────────────────────────────────┘
FREE · today 0 of 4 · week 0.2 of 14 · month 0.2 of 40
↑  Show fewer
╭─────────────────────────────────────────────────────────────────────────╮╭─────────────────────────────────────────────────────────────────────────╮
│ Your AI Agent's Data Layer, Built on MongoDB Atlas                    Ad ││ Measure Developer Productivity and AI Impact                          Ad │
╰─────────────────────────────────────────────────────────────────────────╯╰─────────────────────────────────────────────────────────────────────────╯
""".strip()

# The same screen as the previous release painted it: a marker on the third
# card instead of nothing at all.
V167_WELCOME = V168_WELCOME.replace("│   GLM 5.3 Flash  ", "│ › GLM 5.3 Flash  ")

# 0.0.168 says this under the cards: the expanded list is already on screen,
# so there is no "See all models" entry to open before navigating.
assert "Show fewer" in V168_WELCOME

READY = "Describe your task"
WORKING = "do the work\nHere is the answer.\nEsc to stop"
# Once the turn is over the screen holds the exchange above the composer.
DONE = "do the work\nHere is the answer.\nDescribe your task"

# FreeBuff's TUI repaints: every frame clears what was on screen before it.
# Feeding bare text instead would let "Esc to stop" pile up forever and the
# turn would never be seen to end. Lines are joined with \r\n as a terminal
# really receives them: a bare \n moves down without returning the carriage,
# and every line after the first would paint one column too far right until
# the cards wrap and fragment.
_FRAME_PREFIX = "\x1b[2J\x1b[H"


def _frame(text: str) -> str:
    return _FRAME_PREFIX + text.replace("\n", "\r\n") + "\r\n"


REJOIN_LOG = (
    '{"level":"INFO","msg":"[chat-runtime] Freebuff session over; '
    'holding queued messages until rejoin"}\n'
)


def test_the_v168_welcome_screen_is_read_without_a_focus_marker():
    """No ``›`` anywhere: the first card is where the highlight starts."""
    options, focused = _freebuff_picker_options(V168_WELCOME, CATALOG)

    assert focused == 0
    assert options[0] == "openai/gpt-5.6-luna"
    assert "z-ai/glm-5.3-flash" in options


def test_a_welcome_screen_that_still_draws_the_marker_keeps_using_it():
    """A release that marks the focus is read from the marker, not from the
    top: here the marker sits on the third painted card and wins over the
    first-card reading the marker-less screen falls back to."""
    options, focused = _freebuff_picker_options(V167_WELCOME, CATALOG)

    assert options[focused] == "z-ai/glm-5.3-flash"
    assert focused == 2


def test_a_degraded_catalog_does_not_guess_where_the_focus_is():
    """Five cards are painted but only one model is known, so the matched
    row need not be where the highlight is: no position is guessed at all."""
    options, focused = _freebuff_picker_options(V168_WELCOME, ["z-ai/glm-5.3-flash"])

    assert options == ["z-ai/glm-5.3-flash"]
    assert focused == -1


def test_a_catalog_that_lost_a_later_model_still_finds_the_focus():
    """The highlight starts on the first card regardless of how many models
    the catalog knows, so a cache that missed one of the later cards — the
    state a real installation was found in — still knows where the focus is."""
    missing_deepseek = [m for m in CATALOG if m != "deepseek/deepseek-v4-flash"]
    options, focused = _freebuff_picker_options(V168_WELCOME, missing_deepseek)

    assert focused == 0
    assert options[focused] == "openai/gpt-5.6-luna"


def test_a_catalog_that_lost_the_first_model_reports_no_focus():
    """The rule names the first card's row only when that row was matched;
    a catalog without the recommended model cannot say where the highlight
    is, and guessing would send the message to some other model."""
    missing_luna = [m for m in CATALOG if m != "openai/gpt-5.6-luna"]
    options, focused = _freebuff_picker_options(V168_WELCOME, missing_luna)

    assert focused == -1
    assert "z-ai/glm-5.3-flash" in options


class _Turn:
    def __init__(self):
        self.activity: list[tuple[str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []


def _worker(turn: _Turn, model: str = "") -> FreebuffWorker:
    return FreebuffWorker(
        "do the work",
        None,
        ".",
        "default",
        model=model,
        on_session=lambda _s: None,
        on_started=lambda: None,
        on_activity=lambda kind, text: turn.activity.append((kind, text)),
        on_complete=turn.completed.append,
        on_failed=turn.failures.append,
        on_done=lambda: None,
    )


def _stub_cli(monkeypatch):
    monkeypatch.setattr(agent_backends, "find_backend_cli", lambda _backend: "freebuff")
    monkeypatch.setattr(agent_backends, "set_freebuff_model", lambda _model: None)


def _dead_terminal(worker: FreebuffWorker):
    """A terminal that ends its stream the moment it is read."""

    def spawn(_args):
        def read(_timeout):
            return ""

        worker._stream_ended.set()
        return read

    return spawn


def test_a_terminal_that_dies_over_a_dropped_session_says_so(monkeypatch, tmp_path):
    """EOF before any prompt, and a new chat logging the drop: the log wins."""
    turn = _Turn()
    worker = _worker(turn)
    _stub_cli(monkeypatch)
    chat = tmp_path / "chat-drop"
    chat.mkdir()
    (chat / "log.jsonl").write_text(REJOIN_LOG, encoding="utf-8")
    polls = {"n": 0}

    def dirs(_cwd):
        polls["n"] += 1
        return {} if polls["n"] == 1 else {"chat-drop": 1.0}

    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", dirs)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_path", lambda _cwd, _sid: chat)
    worker._write = lambda _text: True
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(_dead_terminal(worker)))

    worker._do_run()

    assert turn.failures, turn.completed
    assert "Quit and reopen FreeBuff" in turn.failures[0], turn.failures


def test_an_ordinary_death_still_reports_its_own_words(monkeypatch):
    """No new chat, no drop logged: the launch-failure reading stands."""
    turn = _Turn()
    worker = _worker(turn)
    _stub_cli(monkeypatch)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {})
    worker._write = lambda _text: True
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(_dead_terminal(worker)))

    worker._do_run()

    assert turn.failures == [
        "FreeBuff's terminal closed before it was ready for a prompt, "
        "without saying why. Check that FreeBuff runs in a terminal, then "
        "try again."
    ], turn.failures


def test_a_silent_welcome_screen_over_a_dropped_session_does_not_wait(monkeypatch, tmp_path):
    """The composer never arrives and nothing ever dies: the log still ends
    the wait instead of the two-minute silence or the hour."""
    turn = _Turn()
    worker = _worker(turn)
    _stub_cli(monkeypatch)
    chat = tmp_path / "chat-drop"
    chat.mkdir()
    (chat / "log.jsonl").write_text(REJOIN_LOG, encoding="utf-8")
    polls = {"n": 0}

    def dirs(_cwd):
        polls["n"] += 1
        return {} if polls["n"] == 1 else {"chat-drop": 1.0}

    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", dirs)
    monkeypatch.setattr(agent_backends, "_freebuff_chat_path", lambda _cwd, _sid: chat)
    monkeypatch.setattr(agent_backends, "_FREEBUFF_STARTUP_SILENCE_SECONDS", 0.2)

    def spawn(_args):
        def read(_timeout):
            return ""

        return read

    worker._write = lambda _text: True
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))

    worker._do_run()

    assert turn.failures, turn.completed
    assert "Quit and reopen FreeBuff" in turn.failures[0], turn.failures


def test_a_welcome_turn_still_reaches_its_answer(monkeypatch):
    """The whole happy path on the marker-less screen: accept the recommended
    card, send, read the answer."""
    turn = _Turn()
    worker = _worker(turn, model="openai/gpt-5.6-luna")
    _stub_cli(monkeypatch)
    monkeypatch.setattr(
        agent_backends,
        "freebuff_model_options",
        lambda: (list(CATALOG), [], CATALOG[0], "", ""),
    )
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {})
    state = {"sent": False, "enters": 0, "downs": 0, "phase": 0}

    def write(text):
        if text == "do the work":
            state["sent"] = True
        if text == "\r":
            state["enters"] += 1
        if text == "\x1b[B":
            state["downs"] += 1
        return True

    def spawn(_args):
        def read(timeout):
            if timeout == 0:
                return ""  # nothing is pending between frames
            if not state["sent"]:
                if state["enters"] == 0:
                    return _frame(V168_WELCOME)
                return _frame(READY)
            state["phase"] += 1
            return _frame(WORKING) if state["phase"] <= 5 else _frame(DONE)

        return read

    worker._write = write
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)

    worker._do_run()

    assert turn.completed == ["Here is the answer."], turn.failures
    assert turn.failures == []
    assert state["enters"] == 2, state  # picker Enter, then the send's Enter
    assert state["downs"] == 0, state  # no "See all models" step on 0.0.168


def test_the_chosen_model_is_reached_on_the_marker_less_screen(monkeypatch):
    """GLM chosen, Luna recommended and focused: one Down to GLM, one Enter."""
    turn = _Turn()
    worker = _worker(turn, model="z-ai/glm-5.3-flash")
    _stub_cli(monkeypatch)
    monkeypatch.setattr(
        agent_backends,
        "freebuff_model_options",
        lambda: (list(CATALOG), [], CATALOG[0], "", ""),
    )
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {})
    state = {"sent": False, "enters": 0, "downs": 0, "phase": 0}

    def write(text):
        if text == "do the work":
            state["sent"] = True
        if text == "\r":
            state["enters"] += 1
        if text == "\x1b[B":
            state["downs"] += 1
        return True

    def spawn(_args):
        def read(timeout):
            if timeout == 0:
                return ""  # nothing is pending between frames
            if not state["sent"]:
                if state["enters"] == 0:
                    return _frame(V168_WELCOME)
                return _frame(READY)
            state["phase"] += 1
            return _frame(WORKING) if state["phase"] <= 5 else _frame(DONE)

        return read

    worker._write = write
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)

    worker._do_run()

    assert state["downs"] == 2, state  # Luna is focused; GLM is two rows down
    assert state["enters"] == 2, state  # picker Enter, then the send's Enter
    assert turn.completed == ["Here is the answer."], turn.failures


def test_a_dropped_session_logged_mid_turn_ends_the_turn(monkeypatch, tmp_path):
    """A resumed chat that logs the drop while the turn is running is not
    waited out either."""
    turn = _Turn()
    worker = _worker(turn, model="z-ai/glm-5.3-flash")
    _stub_cli(monkeypatch)
    monkeypatch.setattr(
        agent_backends,
        "freebuff_model_options",
        lambda: (list(CATALOG), [], CATALOG[0], "", ""),
    )
    chat = tmp_path / "chat"
    chat.mkdir()
    (chat / "log.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_backends, "_freebuff_chat_dirs", lambda _cwd: {"chat": 1.0})
    monkeypatch.setattr(agent_backends, "_freebuff_chat_path", lambda _cwd, _sid: chat)
    monkeypatch.setattr(agent_backends, "_freebuff_answer_id", lambda _p: "")
    monkeypatch.setattr(agent_backends, "_freebuff_chat_snapshot", lambda _p: ("", "", "", []))
    state = {"sent": False, "phase": 0}

    def stamp(_path):
        return (state["phase"],)

    def run_status(_chat_path, _offset=0):
        text = (chat / "log.jsonl").read_text(encoding="utf-8")
        return "disconnected" if "session over" in text else ""

    monkeypatch.setattr(agent_backends, "_freebuff_chat_stamp", stamp)
    monkeypatch.setattr(agent_backends, "_freebuff_run_status", run_status)

    def write(_text):
        state["sent"] = True
        return True

    def spawn(_args):
        def read(_timeout):
            if not state["sent"]:
                return READY
            state["phase"] += 1
            if state["phase"] == 5:
                (chat / "log.jsonl").write_text(REJOIN_LOG, encoding="utf-8")
            return WORKING

        return read

    worker._write = write
    monkeypatch.setattr(FreebuffWorker, "_spawn_pty", staticmethod(spawn))
    monkeypatch.setattr(agent_backends, "_FREEBUFF_TURN_SECONDS", 60)

    worker._do_run()

    assert turn.failures, turn.completed
    assert "Quit and reopen FreeBuff" in turn.failures[0], turn.failures


def test_the_run_status_reader_still_recognises_an_ordinary_turn(tmp_path):
    """The drop is read out of the same log the complete/cancelled statuses
    come from, and none of the older readings moved."""
    chat = tmp_path / "chat"
    chat.mkdir()

    assert _freebuff_run_status(None) == ""
    assert _freebuff_run_status(chat) == ""

    (chat / "log.jsonl").write_text('{"msg":"Main prompt finished"}\n', encoding="utf-8")
    assert _freebuff_run_status(chat) == "complete"

    (chat / "log.jsonl").write_text('{"msg":"Agent run cancelled by user"}\n', encoding="utf-8")
    assert _freebuff_run_status(chat) == "cancelled"

    (chat / "log.jsonl").write_text(REJOIN_LOG, encoding="utf-8")
    assert _freebuff_run_status(chat) == "disconnected"
