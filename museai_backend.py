"""muse.ai backend for BlindPilot.

muse.ai is Meta's personal agent: it runs on its own cloud machine, with its
own connectors, memory and browser, and is not the same product as Muse Code
(the ``muse`` coding CLI this app also drives). BlindPilot talks to it through
``muse-cli`` (PyPI ``muse-cli``, installed with ``uv tool install muse-cli``),
which reaches the agent's gateway with the muse.ai sign-in cookies it keeps
in ``~/.config/muse-cli``.

BlindPilot opens the main chat by default; new conversations are side chats.
A turn is ``muse-cli send
--thread <id> --wait <seconds> <text>``, which prints one JSON object with
the agent's reply (measured at 0.3.2). The turn itself is in
``museai_worker``.

The agent runs somewhere else, so it cannot read files on this machine: the
working folder means nothing to it, and attachments are not passed.

CLI probes go through ``agent_backends._probe_backend`` and binary discovery
through ``agent_backends.find_backend_cli``, resolved at call time, so the
suite's "every backend" tests fence this adapter off the way they fence the
others.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

import agent_backends
from agent_backends import BACKEND_MUSEAI


def museai_config_path() -> Path:
    return Path.home() / ".config" / "muse-cli" / "config.json"


def _json_from(text: str) -> object:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def museai_status(timeout: int = 20) -> Optional[dict]:
    """``muse-cli status`` as a dict, or None when it is missing or signed out."""
    binary = agent_backends.find_backend_cli(BACKEND_MUSEAI)
    if not binary:
        return None
    code, text = agent_backends._probe_backend(binary, ["status"], timeout)
    payload = _json_from(text) if code == 0 else None
    return payload if isinstance(payload, dict) else None


def museai_auth_ok(timeout: int = 20) -> bool:
    """Signed in means the gateway answered ``status`` with the agent's VM."""
    status = museai_status(timeout)
    return bool(status and status.get("vm_id"))


def museai_account_lines() -> list[str]:
    status = museai_status()
    if not status:
        return ["Signed in: no"]
    lines = ["Signed in: yes"]
    identity = status.get("identity")
    name = identity.get("name") if isinstance(identity, dict) else ""
    if name:
        lines.append(f"Agent: {name}")
    if status.get("sessions") is not None:
        lines.append(f"Chats: {status['sessions']}")
    return lines


def _cli_json(args: list[str], timeout: int = 60) -> object:
    binary = agent_backends.find_backend_cli(BACKEND_MUSEAI)
    if not binary:
        return None
    code, text = agent_backends._probe_backend(binary, args, timeout)
    if code != 0:
        return None
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def museai_chats() -> list[dict]:
    """The agent's main chat and side chats, as muse-cli lists them."""
    found = _cli_json(["threads"])
    chats = [c for c in found if isinstance(c, dict)] if isinstance(found, list) else []
    return [c for c in chats if c.get("session_id") and not c.get("archived")]


def museai_main_session_id() -> Optional[str]:
    return next((str(c["session_id"]) for c in museai_chats() if c.get("thread") is False), None)


MUSEAI_VIEWS = {
    "approvals": "egress.approvals",
    "schedules": "tasks.list",
    "feed": "feed.list",
    "ideas": "api.idea-cards.list",
    "goals": "goals.list",
}


def museai_result(payload: object) -> dict:
    """Raw commands retain the gateway envelope; errors are not empty views."""
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("muse.ai could not be reached. Check your sign-in and try Refresh.")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("muse.ai returned an unreadable response.")
    return result


def museai_pending(result: dict) -> list[dict]:
    pending = result.get("pending_approvals", result.get("pending"))
    return [a for a in _museai_records(pending) if a.get("approval_id")]


