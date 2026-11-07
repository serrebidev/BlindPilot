"""A name imported by value is a copy, so patching one copy reaches only it.

`module_a` doing `from module_b import fn` holds its own reference. A test that
patches `module_b.fn` changes B and leaves A calling the original, so the test
can pass while testing nothing. This module finds that shape by reading source,
because importing the modules would run them.

The guard is `stale_patches()`. It takes the directories to scan so the tests
can drive it with a synthetic tree instead of the real one.

**It reports candidates, not proven bugs.** Static reading cannot know which
module a test actually exercises, so patching one holder is right whenever that
holder is the module under test: `test_codex_replace_drops_server` patches
`installers.find_backend_cli`, and `installers.update_backend` is the code that
reads it.

So the guard is a **ratchet**, not a clean-slate assertion. `BASELINE` holds the
patches that already existed when it was added; the test fails on a finding that
is not in the baseline, and on a baseline entry that no longer fires. A module
move that arms a test silently therefore fails the build, while the existing
population can only shrink. The baseline is a record of what was there, not a
claim that each entry is safe - checking them is follow-up work, tracked on the
issue.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from stale_patch_baseline import BASELINE

# `monkeypatch.setattr(module, "name", value)` and `mock.patch("module.name")`.
# Only the module expression and the name matter; the value never does.
_SETATTR = re.compile(r"setattr\(\s*(\w+)\s*,\s*[\"']([\w_]+)[\"']")
_PATCH_STRING = re.compile(r"patch\(\s*[\"'](\w+)\.(\w+)[\"']")
# `module.name = value` rebinds it just as surely, and several tests do this to
# save and restore the original. Matched against an AST-unparsed line so a
# comparison (`==`) or an attribute of a local (`self.x = y`) never qualifies.
_ASSIGN = re.compile(r"^\s*(\w+)\.(\w+)\s*=(?!=)", re.MULTILINE)


@dataclass(frozen=True)
class StalePatch:
    """One test that patches a name another module still holds the original of."""

    test_file: str
    patched_module: str
    name: str
    other_holders: tuple[str, ...]

    def __str__(self) -> str:
        holders = ", ".join(self.other_holders)
        return (
            f"{self.test_file} patches {self.patched_module}.{self.name}, "
            f"but {holders} hold(s) its own reference and will not see it"
        )

    @property
    def key(self) -> tuple[str, str, str]:
        """What a `REVIEWED` entry is keyed by."""
        return (self.test_file, self.patched_module, self.name)


def _defined(source: str) -> set[str]:
    """Top-level names this module defines, so the origin counts as a holder.

    Without this the defining module is invisible: patching
    `agent_backends.find_backend_cli` would look harmless even though every
    importer holds its own copy of it.
    """
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _imports(source: str) -> dict[str, str]:
    """Names this module holds by value, mapped to the module they came from."""
    held: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                held[alias.asname or alias.name] = node.module.split(".")[0]
    return held


def _aliases(source: str, modules: set[str]) -> dict[str, str]:
    """Local names bound to a production module, so `app` resolves to its file."""
    bound: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    bound[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in modules:
            bound[node.module] = node.module
    return bound


def stale_patches(tests_dir: Path, prod_dir: Path, *, suppress: bool = True) -> list[StalePatch]:
    """Every test patch that another module's own reference would ignore."""
    modules = {path.stem for path in prod_dir.glob("*.py")}
    sources: dict[str, str] = {}
    for path in prod_dir.glob("*.py"):
        sources[path.stem] = path.read_text(encoding="utf-8")

    # (origin, name) -> every module holding a reference, the origin included.
    holders: dict[tuple[str, str], set[str]] = {}
    for module, text in sources.items():
        for name in _defined(text):
            holders.setdefault((module, name), set()).add(module)
        for name, origin in _imports(text).items():
            if origin in modules:
                holders.setdefault((origin, name), set()).add(module)

    found: list[StalePatch] = []
    for path in tests_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        bound = _aliases(text, modules)
        # Every module this file patches, per name: patching all holders in one
        # file is the fix, so the check has to look at the file, not one patch.
        patched: dict[str, set[str]] = {}
        for module, name in _SETATTR.findall(text):
            patched.setdefault(name, set()).add(bound.get(module, module))
        for module, name in _PATCH_STRING.findall(text):
            patched.setdefault(name, set()).add(module)
        for module, name in _ASSIGN.findall(text):
            if module in modules or module in bound:
                patched.setdefault(name, set()).add(bound.get(module, module))
        for name, modules_patched in sorted(patched.items()):
            needed: set[str] = set()
            for (_origin, exported), copies in holders.items():
                if exported == name and copies & modules_patched:
                    needed |= copies
            missing = needed - modules_patched
            if missing:
                for module in sorted(modules_patched):
                    found.append(StalePatch(path.name, module, name, tuple(sorted(missing))))
    if not suppress:
        return found
    return [item for item in found if item.key not in BASELINE]


def stale_baseline_entries(tests_dir: Path, prod_dir: Path) -> list[tuple[str, str, str]]:
    """Baseline entries that no longer fire, so the list can only shrink."""
    still_there = {item.key for item in stale_patches(tests_dir, prod_dir, suppress=False)}
    return sorted(BASELINE - still_there)
