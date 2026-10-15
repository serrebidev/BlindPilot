# BlindPilot 0.47.4

This release fixes Command Code Auto routing and makes history open faster.

- Command Code Auto no longer picks Ling 3.1 Flash's free tier. That free offer has been retired on the server, but the installed Command Code CLI can still list it as free in its bundled catalog, so Auto could choose a model that cannot actually run. Laguna S 2.1 remains available for quick tasks and for safe retries while a rate or credit limit cools down. Thanks to Farhan Khan for the fix.
- Opening history is faster when several backends have stored conversations. BlindPilot now scans the providers in parallel instead of one after another, and a provider whose store cannot be read is skipped the same way as before.
