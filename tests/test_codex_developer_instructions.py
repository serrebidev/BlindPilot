"""Telling Codex to ask through its question tool, without taking a slot away.

Codex's ``request_user_input`` is the tool BlindPilot opens its question
dialog from. A model that writes its question into its answer instead sends
nothing to open a dialog with, so the instruction to use the tool is passed
for this app server alone -- the same way the tool is switched on.

``developer_instructions`` is a real, typed Codex config key (checked against
Codex 0.154.0: a string loads, an integer is refused as a config error), and
``-c`` replaces a key rather than adding to it. Somebody who has written their
own developer instructions must not lose them for as long as BlindPilot is
running, which is the part these check.
"""

from __future__ import annotations

import agent_backends as ab


def _instruction_value(args) -> str:
    for item in args:
        if item.startswith("developer_instructions="):
            return item.split("=", 1)[1]
    raise AssertionError(f"no developer_instructions in {args!r}")


def test_the_instruction_names_codex_own_question_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_codex_home", lambda: tmp_path)
    value = _instruction_value(ab.codex_question_args())
    assert "request_user_input" in value
    assert "BlindPilot" in value


def test_the_value_is_a_quoted_toml_string(tmp_path, monkeypatch):
    # `-c` parses the value as TOML and falls back to treating it as a literal
    # only when that fails. Quoting it means the fallback is never what is
    # being relied on, and a newline or a quote in the text cannot change how
    # the rest of it parses.
    monkeypatch.setattr(ab, "_codex_home", lambda: tmp_path)
    value = _instruction_value(ab.codex_question_args())
    assert value.startswith('"') and value.endswith('"')


def test_the_question_tool_is_still_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_codex_home", lambda: tmp_path)
    args = ab.codex_question_args()
    assert "tools.experimental_request_user_input={enabled=true}" in args
    assert ab._CODEX_QUESTION_FEATURE in args


def test_instructions_the_person_wrote_are_kept_and_come_first(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        'developer_instructions = "Always run the tests before you say you are done."\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "_codex_home", lambda: tmp_path)
    value = _instruction_value(ab.codex_question_args())
    assert "Always run the tests before you say you are done." in value
    assert value.index("Always run the tests") < value.index("request_user_input")


def test_a_config_codex_itself_could_not_load_costs_nothing(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("this is not = = toml\n", encoding="utf-8")
    monkeypatch.setattr(ab, "_codex_home", lambda: tmp_path)
    # Codex will report the config error itself. BlindPilot's job here is only
    # to not add a second failure of its own on the way past.
    assert "request_user_input" in _instruction_value(ab.codex_question_args())
