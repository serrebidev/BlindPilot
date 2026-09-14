# BlindPilot 0.29.0

Chat mode gains Command Code's Provider API, and file attachments now work on every chat account.

- Command Code joins the account list. One account reaches every model Command Code sells - Claude, GPT, Gemini, and the strongest open models - billed at their underlying rates with every deal applied automatically. Subscribe to any plan above Go, create an API key in Command Code Studio, and type it in with a name of your choosing; the addresses are built in and nothing else is asked of you.
- Claude models on a Command Code account are sent over Anthropic's Messages protocol, and every other model over Chat Completions. The choice is made per model as the request is built, because Command Code serves each protocol only to the models that speak it. An account whose API mode you have set yourself keeps your choice.
- Attachments are no longer an OpenRouter privilege. Every chat account takes files: images and PDFs travel as the protocol's own content blocks on the Messages protocol, and any other file goes in as its text, which is how Chat Completions already carried them. Accounts left on OpenAI's Responses API still say clearly that they cannot take attachments, because that protocol has no file block BlindPilot can serve them through.
