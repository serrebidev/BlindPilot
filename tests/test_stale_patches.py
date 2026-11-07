"""The guard for ticket #47: a patched copy is not the copy the code calls.

Synthetic trees drive `stale_patches` so its behaviour is pinned without
depending on the real tree's current state. The last test runs it over this
repository, which is what turns the shape into a failing build.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stale_patch_contract import stale_baseline_entries, stale_patches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _tree(tmp_path: Path, prod: dict[str, str], tests: dict[str, str]) -> tuple[Path, Path]:
    prod_dir = tmp_path / "prod"
    test_dir = tmp_path / "tests"
    prod_dir.mkdir()
    test_dir.mkdir()
    for name, body in prod.items():
        (prod_dir / f"{name}.py").write_text(body, encoding="utf-8")
    for name, body in tests.items():
        (test_dir / f"{name}.py").write_text(body, encoding="utf-8")
    return test_dir, prod_dir


def test_a_patch_on_a_module_another_module_holds_is_reported(tmp_path):
    """The shipped case: `installers` held its own `find_backend_cli`, so the
    test patching the window's copy left the installer on the real one."""
    test_dir, prod_dir = _tree(
        tmp_path,
        prod={
            "backend": "def find_backend_cli(backend):\n    return '/real'\n",
            "worker": "from backend import find_backend_cli\n",
            "window": "from backend import find_backend_cli\n",
        },
        tests={
            "test_window": (
                "import window\n"
                "def test_it(monkeypatch):\n"
                "    monkeypatch.setattr(window, 'find_backend_cli', lambda b: '/fake')\n"
            )
        },
    )

    found = stale_patches(test_dir, prod_dir)

    assert len(found) == 1, found
    assert found[0].patched_module == "window"
    assert found[0].name == "find_backend_cli"
    # The definer holds the original too, so patching the window's copy leaves
    # both `backend` and `worker` calling the real one.
    assert found[0].other_holders == ("backend", "worker")


def test_patching_every_holder_is_not_reported(tmp_path):
    """A test that patches every copy reaches the code it means to.

    The holder set for a name is every module holding a copy, so "every" means
    both of the two copies here - the source and its one importer.
    """
    test_dir, prod_dir = _tree(
        tmp_path,
        prod={
            "backend": "def find_backend_cli(backend):\n    return '/real'\n",
            "window": "from backend import find_backend_cli\n",
        },
        tests={
            "test_both": (
                "import backend, window\n"
                "def test_it(monkeypatch):\n"
                "    monkeypatch.setattr(backend, 'find_backend_cli', lambda b: '/fake')\n"
                "    monkeypatch.setattr(window, 'find_backend_cli', lambda b: '/fake')\n"
            )
        },
    )

    assert stale_patches(test_dir, prod_dir) == []


def test_a_name_only_one_module_holds_is_not_reported(tmp_path):
    """No copy exists, so a patch on the source reaches every caller."""
    test_dir, prod_dir = _tree(
        tmp_path,
        prod={
            "backend": "def find_backend_cli(backend):\n    return '/real'\n",
            "worker": "import backend\n",
            "window": "import backend\n",
        },
        tests={
            "test_window": (
                "import backend\n"
                "def test_it(monkeypatch):\n"
                "    monkeypatch.setattr(backend, 'find_backend_cli', lambda b: '/fake')\n"
            )
        },
    )

    assert stale_patches(test_dir, prod_dir) == []


def test_a_patch_target_written_as_a_string_is_reported(tmp_path):
    """`mock.patch("window.find_backend_cli")` is the same hazard, spelled as a path."""
    test_dir, prod_dir = _tree(
        tmp_path,
        prod={
            "backend": "def find_backend_cli(backend):\n    return '/real'\n",
            "worker": "from backend import find_backend_cli\n",
            "window": "from backend import find_backend_cli\n",
        },
        tests={
            "test_window": (
                "def test_it():\n"
                "    with patch('window.find_backend_cli', lambda b: '/fake'):\n"
                "        pass\n"
            )
        },
    )

    found = stale_patches(test_dir, prod_dir)

    assert [f.name for f in found] == ["find_backend_cli"]


def test_a_local_alias_for_the_module_is_resolved(tmp_path):
    """Tests import the window as `app`, so `app` has to resolve to its file."""
    test_dir, prod_dir = _tree(
        tmp_path,
        prod={
            "backend": "def find_backend_cli(backend):\n    return '/real'\n",
            "worker": "from backend import find_backend_cli\n",
            "window": "from backend import find_backend_cli\n",
        },
        tests={
            "test_window": (
                "import window as app\n"
                "def test_it(monkeypatch):\n"
                "    monkeypatch.setattr(app, 'find_backend_cli', lambda b: '/fake')\n"
            )
        },
    )

    found = stale_patches(test_dir, prod_dir)

    assert found and found[0].patched_module == "window"


def test_this_repository_adds_no_new_stale_patch():
    """Every test patch must reach the code it is meant to control.

    `BASELINE` holds the patches that predate this guard; a new one fails here.
    """
    found = stale_patches(ROOT / "tests", ROOT)
    assert not found, "\n".join(sorted(str(item) for item in found))


def test_the_baseline_does_not_grow_stale():
    """An entry that no longer fires must be deleted, so the list only shrinks.

    Without this the baseline accumulates exemptions for code that has moved
    again - the same sediment the guard exists to prevent.
    """
    stale = stale_baseline_entries(ROOT / "tests", ROOT)
    assert not stale, f"delete these from stale_patch_baseline.py: {stale}"
