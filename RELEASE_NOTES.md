# BlindPilot 0.16.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release fixes a quiet bug in how FreeBuff finishes reading an answer: a word could
be cut at the wrong letter, silently, with no reason given. It was found by property
testing, not by reading — pointed at the arithmetic, a falsifying case showed up in
seconds.

## Keep the letters and their places in step

FreeBuff reads its answer off a terminal, so working out which part was never spoken
means comparing what was drawn against what was written, letter by letter. `_keyed`
reduces both to a comparable form and records where each kept character came from. The
two ran out of step.

`casefold()` is not length-preserving. German `ß` folds to `ss`, Turkish `İ` to two
characters, the `fi` ligature to two — but the position list got one entry per character
of the input. `"Straße"`, six characters, produced a seven-character key and only seven
positions including the sentinel, so `positions[len(key)]` indexed past the end.

Two failures came out of it:

- **An answer read out in full:** `IndexError` — at least it was loud.
- **An answer read out in part:** cut at the wrong letter, silently.
  `_unspoken_tail("Grüße", "Grüße gehen raus")` returned `"ehen raus"`. A German user
  hears a word with its first letter missing and is given no reason.

One position per character of the key fixes both. The sentinel that keeps a fully-spoken
answer indexable stays.

## How it was found

Property testing, not reading. Hypothesis had already been run at the segmenter and
found nothing across 18,000 examples — that code is solid. Pointed at this arithmetic it
produced a falsifying case in seconds.

The inputs are ordinary words. What nobody writing examples by hand does is put
`"Straße"` in one.

The properties are kept only for this file — a few hundred examples, about two seconds —
because it is the arithmetic that earned them and the next edit deserves the same
treatment. Hypothesis is not spread further than the place that paid for it. Example-based
tests written from what the property test produced sit alongside, so the specific
regressions are named and readable.