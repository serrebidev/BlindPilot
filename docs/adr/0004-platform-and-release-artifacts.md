# ADR 0004: Ship Linux and a universal2 macOS build

- **Status:** Accepted
- **Date:** 2026-11-06
- **Context:** The code supported Linux — `linux_accessibility.py` announces
  through AT-SPI/GTK for Orca, the pseudo-terminal and POSIX process groups
  are implemented, and CI ran the suite on `ubuntu-24.04` because it said
  "Linux ships". The release matrix published only Windows and an arm64-only
  macOS bundle, so Linux users had source-only instructions and the macOS
  bundle declared `LSMinimumSystemVersion 10.15`, which Apple silicon cannot
  run. Lint targeted Python 3.10 while mypy, CI and the runtime were 3.12.

## Decisions

The release builds three artifacts from one PyInstaller spec:

- **Windows x64** — unchanged: the installer and the portable zip.
- **Linux x64** — a `tar.gz` of the one-directory build, made on
  `ubuntu-24.04` with the distribution's wxPython wheel, after both packaged
  startup checks under xvfb.
- **macOS universal2** — one bundle for Apple silicon and Intel. The workflow
  checks with `lipo` that the build Python carries both slices before
  PyInstaller runs; the minimum system version is **11.0**, because Apple
  silicon cannot run below it and the universal2 wxPython wheel targets 11+.

The Linux tarball is not an AppImage: FUSE availability and the extra packaging
layer buy nothing for an app whose runtime dependency is GTK, which the
distribution already provides.

Lint targets **3.12**, the version mypy, CI and the runtime already use.

## Considered Options

- **Linux source-only, no artifact.** Rejected: the code already invests in
  Linux accessibility and CI, and the README already says the app runs on
  Linux; leaving the release empty makes that a claim with no download.
- **Linux AppImage.** Rejected: FUSE and bundling complexity for no gain over a
  tarball that uses the system GTK.
- **macOS arm64-only with a corrected minimum.** Rejected by the maintainer in
  favour of one universal artifact.
- **Two macOS artifacts (arm64 and x64).** Rejected: two downloads and two
  code paths to test for the same result.

## Consequences

The Linux and universal2 builds are proven by the release workflow, not by the
development machine; a failed universal2 build fails at the `lipo` check rather
than inside PyInstaller. The workflow can be dispatched by hand to exercise the
build matrix without publishing, because the release job only runs for a `v*`
tag. The Linux artifact depends on the user's GTK 3 and AT-SPI installation —
they are not bundled — and the README says so. The macOS bundle is ad-hoc
signed, not notarized, as before.
