# BlindPilot 0.43.9

This release fixes the packaged macOS app icon, whose Retina entries did not match the sizes the icon generator declares for them.

- The packaged BlindPilot.icns now carries Retina entries at the exact dimensions tools/make_icon.py specifies for each ICNS type, so the macOS icon renders at the correct resolution instead of being scaled from a mismatched entry.
- A new test checks every entry in the packaged icon against the generator's size table, so a future icon regeneration that drifts from the declared sizes fails the test suite instead of shipping.
