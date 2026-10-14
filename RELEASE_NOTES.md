# BlindPilot 0.45.0

This release gives ChatGPT plan accounts in Chat mode the same controls the conversation profile already gave OpenRouter: how hard the model thinks, whether you hear its thinking, web search, and how long its answers are. Everything below was checked live against a signed-in ChatGPT plan.

- Thinking effort. The profile's Thinking effort is now sent to ChatGPT plan models, and there is a new Extra high level between High and Maximum. Each plan model offers its own set of levels; the current ones take Low through Maximum but refuse No thinking and Minimal. When you pick a level the chosen model does not offer, BlindPilot uses that model's default instead and tells you, rather than failing the request.
- Hearing the thinking. With "Read the thinking as it arrives" ticked, BlindPilot asks for OpenAI's thinking summary and reads it as it streams, with each part starting on its own heading. Short questions that need little thought may not produce a summary at all.
- Web search. Tick Web search in the profile's tool list and plan models can search the web, using the profile's Web search depth. Each search is announced with what was searched for, and the pages the answer cites are listed at the end. Web search is the only server tool ChatGPT plan usage runs; OpenAI refuses image generation, code interpreter and the rest, so the tool list now says so.
- Answer length. A new Answer length setting in the profile, Short, Standard or Long, sets how long ChatGPT plan answers are. Other providers ignore it.
