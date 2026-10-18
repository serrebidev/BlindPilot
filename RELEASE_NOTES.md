# BlindPilot 0.47.13

This release teaches the Command Code autorouter from experience.

- Per-model outcome feedback over rolling weeks: a model that fails on provider infrastructure twice in a week is withheld from routing until one success on it clears the record. Outcomes are recorded automatically from each turn (success on completion, infra failure on a capacity stop), and `model_leaderboard()` / `leaderboard()` expose which models are on the board week by week.
- OpenRouter's public Command Code usage leaderboard is integrated as a routing input, with cached fetch and parse helpers, so a page change degrades to "no data" rather than an error.
- Adds the AGENTS.md contributor guide. Thanks to Farhan Khan for the original work in #97, ported onto current main in #98.
