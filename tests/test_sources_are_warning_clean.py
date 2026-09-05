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

import shutil
import subprocess
import uuid
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
            part.startswith((".venv", "venv", "build", "dist", "__pycache__", ".test-tmp", "docs"))
            for part in relative.parts[:-1]
        ):
            continue
        found.append(path)
    return found


def _git_ignored(paths: list[Path]) -> set[Path]:
    """The subset of ``paths`` that .gitignore hides, or nothing if git cannot say.

    A scratch folder can be called anything, and the prefix list above cannot
    name them all; .gitignore already does. Without git (an unpacked source
    archive) every file is swept, which is the old behaviour.
    """
    if not paths:
        return set()
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=ROOT,
            input="\0".join(str(path.relative_to(ROOT)) for path in paths),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    # Exit status 1 means nothing was ignored; anything above that is git
    # refusing to answer, in which case nothing is taken away.
    if completed.returncode > 1:
        return set()
    return {ROOT / name for name in completed.stdout.split("\0") if name}


def _sources() -> list[Path]:
    """The repository's own Python files: the sweep minus what git ignores."""
    found = _python_files()
    ignored = _git_ignored(found)
    return [path for path in found if path not in ignored]


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
    for directory in (".venv", ".venv-win", "venv", "build", "dist", "dist_new", "docs/x/cache"):
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


def test_the_sweep_skips_what_git_ignores():
    """A scratch folder .gitignore hides is not source, whatever it is called.

    `.tmp_*` is in .gitignore and not in the prefix list above, so this is
    the git rule doing the work. The probe sits in the repository root
    because the ignore rules are the repository's.
    """
    probe_dir = ROOT / f".tmp_sweep_probe_{uuid.uuid4().hex}"
    probe = probe_dir / "ignored.py"
    probe_dir.mkdir()
    try:
        probe.write_text("")
        assert probe in _python_files(), "the probe must reach the ignore check to test it"
        assert probe not in _sources()
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_module_compiles_without_warnings(path: Path):
    source = path.read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        # "always", not "error": collect every warning rather than stopping at
        # the first, so one run names all of them.
        warnings.simplefilter("always")
        compile(source, str(path), "exec")
    complaints = [f"{w.category.__name__}: {w.message}" for w in caught]
    assert not complaints, f"{path.relative_to(ROOT)} compiles with warnings: {complaints}"
