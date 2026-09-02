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


def _python_files(root: Path = ROOT) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        # Build output, virtualenvs and caches are nobody's source. Matched by
        # prefix on directory components, relative to the swept root: a
        # contributor's virtualenv may be named `.venv-win` and a staging
        # directory `dist_new`, a file whose own name starts with dist is still
        # source wherever it sits, and a checkout that happens to live under a
        # path containing one of these words -- including pytest's own basetemp,
        # which is how the test below exercises this -- is not thrown away
        # wholesale. `parts[:-1]` is every directory on the way down; the final
        # component is the file itself and is never filtered.
        relative = path.relative_to(root)
        if any(
            part.startswith((".venv", "venv", "build", "dist", "__pycache__", ".test-tmp"))
            for part in relative.parts[:-1]
        ):
            continue
        found.append(path)
    return found


def test_there_are_sources_to_check():
    assert len(_python_files()) > 10, "the file sweep found almost nothing — check the filter"


def test_the_sweep_ignores_a_virtualenv_however_it_is_named(tmp_path):
    """The exclusion is by prefix, not one blessed spelling.

    A contributor's virtualenv was `.venv-win` and their staging directory
    `dist_new`; the sweep compiled all 4,652 files of their site-packages as
    parametrised cases, which is where a "5682 passed" report came from. A
    real directory layout, written out here, is the test: exactly the build
    output and the virtualenvs go, exactly the source stays.
    """
    for directory in (".venv", ".venv-win", "venv", "build", "dist", "dist_new"):
        module = tmp_path / directory / "x.py"
        module.parent.mkdir(parents=True)
        module.write_text("")
    # A file whose own name starts with dist is source, wherever it sits; the
    # exclusion reads directory components, not the file name.
    (tmp_path / "distance_util.py").write_text("")
    (tmp_path / "real.py").write_text("")

    found = _python_files(tmp_path)

    assert {p.relative_to(tmp_path).as_posix() for p in found} == {
        "real.py",
        "distance_util.py",
    }


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