def _museai_records(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("muse.ai returned an unreadable list.")
    return value


def museai_items(view: str) -> list[dict]:
    # shortcut: current gateway page only; add paging when older feed or idea pages are needed.
    result = museai_result(_cli_json(["raw", MUSEAI_VIEWS[view]]))
    if view == "approvals":
        recent = result.get("recent_approvals", result.get("recent", []))
        return [dict(a, _pending=True) for a in museai_pending(result)] + [
            dict(a, _pending=False) for a in _museai_records(recent)
        ]
    if view == "schedules":
        return _museai_records(result.get("schedules"))
    if view == "goals":
        pending = [(goal, "") for goal in reversed(_museai_records(result.get("goals")))]
        goals, seen = [], set()
        while pending:
            goal, parent = pending.pop()
            goal_id = _museai_id(goal, "goal_id", "id")
            if goal_id in seen:
                continue
            seen.add(goal_id)
            goals.append(dict(goal, parent_title=parent))
            children = _museai_records(goal.get("subGoals", goal.get("sub_goals", [])) or [])
            pending.extend((child, str(goal.get("title") or "")) for child in reversed(children))
        return goals
    if view == "feed":
        return [
            dict(unit, date=day.get("local_date", ""), edition=edition.get("kind", ""))
            for day in _museai_records(result.get("days"))
            for edition in _museai_records(day.get("editions", []))
            for unit in _museai_records(edition.get("units", []))
        ]
    return [
        dict(card, section=section.get("title", ""))
        for section in _museai_records(result.get("sections"))
        for card in _museai_records(section.get("cards", section.get("ideas", [])))
    ]


def museai_item_title(view: str, item: dict) -> str:
    display = item.get("display") or {}
    if not isinstance(display, dict):
        raise ValueError("muse.ai returned unreadable item details.")
    title = str(
        display.get("summary_title") or item.get("title") or item.get("display_name") or "Untitled"
    )
    if view == "approvals":
        title = (
            f"{'Pending' if item.get('_pending') else item.get('decision') or 'Recent'}: {title}"
        )
    elif view in ("goals", "suggestions") and item.get("status"):
        title = f"{item['status']}: {title}"
    if view == "goals" and item.get("parent_title"):
        title += f" (under {item['parent_title']})"
    return title


def museai_item_text(view: str, item: dict) -> str:
    lines = [museai_item_title(view, item)]
    if view == "approvals":
        display = item.get("display") or item
        question = display.get("permission_question") or {}
        payload = item.get("payload") or {}
        if not isinstance(question, dict) or not isinstance(payload, dict):
            raise ValueError("muse.ai returned unreadable approval details.")
        network = payload.get("network_payload") or item
        rows = display.get("detail_rows", [])
        if (
            not isinstance(network, dict)
            or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
        ):
            raise ValueError("muse.ai returned unreadable approval details.")
        for key in (
            "purpose_summary",
            "scope_summary",
            "reason",
            "short_explanation",
            "rich_explanation",
        ):
            if display.get(key):
                lines.append(str(display[key]))
        if question.get("text"):
            lines.append(str(question["text"]))
        for row in rows:
            lines.append(f"{row.get('label', '')}: {row.get('value', '')}")
        for key in ("host", "port", "scheme", "method", "path"):
            if network.get(key):
                lines.append(f"{key.title()}: {network[key]}")
    elif view == "schedules":
        lines += ["Disabled" if item.get("enabled") is False else "Enabled"]
        for key, label in (
            ("schedule_key", "Schedule"),
            ("timezone", "Timezone"),
            ("next_run_at_utc", "Next run (UTC)"),
        ):
            if item.get(key):
                lines.append(f"{label}: {item[key]}")
    else:
        content = item.get("content") or {}
        for key in (
            "date",
            "edition",
            "section",
            "kicker",
            "summary",
            "body_md",
            "prerequisiteNotes",
            "buildStatus",
            "description",
            "status",
            "momentum",
            "result_summary",
            "error_text",
        ):
            if item.get(key):
                lines.append(str(item[key]))
        for key in ("title", "summary", "buildSummary"):
            if isinstance(content, dict) and content.get(key):
                lines.append(str(content[key]))
        for part in _museai_records(item.get("items", [])):
            if isinstance(part, dict) and isinstance(part.get("content"), dict):
                lines.extend(
                    str(value)
                    for value in part["content"].values()
                    if isinstance(value, str) and value
                )
        for update in _museai_records(item.get("updates", [])):
            lines.extend(
                str(update[key]) for key in ("title", "summary", "description") if update.get(key)
            )
    return "\n\n".join(dict.fromkeys(lines))


def museai_read_item(view: str, item: dict) -> dict:
    if view == "goals":
        result = _museai_call("goals.get", {"id": _museai_id(item, "goal_id", "id")})
        goal = result.get("goal")
        if not isinstance(goal, dict):
            raise ValueError("muse.ai could not read this goal.")
        return dict(
            goal, updates=result.get("updates", []), suggestions=result.get("suggestions", [])
        )
    if view != "ideas":
        return item
    found = _cli_json(["idea", str(item.get("id") or item.get("ideaCardId") or "")])
    card = found.get("card") if isinstance(found, dict) else None
    if not isinstance(card, dict):
        raise ValueError("muse.ai could not read this idea.")
    for part in _museai_records(card.get("items", [])):
        if part.get("content") is not None and not isinstance(part["content"], dict):
            raise ValueError("muse.ai returned unreadable idea parts.")
    return dict(card, section=item.get("section", ""))


def museai_goal_suggestions(goal: dict) -> list[dict]:
    detail = museai_read_item("goals", goal)
    rows = _museai_records(detail.get("suggestions", []))
    result = []
    for row in rows:
        idea = row.get("idea") or {}
        if not isinstance(idea, dict):
            raise ValueError("muse.ai returned an unreadable goal suggestion.")
        result.append(
            dict(
                row,
                title=idea.get("title", "Untitled suggestion"),
                summary=idea.get("summary", ""),
                description=idea.get("rationale", ""),
            )
        )
    return result


def _museai_id(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError("This muse.ai item has no usable ID. Refresh and try again.")


def _museai_call(method: str, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
    args = ["raw", method]
    for key, value in (params or {}).items():
        args += ["--param", f"{key}={quote(value, safe='')}"]
    if body is not None:
        args += ["--body", json.dumps(body)]
    return museai_result(_cli_json(args))


def museai_idea_id(item: dict) -> str:
    if "unit_id" in item:
        action = item.get("idea_action")
        if not isinstance(action, dict):
            raise ValueError("This feed post has no executable idea.")
        return _museai_id(action, "idea_id")
    return _museai_id(item, "ideaCardId", "id")


def museai_run_idea(item: dict, item_ids: Optional[list[str]] = None) -> dict:
    idea_id = museai_idea_id(item)
    body: dict = {"ideaCardId": idea_id, "mode": "full"}
    if item_ids is not None:
        if not item_ids or any(
            not isinstance(value, str) or not value.strip() for value in item_ids
        ):
            raise ValueError("Select at least one part of the idea to run.")
        body.update(mode="selectedItems", itemIds=item_ids)
    result = _museai_call("api.idea-cards.execute", {"ideaCardId": idea_id}, body)
    if result.get("status") not in ("queued", "accepted"):
        raise ValueError(
            "muse.ai did not confirm that the idea started. Refresh before trying again."
        )
    return result


def museai_action_session(result: dict) -> str:
    chat = result.get("chat")
    if chat is None:
        return ""
    if not isinstance(chat, dict):
        raise ValueError("The action started, but muse.ai returned unreadable chat details.")
    session = chat.get("session_id", chat.get("sessionId"))
    return session if isinstance(session, str) else ""


def museai_run_schedule(item: dict) -> dict:
    return _museai_call("tasks.run", {"job_id": _museai_id(item, "id")}, {})


def museai_schedule_history(item: dict) -> str:
    job_id = _museai_id(item, "id")
    result = _museai_call("tasks.runs", body={"job_id": job_id, "limit": 100})
    runs = _museai_records(result.get("runs"))
    from datetime import datetime, timezone

    lines = []
    for run in runs:
        if run.get("job_id") != job_id:
            continue
        stamp = run.get("scheduled_for_utc")
        try:
            if not isinstance(stamp, (str, int, float)):
                raise ValueError("Missing run time")
            when = datetime.fromtimestamp(float(stamp), timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            when = "Unknown time"
        lines.append(
            "\n".join(
                str(value)
                for value in (
                    when,
                    run.get("status", "Unknown status"),
                    run.get("trigger_reason", ""),
                    run.get("result_summary", ""),
                    run.get("error_text", ""),
                )
                if value
            )
        )
    return "\n\n".join(lines) or "No run history for this schedule."


def museai_item_links(item: dict) -> list[str]:
    text = str(item.get("body_md") or "")
    urls = re.findall(r"https?://[^\s<>\"()]+", text)
    attachment = item.get("attachment")
    if isinstance(attachment, dict):
        urls += [
            attachment[key] for key in ("social_embed_url",) if isinstance(attachment.get(key), str)
        ]
    result = []
    for url in urls:
        url = url.rstrip(".,;!")
        try:
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username:
                result.append(url)
        except ValueError:
            continue
    return list(dict.fromkeys(result))


def museai_goal_status(item: dict, status: str) -> dict:
    if status not in {"active", "paused", "completed", "retired"}:
        raise ValueError("Unsupported muse.ai goal status.")
    goal_id = _museai_id(item, "goal_id", "id")
    return _museai_call("goals.update", {"id": goal_id}, {"status": status})


def museai_goal_edit(item: dict, title: str, description: str) -> dict:
    if not title.strip():
        raise ValueError("A goal needs a title.")
    return _museai_call(
        "goals.update",
        {"id": _museai_id(item, "goal_id", "id")},
        {"title": title.strip(), "description": description},
    )


def museai_goal_delete(item: dict) -> dict:
    return _museai_call("goals.delete", {"id": _museai_id(item, "goal_id", "id")})


def museai_goal_decide(goal_id: str, suggestion: dict, decision: str) -> dict:
    if decision not in {"accepted", "dismissed"}:
        raise ValueError("Unsupported muse.ai suggestion decision.")
    _museai_id({"id": goal_id}, "id")
    suggestion_id = _museai_id(suggestion, "suggestion_id", "suggestionId", "id")
    return _museai_call(
        "goals.suggestions.decide",
        {"goal_id": goal_id, "suggestion_id": suggestion_id},
        {"decision": decision},
    )


def museai_allowed_decisions(approval: dict) -> set[str]:
    options = approval.get("decision_options")
    if options is None:
        return {"allow_once", "deny"}
    if not isinstance(options, list):
        raise ValueError("muse.ai returned unreadable approval choices.")
    return {
        str(option.get("kind")) if isinstance(option, dict) else str(option) for option in options
    } & {"allow_once", "deny"}


def museai_decision_args(approval_id: str, decision: str, reason: str = "") -> list[str]:
    if not approval_id or decision not in {"allow_once", "deny"}:
        raise ValueError("Choose a pending approval and Allow once or Deny.")
    body = {"approval_id": approval_id, "decision": decision}
    if reason:
        body["reason"] = reason
    return [
        "raw",
        "egress.approval.decide",
        "--param",
        f"approval_id={approval_id}",
        "--body",
        json.dumps(body),
    ]


def museai_decide(approval_id: str, decision: str, reason: str = "") -> None:
    museai_result(_cli_json(museai_decision_args(approval_id, decision, reason)))


def museai_chat_snapshot(session_id: str, limit: int = 300) -> tuple[list[tuple[str, str]], int]:
    """One chat as (what was asked, everything the agent said back) pairs.

    Everything means every message the agent posted after the question --
    status updates included -- joined in order, as the turn showed them.
    """
    found = _cli_json(["history", "--thread", session_id, "--limit", str(limit)])
    turns: list[tuple[str, list[str]]] = []
    sequence = 0
    for message in found if isinstance(found, list) else []:
        if not isinstance(message, dict):
            continue
        if isinstance(message.get("seq"), int):
            sequence = max(sequence, message["seq"])
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if message.get("role") == "user":
            turns.append((text, []))
        elif message.get("role") == "assistant":
            if not turns:
                turns.append(("", []))
            turns[-1][1].append(text)
    return [(prompt, "\n\n".join(said)) for prompt, said in turns], sequence


def museai_chat_turns(session_id: str, limit: int = 300) -> list[tuple[str, str]]:
    return museai_chat_snapshot(session_id, limit)[0]


def museai_latest_seq(session_id: str) -> int:
    """The newest agent event in a chat, text or not; 0 if it cannot be read.

    What an open muse.ai tab compares against to notice muse.ai working again
    (or still) after a turn has ended.
    """
    found = _cli_json(["history", "--thread", session_id, "--limit", "5", "--raw"], 30)
    events = found.get("chat_events") if isinstance(found, dict) else None
    seqs = [
        int((e.get("payload") or {}).get("seq") or e.get("seq") or 0)
        for e in (events if isinstance(events, list) else [])
        if isinstance(e, dict) and e.get("event_name") == "message.assistant"
    ]
    return max(seqs, default=0)


def museai_install_argv(upgrade: bool = False) -> Optional[list[str]]:
    """How to install (or upgrade) muse-cli: uv first, then pip for this user."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "tool", "upgrade" if upgrade else "install", "muse-cli"]
    # A frozen build's sys.executable is BlindPilot itself, not a Python.
    python = None if getattr(sys, "frozen", False) else sys.executable
    python = python or shutil.which("python3") or shutil.which("python") or shutil.which("py")
    if not python:
        return None
    return [python, "-m", "pip", "install", "--user", "--upgrade", "muse-cli"]


MUSEAI_MISSING_PREREQ = (
    "muse.ai needs muse-cli, a Python package. Install uv "
    "(https://docs.astral.sh/uv/) or Python, then choose Install again, or run: "
    "uv tool install muse-cli"
)
