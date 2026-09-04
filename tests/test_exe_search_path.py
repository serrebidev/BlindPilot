"""Where a provider CLI is allowed to look for the programs it runs.

Every CLI is started with `cwd` set to the user's project folder, and CLIs
shell out constantly — `git`, `node`, `npm`, `sh`. On Windows the executable
search has historically included the current directory, so a file called
`git.exe` sitting in a project folder can be what runs when something asks for
`git`.

That matters here because the project folder is frequently not the user's own
work: the ordinary use of this application is to clone a repository and ask an
agent to look at it. The agent then runs commands inside it.

Windows documents the way out — `NoDefaultCurrentDirectoryInExePath`, read by
`NeedCurrentDirectoryForExePathW`, which `CreateProcess` and `cmd.exe` consult.
Setting it in the environment every CLI is launched with covers the CLI and
everything the CLI goes on to spawn, since the variable is inherited.

This is hardening rather than a fixed exploit: it removes a way for a hostile
repository to get a program run, and it costs a dictionary entry.
"""

from __future__ import annotations

import os

import agent_backends


def _windows(monkeypatch):
    monkeypatch.setattr(agent_backends.platform, "system", lambda: "Windows")


def _linux(monkeypatch):
    monkeypatch.setattr(agent_backends.platform, "system", lambda: "Linux")


def test_a_cli_does_not_search_the_project_folder_for_programs(monkeypatch):
    _windows(monkeypatch)
    # The developer's own machine may have this set in the real environment;
    # the test must establish it from the function, not inherit it.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)

    env = agent_backends.subprocess_env(r"C:\tools\claude.exe")

    assert env.get("NoDefaultCurrentDirectoryInExePath") == "1"


def test_it_is_not_set_where_it_means_nothing(monkeypatch):
    """POSIX never searched the working directory, so the variable would only
    be a puzzle for whoever read the environment next."""
    _linux(monkeypatch)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)

    env = agent_backends.subprocess_env("/usr/local/bin/claude")

    assert "NoDefaultCurrentDirectoryInExePath" not in env


def test_the_cli_can_still_find_its_own_siblings():
    """The reason this environment exists in the first place: an npm shim has
    to find the `node` next to it. Hardening must not break that.

    Built with `os.path.join` rather than written out: `os.path.dirname` uses
    the running platform's separator, so a path written with backslashes is
    not a directory at all on the two runners that are not Windows.
    """
    directory = os.path.join(os.sep + "tools", "npm")

    env = agent_backends.subprocess_env(os.path.join(directory, "claude.cmd"))

    assert env["PATH"].split(os.pathsep)[0] == directory


def test_the_rest_of_the_environment_is_still_inherited(monkeypatch):
    _windows(monkeypatch)
    monkeypatch.setenv("SOME_TOKEN_THE_CLI_NEEDS", "kept")

    env = agent_backends.subprocess_env(r"C:\tools\claude.exe")

    assert env["SOME_TOKEN_THE_CLI_NEEDS"] == "kept"
