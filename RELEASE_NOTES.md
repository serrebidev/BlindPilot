# BlindPilot 0.20.4

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release repairs what the last one built, using the evidence only a real run can
give: the wizard's Install Hermes was driven end to end on a Windows machine, the
installer did everything right, and BlindPilot then could not find what it had just
installed. It can now.

## A successful install, reported as a failure

The end-to-end run of 0.20.3's install path: the official Hermes installer ran,
installed its managed uv, cloned its source tree, built a Python 3.11 virtual
environment, resolved 104 packages, wrote its configuration, synced 58 skills, printed
"[OK] hermes command ready" — and BlindPilot's next line said `hermes` was not found
afterwards. Exit code 0 from the installer, failure from the installer's driver. The
install was complete; the discovery was wrong.

Three facts, from the installer's own captured output:

**The launcher is in `%LOCALAPPDATA%\hermes\bin`.** Beside the managed uv the
installer puts there — a directory the old discovery had never heard of. Its known
locations were reasonable guesses; this one is measured.

**`HERMES_HOME` is set persistently, and that is not enough.** A shell started after
the installer sees the variable. A process that was already running when the installer
finished — this application, during a wizard-driven install — carries the environment
it started with, so the default install location must be found on disk rather than
trusted from the environment. The source tree under `%LOCALAPPDATA%\hermes` is now
checked directly.

**The venv launcher is `Scripts\hermes.exe` on Windows.** The fallback candidate had
been `venv\bin\hermes` — the POSIX layout — on every platform, so even a venv it knew
the home of would have looked launcher-less on Windows.

Each fact is pinned by a test written against the captured output, and the whole chain
was verified against the real install this machine now carries: launcher, interpreter,
source root and `hermes_installed()` all answer correctly, with no environment
inherited from the installer.

## Verification

pytest 1022 passed, 3 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit. Four new tests, plus the
end-to-end run that found the defect: the installer's complete captured output against
the discovery code, on the machine it installed to.
