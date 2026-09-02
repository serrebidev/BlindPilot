# BlindPilot 0.20.1

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This is a point release with one change, and the change is to a test — but it is the
kind of test defect that quietly corrupts every number a contributor reports, so it is
worth its own release note.

## The sweep that counted somebody else's site-packages

One test in this repository compiles every Python module in the tree, so that no
`SyntaxWarning` — an invalid escape sequence written without its `r`, which Python
compiles with a warning and carries on — can reach a release and fail it there. To do
that it has to decide what counts as source, and it decided with a list of exact
directory names: `.venv`, `venv`, `build`, `dist`, `__pycache__`, `.test-tmp`.

A contributor whose virtualenv was named `.venv-win` and whose staging directory was
`dist_new` therefore had 4,652 files of installed wxPython swept in and compiled as
parametrised test cases. Every suite-wide total their reports quoted was measuring
their own site-packages alongside the repository: "5682 passed" against a project with
1,001 tests. Nothing that rested on a named, targeted run was wrong, and nothing in
BlindPilot was wrong — but the number at the top of every check list was, and it took
a reviewer re-deriving the count to find out.

The exclusion now matches a prefix on directory components, judged relative to the
swept root rather than the absolute path. Three consequences, each of which is a
defect the old form had: a virtualenv called anything at all is excluded, not only the
half-dozen spellings somebody thought to list; a file whose own name happens to start
with dist is still source, because the filter reads directories and not file names; and
a checkout that happens to live under a path containing one of these words — pytest's
own basetemp among them, which is `.test-tmp` here and is how the new test exercises
the behaviour — is not thrown away wholesale.

The layout that exposed the defect is now a test of its own: `.venv`, `.venv-win`,
`venv`, `build`, `dist`, `dist_new`, one file named `distance_util.py` that must
survive, and one real module that must survive. Written failing-first, like everything
else here.

Diagnosed by michaldziwisz, in the conversation around the Hermes backend — the second
time in one PR that a stand-in that did not match the real thing hid a defect, and the
first time the stand-in was this repository's own.

## Verification

pytest 999 passed, 3 skipped; ruff check, ruff format --check, mypy, `--startup-smoke`
and `--startup-gui-smoke` all clean at the release commit.
