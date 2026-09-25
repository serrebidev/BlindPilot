# BlindPilot 0.29.22

Answers from Muse Code, Hermes and Command Code now read the way they were written, found by checking a real Muse session against Muse's own saved record of it.

- Streamed answers keep their shape. These backends send their answer a sentence at a time so you hear it straight away, and BlindPilot turned every sentence into a row of its own. A numbered list lost its numbers and fell apart into single lines, and a line such as "Fallback: sound." was split at its colon. Each sentence is still spoken the moment it arrives, but a row is now added only once its whole paragraph, list or heading is finished. Rows are only ever added to the end, so the list never rebuilds or moves under you while you read.
- Hermes answers keep their paragraph breaks. The spaces and blank lines between sentences were dropped on the way to the window, which ran separate paragraphs together.
- Muse tool steps say what they are doing. A search read only "search" because Muse sends the search text in a field BlindPilot did not look at; it now reads, for example, "search: def _uuid". A to-do update names its items instead of reading out a line of bookkeeping like "items 4, ok true, revision 1".
