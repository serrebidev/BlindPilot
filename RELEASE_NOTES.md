# BlindPilot 0.43.10

This release adds opt-in task-based model routing for Command Code tabs, contributed by Farhan Khan. It is off by default: leave Model blank and the tab keeps using the model your Command Code CLI is configured with.

- Choose "Auto (choose for each task)" under Model, or type /model auto, to opt a tab in. Each turn is then routed by the task itself: a short lookup or a small text or label edit runs on the cheapest eligible model, free models included, while anything touching security, accessibility, migration, a release, a deploy, payment, data loss, race conditions, concurrency, multi-agent work, or refactoring runs on a stronger one.
- Prices come from the price catalog bundled with your installed Command Code CLI, so routing follows the prices your CLI actually charges; a hand-picked table is used only when that catalog cannot be read. Models are also filtered by your plan, and a model whose upstream may train on the prompt is never chosen.
- The chosen route is announced when it changes and is shown in Model, Session Status. A hand-picked model and effort still override routing at any time.
- If an Auto turn hits a rate limit or runs out of credits before it has answered or run a tool, BlindPilot announces one retry on a model the installed catalog confirms is free. A turn that has already produced output or run a tool is never replayed.
