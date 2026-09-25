# BlindPilot 0.29.21

More Muse Code fixes, found by checking a long real session against Muse's own saved record of it.

- Muse no longer reads out a refusal for a permission you gave. When Muse was busy, it sometimes replied to your Allow with an internal error even though it had already saved your choice and run the command. You heard "Muse Code refused" for something that went ahead. BlindPilot now quietly sends the same choice once more, which Muse treats as the same choice rather than a second one. You only hear a refusal if that second try fails too. This also covers the rare case where Muse really did lose your choice, which would otherwise have left the turn waiting.
- You can answer a Muse question in your own words. When you typed your own answer instead of picking an option, Muse rejected it and the turn waited on the question forever. Your typed text now reaches Muse as your answer.
- Closing a Muse question without answering, or pressing Stop while one is open, now tells Muse you declined. Before, the turn stayed stuck on the question.
- BlindPilot now sends the short acknowledgement Muse expects each time it shows you a question or a permission request.
- On Hermes and Muse, pressing Stop at the exact moment a turn sends its own request can no longer give both the same number, which could have sent a reply to the wrong place.
