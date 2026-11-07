"""Typed inputs and lifecycle promised by the backend worker factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from questions import AskQuestions


class Worker(Protocol):
    """One unstarted turn. ``steer`` returns False when it cannot accept text."""

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def cancel(self) -> None: ...

    def steer(self, text: str) -> bool: ...


@dataclass(frozen=True)
class WorkerCapabilities:
    """Behavior the window needs to know before starting a turn."""

    subagents_outlive_turn: bool = False


PermissionCallback = Callable[[str, dict, list], dict]
SubagentCallback = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class WorkerCallbacks:
    """Turn events retain their provider ordering and existing GUI delivery path."""

    on_session: Callable[[str], None]
    on_started: Callable[[], None]
    on_activity: Callable[[str, str], None]
    on_complete: Callable[[str], None]
    on_failed: Callable[[str], None]
    on_done: Callable[[], None]
    on_question: AskQuestions | None = None
    on_permission: PermissionCallback | None = None
    on_subagent: SubagentCallback | None = None
    on_tasks: Callable[..., None] | None = None
    on_remote: Callable[[dict], None] | None = None
    on_unsolicited: Callable[[], None] | None = None


@dataclass(frozen=True)
class ClaudeOptions:
    held_for: object = None
    remote_control: dict | None = None


_UNSET = object()


@dataclass(frozen=True)
class CodexOptions:
    compact: bool = False
    attachments: tuple[str, ...] = ()
    # Omitted means the worker chooses the saved Account. None explicitly
    # chooses Codex login, so those two values cannot be merged.
    plan_account: object = _UNSET


@dataclass(frozen=True)
class FreebuffOptions:
    compact: bool = False
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpencodeOptions:
    compact: bool = False
    attachments: Sequence[str] = ()


@dataclass(frozen=True)
class HermesOptions:
    compact: bool = False
    remote_url: str = ""
    remote_token: str = ""
    remote_credential: str = "token"
    remote_username: str = ""
    held: object = None
    attachments: Sequence[str] | None = None
    resume_only: bool = False
    session_title: str = ""


@dataclass(frozen=True)
class MuseOptions:
    compact: bool = False
    resume_only: bool = False
    attachments: Sequence[str] | None = None


@dataclass(frozen=True)
class MuseAiOptions:
    follow_after: int | None = None


@dataclass(frozen=True)
class CommandcodeOptions:
    compact: bool = False
    additional_dirs: tuple[str, ...] = ()
    allow_session_retry: bool = True
    auto_free_fallback: bool = False
    auto_route: bool = False
    auto_context_tokens: int | None = None


@dataclass(frozen=True)
class GeminiOptions:
    additional_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AntigravityOptions:
    additional_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CopilotOptions:
    additional_dirs: tuple[str, ...] = ()


WorkerOptions = (
    ClaudeOptions
    | CodexOptions
    | FreebuffOptions
    | OpencodeOptions
    | HermesOptions
    | MuseOptions
    | MuseAiOptions
    | CommandcodeOptions
    | GeminiOptions
    | AntigravityOptions
    | CopilotOptions
)


@dataclass(frozen=True)
class WorkerRequest:
    prompt: str | None
    session_id: str | None
    cwd: str
    permission_mode: str
    callbacks: WorkerCallbacks
    options: WorkerOptions
    model: str = ""
    effort: str = ""
