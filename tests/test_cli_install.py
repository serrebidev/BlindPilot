"""Unit tests for detecting, installing and PATH-registering the Claude Code CLI.

Covers the first-launch path: find an existing install, offer to install the
native build when there isn't one, and make sure the folder it lands in is on
the persistent PATH — the registry on Windows, the login shell's startup file
on macOS and Linux — so `claude` also works in a terminal.

Nothing here writes to the real registry, touches a real shell profile, or runs
the real installer. The macOS / Linux profile-writing tests are plain file I/O,
so they run and are meaningful on every platform.

Run from the project root:

    python -m pytest tests/ -q
    # or, with no pytest installed:
    python tests/test_cli_install.py
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blindpilot_app  # noqa: E402
from blindpilot_app import (  # noqa: E402
    BACKEND_FREEBUFF,
    PATH_STANZA_MARKER,
    _first_login_url,
    _fallback_claude_paths,
    _install_argv,
    _is_on_persistent_path,
    _node_archive_spec,
    _native_bin_dir,
    _path_export_line,
    _path_with_entry,
    _same_dir,
    _shell_profile_file,
    ensure_on_posix_path,
    install_backend,
    install_claude,
)

WINDOWS = platform.system() == "Windows"


class _Patch:
    """Swap module attributes for the duration of a with-block."""

    def __init__(self, **kwargs):
        self._new = kwargs
        self._old = {}

    def __enter__(self):
        for name, value in self._new.items():
            self._old[name] = getattr(blindpilot_app, name)
            setattr(blindpilot_app, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self._old.items():
            setattr(blindpilot_app, name, value)
        return False


class _FakePlatform:
    """Stands in for the `platform` module so the other OS's code path runs."""

    def __init__(self, system):
        self._system = system

    def system(self):
        return self._system


class _FakeShutil:
    """Stands in for `shutil`, with only the tools we say are installed."""

    def __init__(self, available):
        self._available = available

    def which(self, name):
        return self._available.get(os.path.basename(name))


class _PatchPopen:
    """Stand in for subprocess.Popen, restoring the real class afterwards."""

    def __init__(self, factory):
        self._factory = factory

    def __enter__(self):
        self._real = blindpilot_app.subprocess.Popen
        blindpilot_app.subprocess.Popen = self._factory
        return self

    def __exit__(self, *exc):
        blindpilot_app.subprocess.Popen = self._real
        return False


class _PatchRun:
    """Stand in for subprocess.run without replacing its exception types."""

    def __init__(self, function):
        self._function = function

    def __enter__(self):
        self._real = blindpilot_app.subprocess.run
        blindpilot_app.subprocess.run = self._function
        return self

    def __exit__(self, *exc):
        blindpilot_app.subprocess.run = self._real
        return False


# ---- Where we look for an existing install --------------------------------


def test_native_install_dir_is_a_candidate():
    """The native installer's own target must be searched.

    It's the location the docs recommend, so missing it means the wizard
    offers to install a CLI the user already has.
    """
    candidates = _fallback_claude_paths()
    name = "claude.exe" if WINDOWS else "claude"
    assert _native_bin_dir() / name in candidates


def test_windows_candidates_cover_winget_and_npm():
    if not WINDOWS:
        return
    joined = [str(p).lower() for p in _fallback_claude_paths()]
    assert any("winget" in p for p in joined)
    assert any(os.path.join("npm", "claude") in p for p in joined)


# ---- Comparing PATH entries -----------------------------------------------


def test_same_dir_ignores_case_slashes_and_quotes():
    if not WINDOWS:
        return
    assert _same_dir("C:\\Users\\x\\.local\\bin", "C:/Users/x/.local/bin")
    assert _same_dir('"C:\\Tools"', "C:\\tools")
    assert _same_dir("  C:\\Tools\\  ", "C:\\Tools")
    assert not _same_dir("C:\\Tools", "C:\\Tools2")
    assert not _same_dir("", "C:\\Tools")


def test_same_dir_expands_environment_variables():
    """User PATH entries are commonly written as %USERPROFILE%\\....

    Comparing them literally would add a duplicate entry every launch.
    """
    if not WINDOWS:
        return
    os.environ["_CCR_TEST_HOME"] = "C:\\Users\\tester"
    try:
        assert _same_dir("%_CCR_TEST_HOME%\\bin", "C:\\Users\\tester\\bin")
    finally:
        os.environ.pop("_CCR_TEST_HOME", None)


