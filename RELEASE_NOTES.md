# BlindPilot 0.9.0

BlindPilot is an accessible desktop reader for AI coding agents. It is based on Claude
Code Reader and remains available under the MIT License, with credit to the original
project throughout the application and documentation.

This release adds two things you could not do before: ask any backend who it is signed in
as, and let a chat-mode conversation use the tools OpenRouter runs on its own servers. It
follows 0.8.1, which stopped five failures from happening in silence.

## /status is answered by every backend

`/status` was offered under Claude Code alone, and it did not work there either. Claude
Code's own `/status` is an interactive-only command; sent as a message through the
headless mode BlindPilot drives it in, it replies that the command is not available in
this environment. Codex, FreeBuff and opencode have no status command at all — FreeBuff's
own command table has `/usage` and `/diagnostics` and nothing that answers this.

So the command is BlindPilot's now, offered for all four, and each provider is asked in
the way it can actually answer. Claude Code has `claude auth status`, which replies in
JSON carrying the account, the subscription, how it was signed in, and the organisation.
Codex has `codex login status`, which replies in a sentence. The two with no command to
ask are answered from the credentials they stored: FreeBuff's account name and email, and
the providers opencode has connected. opencode's are read off disk rather than asked of
its server, because `/status` should not be the thing that spends ten seconds starting
one.

A CLI that will not run at all is reported as that, not as signed out — those are
different answers and only one of them is worth acting on. FreeBuff's stored token is the
thing its account is reached with, and never appears in the report.

The report then says what the tab is about to do: the model and effort, the permission
mode, the folder, and whether the next message continues this conversation or begins a new
one. A backend whose permission picker is disabled says the mode is not offered by it,
rather than naming a setting that has no effect there. It runs on a thread of its own,
because reaching a provider CLI takes a second or two, and opens in the read-only viewer
with focus in the text, so it can be read line by line and copied.

## Chat mode reaches OpenRouter's server tools

OpenRouter runs a set of tools itself. A model that calls one has it executed there and
the result handed back part-way through its answer — nothing runs on this computer,
nothing stops to ask permission, and there is no agent behind the chat window that would
need one. There are twelve: web search, web fetch, date and time, image generation, apply
patch, shell, bash, fusion, advisor, subagent, tool search and model search. They are a
checklist on the conversation profile, so a conversation is given the ones it needs and
nothing else.

Beside them sit the thinking budget a reasoning model gets — from minimal to maximum,
with an optional token limit — and a reader that turns an attached PDF into text any model
can read, rather than only the models that read one themselves. Every control has a name a
screen reader announces.

## The thinking is kept out of the answer

A reasoning model's thinking usually runs longer than the answer it precedes, and it is
not the answer. Reading it aloud as it streams would bury what was asked for.

It arrives as its own History entry instead. That entry's line is read out whenever the
arrow keys pass over it, so the line says how many words there are rather than being the
words; BlindPilot says "Thinking" once when it starts, the text view holds the whole of it
under its own heading, and Ctrl+C copies it. It is not saved with the conversation,
because only the answer is a message. Turning the thinking off in the profile still lets
the model think — it just does not send the words back, which is a request to the provider
rather than a filter here.

## Tools and sources are reported as they happen

A tool the model calls is spoken as it runs and left in History, down the same path a
batch's progress already used, so an answer can be read back knowing what was consulted to
produce it. When an answer cites pages, they are collected, deduplicated and written into
the end of it as a numbered Sources list, title first — an address read out character by
character is not the useful part. Those go into the answer rather than beside it, because
unlike the thinking they are part of what it rests on and have to still be there when the
conversation is reopened.

A turn that ends asking for a tool the chat window cannot run now names the tool and
points at the matching OpenRouter one, instead of reporting that the provider returned no
text.

## Older conversation profiles still open

The chat database schema is written with `CREATE TABLE IF NOT EXISTS`, which leaves an
existing table exactly as it is. The column these settings live in is therefore added with
an `ALTER TABLE` on the way past; without that, every read of the profiles table in an
older database would have failed. A profile saved before any of this existed opens with
everything switched off, which is what it was already doing, and a profile written by a
newer release, or edited by hand, falls back field by field rather than refusing to open.
