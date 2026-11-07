"""A damaged settings file is kept and reported, not silently replaced.

The next save writes a whole new file, so an unreadable one left in place was
destroyed by the first preference change, and the window started from defaults
without saying anything.
"""

from __future__ import annotations

from pathlib import Path

from accessible_ai.storage import preferences

DAMAGED = '{"remote_control": '


def test_a_damaged_file_is_kept_and_the_caller_is_told(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(DAMAGED, encoding="utf-8")
    seen: list[Path] = []

    assert preferences.load_config((path,), on_damaged=seen.append) == {}

    assert len(seen) == 1
    kept = seen[0]
    assert kept.name.startswith("config.json.broken-")
    assert kept.read_text(encoding="utf-8") == DAMAGED
    assert not path.exists()


def test_a_missing_file_is_not_damage(tmp_path: Path) -> None:
    seen: list[Path] = []

    assert preferences.load_config((tmp_path / "absent.json",), on_damaged=seen.append) == {}

    assert seen == []


def test_the_next_save_starts_fresh_without_destroying_the_kept_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text("not json at all", encoding="utf-8")
    kept: list[Path] = []

    preferences.load_config((path,), on_damaged=kept.append)
    assert preferences.update_config(path, {"backend": "codex"}) is True

    assert kept[0].read_text(encoding="utf-8") == "not json at all"
    assert preferences.load_config((path,)) == {"backend": "codex"}
