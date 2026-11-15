"""Computer-use tool definitions and the bounded executor for one turn.

The model chooses actions from the target window's numbered accessibility
tree. Each action validates the window first (inside `ComputerUseSession`),
and the executor enforces the per-turn bound and optional per-action approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from computer_use import ComputerUseError, ComputerUseSession

MAX_ACTIONS_PER_TURN = 25

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_controls",
            "description": "List the target window's controls as a numbered tree.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_control",
            "description": "Press the numbered control from the last list.",
            "parameters": {
                "type": "object",
                "properties": {"number": {"type": "integer"}},
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_control_text",
            "description": "Set text on the numbered control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["number", "text"],
            },
        },
    },
]


@dataclass
class ComputerUseExecutor:
    """Runs client-side computer-use calls for one turn, with a bound."""

    session: ComputerUseSession
    cancel: Any
    ask: Callable[[str], bool] | None = None
    actions_taken: int = 0

    def bound_hit(self) -> bool:
        return self.actions_taken >= MAX_ACTIONS_PER_TURN

    def run(self, name: str, args: dict[str, Any]) -> str:
        """Run one tool call; the result becomes the tool's reply text."""
        if self.bound_hit():
            return f"This turn used its {MAX_ACTIONS_PER_TURN} actions. Continue next turn."
        if self.cancel.is_set():
            return "Stopped. No more actions will run."
        label = self._describe(name, args)
        if self.ask is not None and not self.ask(label):
            return f"Skipped (not approved): {label}"
        try:
            if name == "list_controls":
                return "\n".join(ref.describe() for ref in self.session.list_controls())
            if name == "press_control":
                self.actions_taken += 1
                return self.session.press(int(args.get("number", 0)))
            if name == "set_control_text":
                self.actions_taken += 1
                return self.session.set_value(int(args.get("number", 0)), str(args.get("text", "")))
        except ComputerUseError as exc:
            return f"Could not act: {exc}"
        return f"Unknown computer-use tool {name!r}."

    @staticmethod
    def _describe(name: str, args: dict[str, Any]) -> str:
        if name == "press_control":
            return f"Press control {args.get('number')}"
        if name == "set_control_text":
            return f"Set text on control {args.get('number')}"
        return name
