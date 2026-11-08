"""Optional compiled modules must have importable names in the bundle."""

from importlib.util import find_spec
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("package", ["chardet", "pycparser"])
def test_build_hook_collects_only_existing_modules(package):
    pytest.importorskip("PyInstaller")
    pytest.importorskip(package)
    hook = Path(__file__).resolve().parents[1] / "hooks" / f"hook-{package}.py"
    imports = runpy.run_path(str(hook))["hiddenimports"]
    assert all(find_spec(name) is not None for name in imports)
    if package == "chardet":
        assert "chardet" in imports


def test_backend_workers_hook_collects_registry_modules():
    from backend_workers.factory import _WORKERS

    hook = Path(__file__).resolve().parents[1] / "hooks" / "hook-backend_workers.py"
    imports = runpy.run_path(str(hook))["hiddenimports"]
    assert {spec.module for spec in _WORKERS.values()} <= set(imports)
    assert all(find_spec(name) is not None for name in imports)