# ---- Building the new PATH value ------------------------------------------


def test_path_entry_is_appended_when_missing():
    if not WINDOWS:
        return
    current = os.pathsep.join(["C:\\Windows", "C:\\Windows\\System32"])
    updated = _path_with_entry(current, "C:\\Users\\x\\.local\\bin")
    assert updated == current + os.pathsep + "C:\\Users\\x\\.local\\bin"


def test_path_entry_not_duplicated():
    if not WINDOWS:
        return
    current = os.pathsep.join(["C:\\Windows", "C:/Users/x/.local/bin/"])
    assert _path_with_entry(current, "C:\\Users\\x\\.local\\bin") is None


def test_path_entry_preserves_variable_references():
    """Other entries must come back byte-for-byte.

    Expanding %USERPROFILE% on the way through would bake one user's home
    directory into a value the registry is supposed to keep dynamic.
    """
    if not WINDOWS:
        return
    current = os.pathsep.join(["%USERPROFILE%\\bin", "%JAVA_HOME%\\bin"])
    updated = _path_with_entry(current, "C:\\new")
    assert updated is not None
    assert updated.split(os.pathsep)[:2] == ["%USERPROFILE%\\bin", "%JAVA_HOME%\\bin"]


def test_path_entry_drops_empty_segments():
    """A trailing semicolon is common and must not become a blank entry."""
    if not WINDOWS:
        return
    updated = _path_with_entry("C:\\Windows" + os.pathsep + os.pathsep, "C:\\new")
    assert updated == "C:\\Windows" + os.pathsep + "C:\\new"


def test_path_entry_into_empty_path():
    assert _path_with_entry("", "C:\\new") == "C:\\new"


# ---- Is it reachable from a terminal? -------------------------------------


def test_persistent_path_check_reads_the_registry_view():
    if not WINDOWS:
        return
    with _Patch(_windows_persistent_path_dirs=lambda: ["C:\\Windows", "%FOO%\\bin"]):
        assert _is_on_persistent_path(Path("C:/windows"))
        assert not _is_on_persistent_path(Path("C:/Users/x/.local/bin"))


def test_persistent_path_check_survives_a_registry_failure():
    """A read we can't do must not nag the user about a PATH that is fine."""
    if not WINDOWS:
        return

    def boom():
        raise OSError("no registry")

    with _Patch(_windows_persistent_path_dirs=boom):
        assert _is_on_persistent_path(Path("C:/anywhere"))


def test_posix_persistent_path_check_asks_the_login_shell():
    with _Patch(
        platform=_FakePlatform("Darwin"),
        _posix_persistent_path_dirs=lambda: ["/usr/bin", "/Users/x/.local/bin"],
    ):
        assert _is_on_persistent_path(Path("/Users/x/.local/bin"))
        assert not _is_on_persistent_path(Path("/opt/homebrew/bin"))


def _real_posix_shell():
    """A genuine POSIX shell to run the snippet in, if this box has one.

    Git Bash counts on Windows, which is what lets the macOS / Linux code path
    be exercised from a Windows dev machine.
    """
    import shutil as real_shutil

    for name in ("bash", "sh"):
        found = real_shutil.which(name)
        if found:
            return found
    for path in (r"C:\Program Files\Git\bin\bash.exe",):
        if os.path.isfile(path):
            return path
    return None


def test_login_shell_path_split_keeps_entries_containing_spaces():
    """`/Applications/Some App/bin` on PATH is normal on macOS.

    Splitting PATH by word rather than by colon would shred it into fragments
    and make us wrongly report that claude's folder is not on PATH.
    """
    shell = "/bin/sh"
    spaced = "/opt/Some App/bin"
    called = {}

    def run(argv, **kwargs):
        called["argv"] = argv
        called["kwargs"] = kwargs

        class Result:
            returncode = 0
            stdout = f"/usr/bin\n{spaced}\n/opt/x\n"

        return Result()

    with _PatchRun(run):
        with _Patch(platform=_FakePlatform("Darwin"), _login_shell=lambda: shell):
            dirs = blindpilot_app._posix_persistent_path_dirs()

    assert spaced in dirs, dirs
    assert not any(d == "/opt/Some" for d in dirs)
    assert called["argv"][1:3] == ["-l", "-c"]
    assert 'tr ":" "\\n"' in called["argv"][3]
    assert called["kwargs"]["text"] is True


