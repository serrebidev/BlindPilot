# BlindPilot contributor guide

Screen-reader-first desktop app that runs AI coding CLIs in normal windows.
Accessibility is core behavior, not an extra: keyboard-only operation and NVDA,
JAWS, and VoiceOver speech are part of the product.

## Inner loop

Fast check after each edit:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -q -W error
```

## Gate

Before a commit or PR, every command above must exit 0. `-W error` is
intentional: warnings are failures.

## Notes

- Do not edit without an explicit ask: `BlindPilot.spec` (release workflow
  builds from it), `.release-setup/`, `artifacts/`, `docs/visual-audit/sandbox/`,
  and `docs/visual-audit/shots/` (audit evidence, gitignored by design).
- `pytest.ini`, `ruff.toml`, and `mypy.ini` hold the local configuration. Do not
  loosen a check to make it pass.
- An accessibility change is not complete until it is verified with a real
  screen reader, not only a successful build or a UI-tree dump.
