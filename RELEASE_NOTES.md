# BlindPilot 0.14.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

Nothing you hear changes in this release. What changed is the ground underneath the
code: the types are now checked, and the checker found a contract that was wrong.

## The types are checked, and the checker found a real defect

The type annotations were already in the code — the scattered `# type: ignore` comments
said somebody once meant to run a checker, but nobody was, so nothing held them to it.
Mypy now runs in CI, pinned like ruff. One runner is enough: `mypy.ini` pins
`platform = win32` because this code carries both halves of every platform split behind
runtime checks no checker can read, so unpinned the same tree reports 21 errors on
Windows and 52 on Linux.

There were 21 errors across 15,855 lines. One was a real defect:

**Steer was not part of the worker contract.** The window holds whichever backend's
worker was chosen through a shared Protocol, so it can drive it without knowing which
one it is. That Protocol promised `start`, `is_alive`, `join` and `cancel` — but not
`steer`, which is called directly when you press Steer. All four workers happened to
implement it, so nothing ever broke. A fifth that did not would have type-checked clean
and failed the first time somebody pressed Steer. The call site used to reach for it
with `getattr(worker, "steer")`, which is what hid the gap: written that way, neither a
reader nor a checker could see the Protocol was short. That indirection is gone, `steer`
is in the Protocol, and a test now holds every worker to the whole contract — it fails
without the fix.

Two more errors were latent rather than broken — safe today, and both stopping being
safe after an edit:

- An npm install guarded on the argument list rather than on npm itself, so nothing
  established the value it then passed on. Installing a backend that needs npm now says
  so plainly when npm cannot be installed.
- An opencode history helper called `entry.get("info")` twice, so the guard tested one
  value and the code used another. That shape appeared five times; it is now one named
  helper.

The rest were platform splits behind runtime checks no checker can read, variables
reused for two different lookups, and one pre-existing `# type: ignore` that carried the
wrong error code. Three ignored lines remain, each with the reason written beside it.
82% of expressions are precisely typed even with wxPython untyped, because the third
parties that ship no stubs are covered narrowly rather than by loosening anything else.

Not proposed and worth saying why: `--strict` is 161 errors, 77 of them bare
`dict`/`list`, and pyright — the stronger checker — would need real checks suppressed to
reach zero, because wxPython's bundled stubs declare `wx.GetApp()` non-Optional and
several runtime `is None` guards are genuinely needed.