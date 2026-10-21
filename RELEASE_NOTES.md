# BlindPilot 0.49.1

Quitting now ends the process, not just the window.

- **Alt+F4 closes BlindPilot for good:** the window closed, but the process could stay running in the background with nothing to show for itself. A wx application leaves its event loop only when the last top-level window is destroyed, so any other window left standing — a dialog parented to nothing, a window kept by a platform component — held the whole process open, and a thread started by a library without being made a daemon held it open after the loop, because Python waits for those before it will exit. The close handler now destroys every other top-level window and asks the event loop to end outright, and an exit watchdog armed when the application starts gives the process a bounded fifteen-second grace to finish once the window is gone before ending it. Everything BlindPilot saves is saved before and during the close, so nothing is lost by the ending being certain.
- Regression tests pin the close handler's new last steps and the watchdog's grace period, its firing, and that the watchdog itself is a daemon and can never be the thing keeping the process alive.

Thanks to Farhan Khan for reporting the hang in #102.
