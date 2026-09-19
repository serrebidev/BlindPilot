# BlindPilot 0.29.7

The question dialog is kept for a turn that is waiting on you, instead of every turn that ends on a question mark.

- Since 0.29.4 a question a turn wrote into its answer opens the same dialog a question tool does - which on Command Code is the only way a question can reach you. But most turns end by asking something: "Want me to run the tests too?", "Should I commit this?", "Anything else?" Those were opening a modal over an answer you were still reading, for a question that was holding nothing up. They now let the turn end quietly, and you answer by typing whenever you like, which is what the dialog did with your answer anyway.
- A question only you can settle still opens the dialog on every backend - "Which name do you prefer?", "What should the config file be called?", "How many retries?" - and so does an offer that names a fork, because "Should I use tabs or spaces?" is a decision whatever grammar it wears.
- Questions asked through a backend's own question tool are unchanged, as is the Options switch that turns written questions off altogether.