def test_posix_check_stays_quiet_without_a_usable_login_shell():
    """No shell to ask means no evidence of a problem — don't invent one."""
    with _Patch(platform=_FakePlatform("Darwin"), _posix_persistent_path_dirs=list):
        assert _is_on_persistent_path(Path("/anywhere"))


# ---- macOS / Linux: the shell startup file --------------------------------


def test_profile_file_matches_the_login_shell():
    home = Path.home()
    cases = [
        ("Darwin", "/bin/zsh", home / ".zshrc"),
        ("Darwin", "/bin/bash", home / ".bash_profile"),
        ("Linux", "/bin/bash", home / ".bashrc"),
        ("Darwin", "/usr/local/bin/fish", home / ".config" / "fish" / "config.fish"),
        ("Darwin", "/bin/ksh", home / ".profile"),
    ]
    for system, shell, expected in cases:
        with _Patch(platform=_FakePlatform(system), _login_shell=lambda s=shell: s):
            assert _shell_profile_file() == expected, (system, shell)


def test_profile_file_without_a_login_shell():
    with _Patch(platform=_FakePlatform("Darwin"), _login_shell=lambda: None):
        assert _shell_profile_file() == Path.home() / ".profile"


def test_export_line_uses_home_relative_form():
    """A profile that hardcodes /Users/someone breaks when synced elsewhere."""
    directory = Path.home() / ".local" / "bin"
    line = _path_export_line(directory, "zsh")
    assert line.startswith('export PATH="$HOME')
    assert line.endswith(':$PATH"')
    assert str(Path.home()) not in line


def test_export_line_keeps_paths_outside_home_literal():
    line = _path_export_line(Path("/opt/claude/bin"), "bash")
    assert line == 'export PATH="/opt/claude/bin:$PATH"'


def test_export_line_uses_fish_syntax_for_fish():
    line = _path_export_line(Path("/opt/claude/bin"), "fish")
    assert line == 'fish_add_path "/opt/claude/bin"'


class _Profile:
    """A throwaway shell startup file wired into the module under test."""

    def __init__(self, shell="/bin/zsh", on_path=False, initial=None):
        self._tmp = tempfile.TemporaryDirectory()
        self._shell = shell
        self._on_path = on_path
        self._initial = initial

    def __enter__(self):
        self.path = Path(self._tmp.name) / ".zshrc"
        if self._initial is not None:
            self.path.write_text(self._initial, encoding="utf-8")
        self._patch = _Patch(
            platform=_FakePlatform("Darwin"),
            _login_shell=lambda: self._shell,
            _shell_profile_file=lambda: self.path,
            _is_on_persistent_path=lambda _d: self._on_path,
        )
        self._patch.__enter__()
        return self

    def __exit__(self, *exc):
        self._patch.__exit__(*exc)
        self._tmp.cleanup()
        return False

    def text(self):
        return self.path.read_text(encoding="utf-8")


def test_posix_path_write_appends_a_marked_stanza():
    with _Profile() as prof:
        changed = ensure_on_posix_path(Path.home() / ".local" / "bin")
        assert changed == str(prof.path)
        body = prof.text()
        assert PATH_STANZA_MARKER in body
        assert 'export PATH="$HOME' in body


def test_posix_path_write_does_not_join_the_last_line():
    """Profiles often lack a trailing newline; appending blind corrupts them."""
    with _Profile(initial="alias ll='ls -l'") as prof:
        ensure_on_posix_path(Path.home() / ".local" / "bin")
        lines = prof.text().splitlines()
        assert lines[0] == "alias ll='ls -l'"
        assert PATH_STANZA_MARKER in lines


def test_posix_path_write_preserves_what_was_there():
    original = "export EDITOR=vim\nsource ~/.aliases\n"
    with _Profile(initial=original) as prof:
        ensure_on_posix_path(Path.home() / ".local" / "bin")
        assert prof.text().startswith(original)


