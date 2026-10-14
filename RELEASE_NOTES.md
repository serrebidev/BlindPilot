# BlindPilot 0.46.0

This release makes a ChatGPT plan account in Chat mode search and research the way ChatGPT itself does.

- Web search is always on for ChatGPT plan accounts, and the model decides when to use it. In 0.45.0 it searched only when the conversation profile had OpenRouter's Web search ticked, so a profile set up for other tools left the model saying it could not search the web. The profile's Web search depth still applies.
- The model can now read the pages it finds, and check your local date and time, through two tools BlindPilot runs on your computer. It can search, read a page, and search again, for up to eight rounds in one answer, which is enough for "look at ten results and find me..." requests.
- Page reading only opens public web addresses. It refuses your own computer and your local network, so a web page cannot steer it towards them.
- Every step is spoken as it happens: each search and what it searched for, each page opened or read, and checking the clock. The sources the answer cites are listed at the end.
