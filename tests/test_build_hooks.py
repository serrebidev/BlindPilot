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
