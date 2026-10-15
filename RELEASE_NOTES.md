# BlindPilot 0.47.0

This release hardens the backends, the workers, and settings saving, and makes Command Code Auto handle rate and credit limits more clearly.

- During a recorded rate or credit cooldown, Command Code Auto runs any task on a model the installed catalog confirms is free, and announces that choice. It also speaks up when routing is unavailable, no free route is confirmed, or a limit cannot be retried safely. The cooldown is shared across processes and matches the model id regardless of case, and a provider-capacity stop gets one announced retry on the next paid model.
- Turns can no longer complete twice after a cancel, compaction, replay or result, in any of the three workers. Subagent rows are counted, and Muse reasoning arrives as one row.
- A settings save that fails is now always spoken, once, including a credential write that fails. Before, some of these failures were silent.
- Security and reliability fixes underneath: the Muse log is read without a shell, Hermes refuses a cleartext login, FreeBuff's cache is replaced atomically, and a process group that is ended gets a bounded wait with its pipes closed, so a wedged turn cannot leave the app stuck.
- The update-status file moved from shared temporary storage into your per-user data folder.
