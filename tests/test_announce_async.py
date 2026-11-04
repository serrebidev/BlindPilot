from __future__ import annotations

import queue
import threading

import blindpilot_app as app


def test_windows_announce_only_enqueues_on_calling_thread(monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(app.platform, "system", lambda: "Windows")
    monkeypatch.setattr(app, "_last_announcement", "")
    monkeypatch.setattr(app, "_enqueue_windows_announcement", queued.append)
    monkeypatch.setattr(
        app, "_speak_windows_announcement", lambda text: (_ for _ in ()).throw(AssertionError())
    )

    app.announce("Working")

    assert queued == ["Working"]
    assert app._last_announcement == "Working"


def test_announcement_worker_delivers_in_fifo_order(monkeypatch):
    messages: queue.Queue[str] = queue.Queue()
    delivered: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def speak(text: str) -> None:
        if text == "first":
            started.set()
            assert release.wait(timeout=2)
        delivered.append(text)

    monkeypatch.setattr(app, "_speak_windows_announcement", speak)
    worker = threading.Thread(target=app._announcement_worker, args=(messages,), daemon=True)
    worker.start()
    messages.put("first")
    assert started.wait(timeout=2)
    messages.put("second")
    release.set()
    messages.join()

    assert delivered == ["first", "second"]
