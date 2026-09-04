# BlindPilot 0.20.8

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release makes the Hermes backend honour the two halves of the conversation it
was still missing. A Hermes command such as /usage is now run and its report read
back, instead of being sent to the model as a string of characters; and a turn that
stops to ask a question — which Hermes does with a clarify request, a sudo request,
or a secret request — now puts that question in front of the person deciding and
answers it, instead of announcing the event and then falling silent.

## Commands that run

A leading slash means nothing to the model endpoint. BlindPilot sends a prompt to
`prompt.submit`, which does not interpret a slash, while the gateway answers its own
commands through a separate `slash.exec`. So until now every Hermes command the
application did not implement itself reached the model as the five or six characters
it spells: /usage came back as a sentence about usage, /title as a sentence about
titles. The command picker gains Hermes' own commands, and the worker decides what is
a command by asking Hermes itself rather than matching against a list compiled into
the application — Hermes ships about a hundred and twenty, plus whatever skills,
bundles and plugins are installed, and a frozen list would be wrong the first time a
skill was added. Anything Hermes does not recognise is still sent to the model as an
ordinary message, which is the safer of the two wrong answers, so a sentence that
merely opens with a slash is never swallowed. A command that finishes without
printing anything still ends the turn out loud, because a turn that says nothing is
indistinguishable from one that failed.

## Questions that get answered

Hermes' gateway protocol has three requests that stop the agent until an answer
arrives — `clarify` for a question with choices, `sudo` for a password, and `secret`
for a credential — and with `clarify_timeout` at zero they wait with no deadline at
all. The worker was documented as having no such request, so it announced the event
and sent nothing back: the window read "Hermes is asking: a question needing the
terminal" — the fallback wording, reached because a batch clarify carries `questions`
and the worker only ever looked for `question` — and then went quiet for good. The
first question a turn asked ended it.

Now the question, its choices, and whether several answers are wanted all reach the
person deciding, and the answer goes back to Hermes. A batch is answered one id at a
time — Hermes releases it only once every question has been locked, so a question the
person skips is still answered with an empty string rather than leaving the turn
hanging exactly as it did before. A password or secret is answered with its value but
never echoed into the transcript, which is read aloud, copied, and saved.

## Verification

Twenty-five new tests, failing-first, pin the behaviour: both clarify shapes, batch
locking, multi-select as a JSON array, passwords and secrets under the right key and
never echoed, command recognition case-insensitively and against the gateway's own
list, the fallback to an ordinary message when the lookup is refused, and a command
that finishes silently still ending the turn. The full suite is green under `-W
error`, and ruff, mypy and the startup smoke tests are clean.
