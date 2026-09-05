# BlindPilot 0.21.6

Hermes' permission modes now do what they say, plus the first visual pass from a sighted contributor.

## Hermes approvals and bypass

Two defects kept commands from running, and both were only found by reading the live gateway's own source rather than trusting the protocol we had written against.

- The approval reply was sent with a key and values the gateway does not read. BlindPilot answered a request with `decision: approve`, while the gateway reads a `choice` that must be `once`, `session`, `always`, or `deny` — anything else falls through to its default, which is deny. So every answer, including the automatic ones sent in bypass mode, landed as a denial, and a turn died on the first dangerous command it met with "couldn't be run because I couldn't approve it". The reply now speaks the gateway's own vocabulary.
- Bypass permissions never reached the session. The `yolo` field was sent on session.create, but the gateway's handler reads model, reasoning, and title there and silently drops the rest — so the session kept asking approvals with nobody to answer them. The bypass is now applied the way Hermes' own /yolo command does it: a per-session config.set on every turn, sent before the prompt, so a mode picked between messages takes effect on the conversation already under way, and a gateway too old to know the key still works because approvals are answered per request regardless.
- In the asking modes the request is no longer denied unheard. It is put in front of the person as a question carrying the gateway's own once, session, always, and deny choices, and what is picked goes back as the choice. A turn with no dialog to ask through — a resume replay, say — still cannot hang: it answers with the mode's own decision and says so.

## Visual pass 1

- A real application icon, with the display-scaling awareness a laptop at 150 percent needs, and packaging checks so the icon and its manifest cannot silently fall out of the installer.
- Menu layouts that match what they announce, the error cue and the update dialog made presentable, and dialogs a sighted user had seen broken put right.
- Ruff's formatter is scoped out of the documentation's code samples where reformatting them changed their meaning, and the audit screenshots are no longer kept in the repository.

## Visual pass 2

- The windows follow the system's dark mode, or light or dark can be forced from a new Appearance section in Preferences. wxWidgets applies the appearance once, before the first window exists, so the dialog says the choice takes effect at the next start and announces it when it saves something new; a wxPython too old to have the appearance API notes it and carries on as it was.
- Two test suites now ask the running toolkit what it can carry rather than assuming every platform shows a chord or has the appearance enum, which is what the Linux job was telling us.

Verified with the regression suite, lint, formatting, and type checks, plus the startup and GUI smoke runs.
