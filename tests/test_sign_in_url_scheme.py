"""A sign-in address is opened as a web page or not at all.

opencode hands back the address for a provider's browser sign-in and expects
whoever asked to open it. That address comes from a provider catalog
BlindPilot does not control and never sees the inside of, and it went straight
to `webbrowser.open`. On Windows that is the default protocol handler: a
`file:` address opens whatever is at that path, and `ms-msdt:` and its
relatives are handed to programs of their own. The sign-in the CLIs do next
door already constrains its address to http or https; this one did not.
"""

from __future__ import annotations

import pytest

import blindpilot_app


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/System32/calc.exe",
        "ms-msdt:/id PCWDiagnostic /skip force /param IT_LaunchMethod=ContextMenu",
        "javascript:alert(1)",
        r"search-ms:query=passwords&crumb=location:\\attacker\share",
        r"\\attacker\share\payload.lnk",
    ],
)
def test_an_address_that_is_not_a_web_page_is_not_opened(url, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(blindpilot_app.webbrowser, "open", lambda target: opened.append(target))

    assert blindpilot_app._open_web_page(url) is False
    assert opened == [], f"handed to the platform's protocol handler: {url}"


@pytest.mark.parametrize(
    "url",
    ["https://auth.example.com/authorize?state=abc", "HTTP://auth.example.com/authorize"],
)
def test_a_web_page_is_still_opened(url, monkeypatch):
    opened: list[str] = []

    def opener(target):
        opened.append(target)
        return True

    monkeypatch.setattr(blindpilot_app.webbrowser, "open", opener)

    assert blindpilot_app._open_web_page(url) is True
    assert opened == [url]


def test_a_browser_that_will_not_start_is_not_an_error(monkeypatch):
    """The address is spoken and shown either way, so this only reports."""

    def opener(_target):
        raise OSError("no browser on this machine")

    monkeypatch.setattr(blindpilot_app.webbrowser, "open", opener)

    assert blindpilot_app._open_web_page("https://auth.example.com/authorize") is False
