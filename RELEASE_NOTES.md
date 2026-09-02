# BlindPilot 0.13.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release changes nothing you can hear, and that is the point of it. Behaviour is
exactly as in 0.12.0. What changed is the ground underneath the build and the evidence
behind the narration choice.

## Every dependency is bounded, not half of them

The build file has always pinned its own three tools to the next major version. The
application's requirements bounded four of nine — and two of the other five had already
drifted a whole major version under a floor that allowed it:

- pywinpty 2 → 3: this drives the pseudo-terminal FreeBuff runs in.
- markdown-it-py 3 → 4: this segments every answer into the rows BlindPilot navigates.

A packaged release resolves dependencies fresh on the build machine, so a major version
arriving unannounced meant a broken release — and the first anybody would know was a
download that did not work.

Every dependency now carries an upper bound, each one being the next major above a
version the application has actually been run against. Raising one is now a deliberate
act, with the test suite to back it, rather than something that happens on its own
between two releases. Two of the bounds — for the macOS and non-Windows entries — could
not be exercised on the Windows development machine; CI covers both platforms before a
release ships.

## The fan-out claim is measured, not asserted

**Keep up** narration exists because of a claim: that a fan-out stops flooding the
screen reader. The tests that arrived with the mode checked the rule — this kind of line
is spoken, that kind is not — but never the claim, and the claim is the reason the mode
exists. Now it is measured: five agents at eight steps each is 85 spoken lines in
*Follow everything*, and one in *Keep up*. At a couple of seconds apiece, 85 lines is
minutes of backlog in a queue BlindPilot cannot see into, cannot shorten and cannot pop
from.

Two more tests hold the mode honest where it could go wrong: quieter is only worth
having if the answer survives it, and skipping a line must not mean losing it — every
tool call and result is still a row, still under the review cursor.

The tests drive the narration pipeline directly rather than through a screen-reader
bridge, so they run for anybody, in CI on all three platforms, rather than only on one
machine. The bridge remains a tool for verifying speech changes by hand, which is what
it is good at.