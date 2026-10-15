# BlindPilot 0.47.5

This release improves Command Code Auto's free fallback routing.

- When a Command Code model refuses a turn or cools down, Auto's free fallback now only picks free models the router already considers fit for work, instead of any free model in the catalog, and it never picks a model whose provider trains on your data. The account plan can also be set through the COMMANDCODE_PLAN environment variable when it can't be read from the CLI. A new catalog coverage check flags models in the router's shortlists that the installed CLI no longer offers, so a renamed or retired model gets spotted instead of silently weakening a tier. Thanks to Farhan Khan for the fix.