def test_posix_path_write_is_idempotent():
    with _Profile() as prof:
        first = ensure_on_posix_path(Path.home() / ".local" / "bin")
        assert first is not None
        after_first = prof.text()
        # A second run (a repeat install, say) must not stack up stanzas.
        assert ensure_on_posix_path(Path.home() / ".local" / "bin") is None
        assert prof.text() == after_first
        assert after_first.count(PATH_STANZA_MARKER) == 1


def test_posix_path_write_skipped_when_already_reachable():
    with _Profile(on_path=True) as prof:
        assert ensure_on_posix_path(Path.home() / ".local" / "bin") is None
        assert not prof.path.exists()


def test_posix_path_write_creates_missing_parent_directories():
    """fish's config lives in ~/.config/fish, which may not exist yet."""
    with _Profile(shell="/usr/local/bin/fish") as prof:
        prof.path = prof.path.parent / ".config" / "fish" / "config.fish"
        with _Patch(_shell_profile_file=lambda: prof.path):
            assert ensure_on_posix_path(Path("/opt/claude/bin")) == str(prof.path)
        assert 'fish_add_path "/opt/claude/bin"' in prof.text()


# ---- Which installer runs where -------------------------------------------


def test_install_command_is_the_official_powershell_one_liner():
    with _Patch(platform=_FakePlatform("Windows"), _powershell_exe=lambda: "powershell.exe"):
        argv = _install_argv()
    assert argv is not None
    assert argv[0] == "powershell.exe"
    assert "irm https://claude.ai/install.ps1 | iex" in argv[-1]


def test_install_command_is_the_official_shell_one_liner_on_macos():
    with _Patch(
        platform=_FakePlatform("Darwin"),
        shutil=_FakeShutil({"curl": "/usr/bin/curl", "bash": "/bin/bash"}),
    ):
        argv = _install_argv()
    assert argv == ["/bin/bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]


def test_install_command_absent_without_curl():
    with _Patch(platform=_FakePlatform("Darwin"), shutil=_FakeShutil({"bash": "/bin/bash"})):
        assert _install_argv() is None


def test_install_command_absent_without_powershell():
    with _Patch(platform=_FakePlatform("Windows"), _powershell_exe=lambda: None):
        assert _install_argv() is None


# ---- Running the installer ------------------------------------------------


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self):
        return self._rc


def test_install_reports_missing_prerequisites():
    log = []
    with _Patch(_install_argv=lambda: None):
        assert install_claude(log.append) is None
    assert any("cannot be run automatically" in line for line in log)


def test_install_succeeds_and_registers_path():
    log = []
    installed = str(_native_bin_dir() / "claude.exe")
    added = []

    with (
        _Patch(
            _install_argv=lambda: ["installer"],
            _find_claude=lambda: installed,
            ensure_on_path=lambda d: added.append(d) or "your user PATH",
        ),
        _PatchPopen(lambda *_a, **_k: _FakeProc(["Downloading\n"])),
    ):
        result = install_claude(log.append)

    assert result == installed
    assert added == [Path(installed).parent]
    assert any("Downloading" in line for line in log)
    assert any("PATH" in line for line in log)


def test_install_on_macos_reports_the_profile_it_edited():
    """The user should be told which of their files was changed, by name."""
    installed = "/Users/x/.local/bin/claude"
    log = []
    with (
        _Patch(
            platform=_FakePlatform("Darwin"),
            _install_argv=lambda: ["/bin/bash", "-c", "curl ... | bash"],
            _find_claude=lambda: installed,
            ensure_on_path=lambda _d: "/Users/x/.zshrc",
        ),
        _PatchPopen(lambda *_a, **_k: _FakeProc([])),
    ):
        assert install_claude(log.append) == installed
    joined = "\n".join(log)
    assert "/Users/x/.zshrc" in joined
    assert "Terminal" in joined and "PowerShell" not in joined


def test_install_trusts_the_binary_over_the_exit_code():
    """Installers exit non-zero for cosmetic reasons; a working binary wins."""
    installed = str(_native_bin_dir() / "claude.exe")
    with (
        _Patch(
            _install_argv=lambda: ["installer"],
            _find_claude=lambda: installed,
            ensure_on_path=lambda _d: None,
        ),
        _PatchPopen(lambda *_a, **_k: _FakeProc([], rc=1)),
    ):
        assert install_claude(lambda _t: None) == installed


