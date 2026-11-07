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
    seen: list[tuple[Path, bool]] = []

    assert preferences.load_config((path,), on_damaged=lambda p, kept: seen.append((p, kept))) == {}

    assert len(seen) == 1
    kept, was_kept = seen[0]
    assert was_kept is True
    assert kept.name.startswith("config.json.broken-")
    assert kept.read_text(encoding="utf-8") == DAMAGED
    assert not path.exists()


def test_a_damaged_file_that_cannot_be_kept_is_still_reported(tmp_path: Path, monkeypatch) -> None:
    """A locked file is the case this exists for: the move fails and the next
    save replaces it, so saying nothing is exactly wrong."""
    path = tmp_path / "config.json"
    path.write_text(DAMAGED, encoding="utf-8")

    def refuse(_source, _target):
        raise OSError("in use")

    monkeypatch.setattr(preferences.os, "replace", refuse)
    seen: list[tuple[Path, bool]] = []

    assert preferences.load_config((path,), on_damaged=lambda p, kept: seen.append((p, kept))) == {}

    assert seen == [(path, False)]
    assert path.read_text(encoding="utf-8") == DAMAGED


def test_a_missing_file_is_not_damage(tmp_path: Path) -> None:
    seen: list[tuple[Path, bool]] = []

    assert (
        preferences.load_config(
            (tmp_path / "absent.json",), on_damaged=lambda p, kept: seen.append((p, kept))
        )
        == {}
    )

    assert seen == []


def test_a_path_that_cannot_be_opened_is_not_called_damaged(tmp_path: Path) -> None:
    """A lock or a permission says nothing about the contents, and the save that
    follows already speaks for itself."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    seen: list[tuple[Path, bool]] = []

    assert (
        preferences.load_config(
            (blocker / "config.json",), on_damaged=lambda p, kept: seen.append((p, kept))
        )
        == {}
    )

    assert seen == []


def test_the_next_save_starts_fresh_without_destroying_the_kept_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text("not json at all", encoding="utf-8")
    kept: list[tuple[Path, bool]] = []

    preferences.load_config((path,), on_damaged=lambda p, k: kept.append((p, k)))
    assert preferences.update_config(path, {"backend": "codex"}) is True

    assert kept[0][0].read_text(encoding="utf-8") == "not json at all"
    assert preferences.load_config((path,)) == {"backend": "codex"}


def test_each_outcome_says_something_different(tmp_path: Path, monkeypatch) -> None:
    """The words are the whole fix: one sentence names the kept copy, the other
    says there is nothing to look for."""
    import blindpilot_app

    spoken: list[str] = []
    monkeypatch.setattr(blindpilot_app, "announce", lambda text, *a, **k: spoken.append(text))
    kept_copy = tmp_path / "config.json.broken-20260101-000000"

    blindpilot_app._note_damaged_config(kept_copy, True)
    blindpilot_app._note_damaged_config(tmp_path / "config.json", False)

    assert kept_copy.name in spoken[0]
    assert "could not be kept" in spoken[1]
    assert "broken-" not in spoken[1]
