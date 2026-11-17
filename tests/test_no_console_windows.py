"""No child process may open a console window on Windows.

A windowed app owns no console, so Windows gives every console child a new
visible one. For a screen-reader user that is a window taking focus and being
announced mid-turn. Every launch in production code therefore passes the
no-window flag, or is listed here with the reason it need not.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LAUNCHERS = {"run", "Popen", "check_output", "check_call", "call", "owned_popen", "_start_owned"}

# (file, enclosing function): why it may start a process without the flag.
EXEMPT = {
    # POSIX-only: guarded by platform checks or reached only off Windows.
    ("agent_backends.py", "find_backend_cli"): "POSIX login shell",
    ("agent_backends.py", "login_shell_path_dirs"): "POSIX login shell",
    ("claude_session.py", "_login_shell_which"): "POSIX login shell",
    ("install_path.py", "_posix_persistent_path_dirs"): "POSIX login shell",
    ("process_utils.py", "_descendant_pids"): "POSIX ps",
    ("blindpilot_app.py", "_open_path"): "Windows uses os.startfile",
    ("diagnostics.py", "_reveal"): "Windows uses os.startfile",
    ("computer_use_mac.py", "open"): "macOS only",
    ("earcons.py", "_spawn"): "POSIX audio players",
    ("earcons.py", "_loop_unix"): "POSIX audio players",
    # Deliberate: the user is meant to see and use this console.
    ("blindpilot_app.py", "_launch_login_terminal"): "interactive sign-in console",
    ("computer_use.py", "open"): "launches the app the user asked to drive",
    # Wrappers: the caller supplies the flags.
    ("app_updater.py", "_spawn_detached"): "builds its own creationflags in **extra",
    ("agent_backends.py", "_start_owned"): "forwards **kwargs",
    ("blindpilot_app.py", "through_native"): "forwards **kwargs",
}


def _marks_no_window(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "creationflags":
            return True
        if kw.arg is None:
            target = kw.value
            name = ""
            if isinstance(target, ast.Call):
                func = target.func
                name = getattr(func, "attr", None) or getattr(func, "id", "")
            if "no_window" in name or "NO_WINDOW" in name:
                return True
            # ``extra`` or ``kwargs`` built elsewhere is judged where it is built.
    return False


def _launch_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "subprocess" and func.attr in LAUNCHERS:
            return func.attr
    if isinstance(func, ast.Name) and func.id in {"owned_popen", "_start_owned"}:
        return func.id
    return ""


def _unflagged(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else function
            )
            if isinstance(child, ast.Call) and _launch_name(child) and not _marks_no_window(child):
                found.append((inner, child.lineno))
            walk(child, inner)

    walk(tree, "<module>")
    return found


def _scan() -> tuple[list[str], set[tuple[str, str]]]:
    offenders: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in sorted([*ROOT.glob("*.py"), *ROOT.glob("backend_workers/*.py")]):
        rel = path.relative_to(ROOT).as_posix()
        for function, line in _unflagged(path):
            if (path.name, function) in EXEMPT:
                used.add((path.name, function))
            else:
                offenders.append(f"{rel}:{line} in {function}()")
    return offenders, used


def test_exemptions_still_name_a_real_launch():
    _, used = _scan()
    assert set(EXEMPT) <= used, f"Stale exemptions: {sorted(set(EXEMPT) - used)}"


def test_every_child_process_is_started_without_a_console_window():
    offenders, _ = _scan()
    assert not offenders, (
        "These start a process without CREATE_NO_WINDOW (process_utils.no_window_kwargs()); "
        "a console window would take focus from the screen reader:\n" + "\n".join(offenders)
    )
