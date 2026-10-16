# BlindPilot 0.47.6

This release tightens Command Code Auto's model routing.

- Auto routing and the free fallback now filter the priced catalog by the Command Code CLI's current --list-models before choosing a model, so a renamed or retired model is never routed to. Stop cancels an in-progress model-list check on every route, retry, and fallback path. Thanks to Farhan Khan for the fix.
