"""Fail on a UTF-8 byte-order marker.

A BOM is invisible to ruff and mypy and surfaces only when something parses the
file: `ast.parse` raises "invalid non-printable character U+FEFF", which reads
as a syntax error in whichever tool happened to look, not as an encoding fault.
Windows tools write one readily — `Set-Content -Encoding utf8` and Notepad both
do — so it is cheap to add by accident.

Exits 1 and names each offender, so the pre-commit hook reports the cause rather
than the symptom.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = b"\xef\xbb\xbf"


def main(paths: list[str]) -> int:
    offenders = [path for path in paths if Path(path).read_bytes().startswith(MARKER)]
    for path in offenders:
        print(f"{path}: starts with a UTF-8 byte-order marker")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
