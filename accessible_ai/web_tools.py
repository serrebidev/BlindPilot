"""Read-only web tools BlindPilot runs itself for any Chat account.

OpenRouter runs its own server tools and a ChatGPT plan has OpenAI's web
search; every other account (OpenAI with a key, Claude, Gemini, Ollama, a
custom endpoint) gets these three instead. They only read, so nothing stops
to ask: a search through DuckDuckGo's HTML page (no key needed), reading one
public page as text, and this computer's clock.

Definitions are in the Chat Completions shape; ``protocols`` converts them for
the Messages and Responses protocols.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_RESULTS = 8
SEARCH_TIMEOUT = 20.0

WEB_SEARCH = "web_search"
FETCH_PAGE = "fetch_page"
CURRENT_TIME = "current_time"
NAMES = frozenset({WEB_SEARCH, FETCH_PAGE, CURRENT_TIME})

DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": WEB_SEARCH,
            "description": (
                "Search the web. Returns titles, addresses and snippets; read a result "
                "in full with fetch_page."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to search for."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": FETCH_PAGE,
            "description": "Read a public web page and return its text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The http or https address."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": CURRENT_TIME,
            "description": "The user's current local date, time and time zone.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class _Results(HTMLParser):
    """Titles, addresses and snippets from DuckDuckGo's HTML results page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._field = ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "result__a" in classes:
            self.results.append(
                {"title": "", "url": _real_url(attributes.get("href") or ""), "snippet": ""}
            )
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._field = ""

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data


def _real_url(href: str) -> str:
    """The address behind DuckDuckGo's redirect link."""
    parts = urlsplit(href)
    target = parse_qs(parts.query).get("uddg")
    if target:
        return target[0]
    return "https:" + href if href.startswith("//") else href


def web_search(query: str) -> str:
    """Search results as numbered text for the model."""
    query = query.strip()
    if not query:
        return "Give a search query."
    try:
        response = httpx.get(
            SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 BlindPilot"},
            timeout=SEARCH_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return f"The search could not be run: {exc}"
    if response.status_code != 200:
        # DuckDuckGo answers 202 with a challenge page when it is rate limiting.
        return f"The search engine answered HTTP {response.status_code}; try again shortly."
    parser = _Results()
    parser.feed(response.text)
    found = [r for r in parser.results if r["url"].startswith(("http://", "https://"))]
    # Ads come first and point back through duckduckgo.com/y.js.
    found = [r for r in found if "duckduckgo.com/y.js" not in r["url"]][:SEARCH_RESULTS]
    if not found:
        return f"No results for {query}."
    lines = [f"Results for {query}:"]
    for number, result in enumerate(found, start=1):
        title = " ".join(result["title"].split())
        snippet = " ".join(result["snippet"].split())
        lines.append(f"{number}. {title}\n{result['url']}\n{snippet}")
    return "\n\n".join(lines)


def run(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Run one web tool. Returns (output for the model, what to announce)."""
    # The page reader and the clock are the ChatGPT plan's own, so both
    # routes read pages with the same private-address guard.
    from accessible_ai.providers import chatgpt

    if name == WEB_SEARCH:
        query = str(args.get("query") or "")
        return web_search(query), f"Searching the web for {query}"
    if name == FETCH_PAGE:
        return chatgpt.run_local_tool("fetch_url", json.dumps({"url": str(args.get("url") or "")}))
    if name == CURRENT_TIME:
        return chatgpt.run_local_tool("current_time", "{}")
    return f"There is no tool named {name}.", f"The model asked for an unknown tool, {name}"


def executor(
    fallback: Callable[[str, dict[str, Any]], str] | None,
) -> Callable[[str, dict[str, Any]], str]:
    """A tool executor that runs the web tools and hands anything else on."""

    def run_one(name: str, args: dict[str, Any]) -> str:
        if name in NAMES:
            return run(name, args)[0]
        if fallback is not None:
            return fallback(name, args)
        return f"There is no tool named {name}."

    return run_one
