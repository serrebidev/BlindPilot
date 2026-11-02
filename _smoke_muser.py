import time

from muser_backend import MuserWorker

events = []
worker = MuserWorker(
    "Reply with exactly the word: ok",
    None,
    "C:/Users/admin/git/BlindPilot",
    "plan",
    on_session=lambda s: events.append(("session", s)),
    on_started=lambda: events.append(("started", "")),
    on_activity=lambda k, t: events.append((k, t)),
    on_complete=lambda t: events.append(("complete", t)),
    on_failed=lambda t: events.append(("failed", t)),
    on_done=lambda: events.append(("done", "")),
)
worker.start()
deadline = time.time() + 240
while time.time() < deadline:
    if any(kind in ("complete", "failed") for kind, _ in events):
        break
    time.sleep(0.5)
worker.join(timeout=10)
for kind, text in events:
    print(kind, "|", str(text)[:200])
