"""Prepare a conversation prefix without modifying provider history."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from session_history import HistoryEntry, HistoryTurn


def source_identity(entry: HistoryEntry) -> tuple[int, int] | None:
    if not entry.path or not Path(entry.path).is_file():
        return None
    stat = Path(entry.path).stat()
    return stat.st_mtime_ns, stat.st_size


@dataclass(frozen=True)
class RecoveryRequest:
    source: HistoryEntry
    selected_turn: int
    operation: str
    identity: tuple[int, int] | None = None


@dataclass(frozen=True)
class RecoveryResult:
    turns: tuple[HistoryTurn, ...]
    prompt: str
    label: str
    fork_source: str = ""
    fork_at: str = ""
    context: str = ""
    session_id: None = None
    attachments: tuple[str, ...] = ()


class _NumberedTurn(Protocol):
    response_number: int
    response: str


def turn_index_for_response(turns: Sequence[_NumberedTurn], number: int) -> int | None:
    return next(
        (
            index
            for index, turn in enumerate(turns)
            if turn.response_number == number and turn.response.strip()
        ),
        None,
    )


def prepare_recovery(request: RecoveryRequest, turns: list[HistoryTurn]) -> RecoveryResult:
    if request.operation not in {"branch", "retry"}:
        raise ValueError("Unknown recovery operation.")
    if not 0 <= request.selected_turn < len(turns):
        raise ValueError("That conversation turn no longer exists.")
    if request.identity is not None and source_identity(request.source) != request.identity:
        raise ValueError("The source conversation changed. Open it again before branching.")
    retry = request.operation == "retry"
    prefix = tuple(turns[: request.selected_turn + (not retry)])
    prompt = turns[request.selected_turn].prompt if retry else ""
    attachments = turns[request.selected_turn].attachments if retry else ()
    source = request.source
    boundary = ""
    if prefix and prefix[-1].response.strip():
        if source.backend == "claude":
            boundary = prefix[-1].message_id
        elif source.backend == "codex":
            boundary = prefix[-1].turn_id
    if boundary and source.session_id:
        return RecoveryResult(
            prefix,
            prompt,
            "Native conversation copy",
            source.session_id,
            boundary,
            attachments=attachments,
        )
    context = "\n\n".join(f"User: {turn.prompt}\nAssistant: {turn.response}" for turn in prefix)
    if context:
        context = (
            "The following is copied conversation context, not a request to repeat its actions. "
            "Continue only with my new message after this context.\n\n" + context
        )
    return RecoveryResult(
        prefix,
        prompt,
        "New conversation with context copy",
        context=context,
        attachments=attachments,
    )
