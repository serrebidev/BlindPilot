"""No module may compile with a warning attached to it.

An invalid escape sequence — `"\\attacker\\share"` written without the `r` —
is not an error. Python compiles it, emits a `SyntaxWarning`, and carries on,
so it survives every ordinary test run. It reached this repository once
already. `ruff` did not see it either, because `W` was not in the select list.

The release build runs `pytest -W error`, so a warning like that fails a
release and nothing before it says a word. This catches it at the point it is
written instead, and does so whatever the linter happens to be configured with.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _python_files() -> list[Path]:
    found: list[Path] = []
    for path in sorted(ROOT.rglob("*.py")):
        parts = set(path.parts)
        # Build output, virtualenvs and caches are nobody's source.
        if parts & {".venv", "venv", "build", "dist", "__pycache__", ".test-tmp"}:
            continue
        found.append(path)
    return found


def test_there_are_sources_to_check():
    assert len(_python_files()) > 10, "the file sweep found almost nothing — check the filter"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_module_compiles_without_warnings(path: Path):
    source = path.read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        # "always", not "error": collect every warning rather than stopping at
        # the first, so one run names all of them.
        warnings.simplefilter("always")
        compile(source, str(path), "exec")
    complaints = [f"{w.category.__name__}: {w.message}" for w in caught]
    assert not complaints, f"{path.relative_to(ROOT)} compiles with warnings: {complaints}"