def test_install_fails_when_no_binary_appears():
    log = []
    with (
        _Patch(_install_argv=lambda: ["installer"], _find_claude=lambda: None),
        _PatchPopen(lambda *_a, **_k: _FakeProc([], rc=0)),
    ):
        assert install_claude(log.append) is None
    assert any("not found afterwards" in line for line in log)


def test_install_survives_a_launch_failure():
    log = []

    def refuse(*_a, **_k):
        raise OSError("access denied")

    with _Patch(_install_argv=lambda: ["installer"]), _PatchPopen(refuse):
        assert install_claude(log.append) is None
    assert any("could not be started" in line for line in log)


def test_install_survives_a_path_write_failure():
    """A read-only profile must not turn a good install into a reported failure."""
    installed = str(_native_bin_dir() / "claude.exe")
    log = []

    def refuse(_d):
        raise OSError("permission denied")

    with (
        _Patch(
            _install_argv=lambda: ["installer"],
            _find_claude=lambda: installed,
            ensure_on_path=refuse,
        ),
        _PatchPopen(lambda *_a, **_k: _FakeProc([])),
    ):
        assert install_claude(log.append) == installed
    assert any("adding it to PATH failed" in line for line in log)


# ---- Clean-machine npm backend installation -------------------------------


def test_node_archive_matches_each_supported_platform():
    assert _node_archive_spec("v24.1.0", "Windows", "AMD64") == (
        "node-v24.1.0-win-x64.zip",
        "node-v24.1.0-win-x64",
    )
    assert _node_archive_spec("v24.1.0", "Darwin", "arm64") == (
        "node-v24.1.0-darwin-arm64.tar.gz",
        "node-v24.1.0-darwin-arm64",
    )
    assert _node_archive_spec("v24.1.0", "Linux", "aarch64") == (
        "node-v24.1.0-linux-arm64.tar.gz",
        "node-v24.1.0-linux-arm64",
    )
    assert _node_archive_spec("v24.1.0", "Windows", "x86") is None


def test_freebuff_login_url_is_recovered_from_hidden_cli_output():
    text = "Open this URL: https://freebuff.com/login?token=abc123."
    assert _first_login_url(text) == "https://freebuff.com/login?token=abc123"
    assert _first_login_url("Waiting for login") == ""


def test_clean_freebuff_install_bootstraps_node_registers_path_and_verifies():
    state = {"npm": None}
    log: list[str] = []
    runs: list[list[str]] = []
    paths: list[Path] = []
    installed = str(Path("C:/BlindPilot/npm/freebuff.cmd"))

    def install_node(_log):
        state["npm"] = str(Path("C:/BlindPilot/node/npm.cmd"))
        return state["npm"]

    with _Patch(
        _find_npm=lambda: state["npm"],
        install_portable_node=install_node,
        _managed_backend_binary=lambda _backend: installed,
        _run_logged_process=lambda argv, _log, env=None: runs.append(list(argv)) or 0,
        _add_to_process_path=lambda _path: None,
        ensure_on_path=lambda path: paths.append(path) or "your user PATH",
    ):
        result = install_backend(BACKEND_FREEBUFF, log.append)

    assert result == installed
    assert runs[0][-1] == "freebuff"
    assert runs[1] == [installed, "--version"]
    assert paths == [Path(installed).parent]
    assert any("verified" in line.casefold() for line in log)


def test_backend_install_rejects_a_cli_that_cannot_start():
    state = {"npm": str(Path("C:/BlindPilot/node/npm.cmd"))}
    installed = str(Path("C:/BlindPilot/npm/freebuff.cmd"))
    return_codes = iter((0, 1))

    with _Patch(
        _find_npm=lambda: state["npm"],
        _managed_backend_binary=lambda _backend: installed,
        _run_logged_process=lambda *_args, **_kwargs: next(return_codes),
        _add_to_process_path=lambda _path: None,
        ensure_on_path=lambda _path: None,
    ):
        assert install_backend(BACKEND_FREEBUFF, lambda _line: None) is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
