# BlindPilot 0.43.11

This release fixes Command Code effort levels, which could be refused by the model and leave the turn running with no effort at all.

- BlindPilot sent some Command Code turns with the effort "xhigh" translated from the model's own top level. The current Command Code CLI checks the level against the chosen model's own list -- Kimi K3, for example, takes low, high, max -- so those turns were refused with 'Unknown effort "xhigh"' and rerun bare. Auto routing now picks the strongest level from the chosen model's own list, spelled exactly as that model spells it, and the fallback routes do the same.
- If a level is still refused -- say, one picked by hand from an older BlindPilot -- the retry now uses the strongest level from the CLI's own "Supported:" list instead of dropping the effort. Only a model with no adjustable effort at all still retries without one.
- The Model picker's effort list for Command Code again offers every level a model may take: low, medium, high, xhigh, max.
