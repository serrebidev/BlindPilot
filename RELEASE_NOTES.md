# BlindPilot 0.28.4

Command Code can be steered and queued, and it no longer flashes a console window when it updates.

- Sending a message while Command Code is working no longer says "still finishing". The message is queued and goes out, in order and with its files, as soon as the running turn ends.
- Steer stops the running turn and resumes the conversation with your new instruction. Stop pauses the queue, and `/queue list`, `/queue clear` and `/queue resume` let you inspect, discard or release what is waiting.
- Command Code's own commands now have working equivalents, including `/effort`, `/mode`, `/plan`, `/add-dir`, `/copy` and `/sessions`. Commands that only exist in its terminal interface are explained when you type them, instead of being sent to the model as text.
- `/compact` now works for Command Code. It summarizes the conversation into a new session; the original stays in Recent Conversations, and stays selected unless the new session is saved.
- Command Code no longer starts its own background updater during a turn, a status check or a model list. That updater was what put a console window on screen. BlindPilot's own install and update steps run hidden and non-interactively, with their output in the log.
