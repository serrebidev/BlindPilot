# BlindPilot 0.42.0

BlindPilot can now keep the messages you send often, so you do not have to type or paste them again. Open Saved Prompts from the Conversation menu, or press Ctrl+Shift+P anywhere in a session.

- The dialog lists your prompts by name. As you arrow through them, the selected prompt's full text is shown in a read-only box below the list (Alt+X), so you can check it before using it.
- Use, or Enter on a prompt, closes the dialog and puts the prompt into the prompt box where your caret was. If you had text selected, the prompt replaces it; anything else you typed stays. Nothing is sent until you press Enter, so you can finish or change it first.
- New starts from whatever is in the prompt box, with its first few words offered as the name. To save the message you have just written, press Ctrl+Shift+P, then New, and accept the name and text. If the prompt box is empty, you type both.
- Edit changes a prompt's name or text. Delete, or the Delete key on the list, removes one after asking, with No as the default. Move Up and Move Down put them in the order you want.
- Every change is saved straight away and spoken ("Prompt saved: Review", "Prompt deleted: Review"). Prompts live in BlindPilot's own settings file next to your other preferences; no backend's files are touched, and they work the same with every backend.
- Escape or Close leaves the dialog and returns you to the prompt without changing it.

The shortcut is listed in Help, Keyboard Shortcuts (F1) and in the README.
