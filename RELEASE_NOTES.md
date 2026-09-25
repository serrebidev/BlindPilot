# BlindPilot 0.29.23

Command Code now reads like Claude Code and Codex. The difference was found by running a real Command Code turn that used every common tool, then playing its output back through BlindPilot next to what Claude produces for the same work.

- Tool steps say what is happening. Command Code's steps read out the raw tool name and the whole absolute path, for example "read_file: C:\Users\you\projects\app\a.txt". They now use the same wording as Claude: "Reading a.txt", "Editing a.txt, 1 line added, 1 removed", "Writing b.txt, 2 lines", "Running: git status", "Searching for gamma" and "Listing src".
- Results show just the output. A tool's result no longer starts with the tool's name, so its preview line is the first line of what the tool returned, the same as on Claude and Codex.
- Thinking is one row per thought. When Show thinking is on, Command Code's reasoning arrived a word or two at a time and each piece became a row and was spoken separately. Each thought is now one row.
- Narration between tools is its own paragraph. Command Code writes a short line before each tool call, such as a heading saying what it is about to do. That line had no full stop, so it was held back and then glued onto the next one, giving "## Reading a.txt## Editing a.txt". Each line is now finished and read before its tool runs.
- Streamed answers are not split mid-word. A full stop at the very end of a streamed chunk was treated as the end of a sentence even when the next chunk carried on the word, so "*.txt" could be read as "*." and "txt" on two rows. This affected Command Code and Muse Code.
- Command Code's partial tool output no longer adds a row that just says "tool_update".
