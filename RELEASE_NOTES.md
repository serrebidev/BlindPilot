# BlindPilot 0.29.30

This release audits the last two backends the way Muse Code was audited in 0.29.29: measured against the live CLIs, gaps closed, each fix proven with a real turn. Command Code had drifted away from its CLI upstream; opencode was dropping transcript content and starving the model of attachments.

- Command Code effort levels that work: upstream narrowed the vocabulary to low, medium, and xhigh, so the picker no longer offers high and max, which failed the turn outright. And when a level goes to a model that takes no effort at all, the turn runs again without it and says so, instead of dying on the refusal. Only a run that produced nothing is ever rerun.
- Command Code, re-verified: the newest withheld tool is named with its reason, and the permission modes, model catalog, and event stream were all re-measured at CLI 1.69.0 with a live turn to prove the worker.
- opencode attachments that arrive: pictures and text files now travel as file parts the model reads -- confirmed live, with the model naming and reading what was sent -- instead of bare paths. Anything else is still named by path, steering leaves attachments with the original prompt, and reopened conversations name their files back.
- opencode turns that report: file changes and delegated subagent runs used to stream past in silence. They are now announced once each, files the turn produces are named, and a part kind from a newer opencode gets a generic line instead of being swallowed.
