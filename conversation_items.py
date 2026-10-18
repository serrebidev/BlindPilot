"""Searchable code and link lists shared by Agent and Chat conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit

import wx
from markdown_it import MarkdownIt


class _ExtractionMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        # Retain destinations for copying; opening checks them separately.
        return True


_MARKDOWN = _ExtractionMarkdown("commonmark")
_WEB_ADDRESS = re.compile(r"https?://[^\s<>\x00-\x1f`\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class ConversationItem:
    label: str
    payload: str


def can_open_link(address: str) -> bool:
    """Only web links with a host may reach the system browser."""
    if any(character.isspace() or ord(character) < 32 for character in address):
        return False
    try:
        parsed = urlsplit(address)
        _ = parsed.port  # Validate malformed or out-of-range ports before offering Open.
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def collect_items(messages: Sequence[tuple[str, str]], kind: str) -> list[ConversationItem]:
    """Parse original Markdown, preserving code and link destinations exactly."""
    items: list[ConversationItem] = []
    seen: set[str] = set()

    def add_link(address: str, source: str, words: str = "", in_code: bool = False) -> None:
        if not address or address in seen:
            return
        seen.add(address)
        notes = ", in code" if in_code else ""
        if not can_open_link(address):
            notes += ", copy only"
        label = f"{source}: {words + ' — ' if words else ''}{address}{notes}"
        items.append(ConversationItem(label, address))

    def bare_links(text: str, source: str, in_code: bool = False) -> None:
        for match in _WEB_ADDRESS.finditer(text):
            address = match.group().rstrip(".,;:!")
            for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
                while address.endswith(closing) and address.count(closing) > address.count(opening):
                    address = address[:-1]
            add_link(address, source, in_code=in_code)

    for source, text in messages:
        for token in _MARKDOWN.parse(text):
            if token.type in {"fence", "code_block"}:
                if kind == "code":
                    language = token.info.split()[0] if token.info.strip() else "code"
                    lines = len(token.content.splitlines())
                    unit = "line" if lines == 1 else "lines"
                    items.append(
                        ConversationItem(f"{source}: {language}, {lines} {unit}", token.content)
                    )
                else:
                    bare_links(token.content, source, True)
            elif kind == "links" and token.children:
                children = token.children
                for index, child in enumerate(children):
                    if child.type == "link_open":
                        words = []
                        for following in children[index + 1 :]:
                            if following.type == "link_close":
                                break
                            words.append(following.content)
                        address = child.attrGet("href")
                        if isinstance(address, str):
                            add_link(address, source, "".join(words))
                    elif child.type in {"text", "code_inline"}:
                        bare_links(child.content, source, child.type == "code_inline")
    return items


class ConversationItemsDialog(wx.Dialog):
    """Native controls; filtering never moves focus or reads the full payload."""

    def __init__(
        self,
        parent: wx.Window,
        items: Sequence[ConversationItem],
        title: str,
        *,
        links: bool,
        copy: Callable[[str], bool],
        read: Callable[[ConversationItem], None],
        announce: Callable[[str], None],
    ):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.items = list(items)
        self.visible = list(items)
        self._copy = copy
        self._read = read
        self._announce = announce
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(wx.StaticText(self, label="&Filter:"), 0, wx.LEFT | wx.TOP, self.FromDIP(12))
        self.filter = wx.TextCtrl(self)
        layout.Add(self.filter, 0, wx.EXPAND | wx.ALL, self.FromDIP(12))
        layout.Add(wx.StaticText(self, label="&Items:"), 0, wx.LEFT, self.FromDIP(12))
        self.list = wx.ListBox(self)
        self.list.SetMinSize(self.FromDIP(wx.Size(650, 220)))
        layout.Add(self.list, 1, wx.EXPAND | wx.ALL, self.FromDIP(12))
        layout.Add(wx.StaticText(self, label="&Text:"), 0, wx.LEFT, self.FromDIP(12))
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        self.text.SetMinSize(self.FromDIP(wx.Size(650, 140)))
        layout.Add(self.text, 1, wx.EXPAND | wx.ALL, self.FromDIP(12))
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.read_button = wx.Button(self, label="&Read Item")
        self.read_button.SetDefault()
        self.read_button.Bind(wx.EVT_BUTTON, self._on_read)
        buttons.Add(self.read_button, 0, wx.RIGHT, self.FromDIP(8))
        self.copy_button = wx.Button(self, label="&Copy")
        self.copy_button.Bind(wx.EVT_BUTTON, self._on_copy)
        buttons.Add(self.copy_button, 0, wx.RIGHT, self.FromDIP(8))
        self.open_button = None
        if links:
            self.open_button = wx.Button(self, label="&Open Link")
            self.open_button.Bind(wx.EVT_BUTTON, self._on_open)
            buttons.Add(self.open_button, 0, wx.RIGHT, self.FromDIP(8))
        buttons.Add(wx.Button(self, wx.ID_CANCEL, label="Close"))
        layout.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, self.FromDIP(12))
        self.SetSizerAndFit(layout)
        self.SetMinSize(self.GetSize())
        self.filter.Bind(wx.EVT_TEXT, self._refresh)
        self.list.Bind(wx.EVT_LISTBOX, self._select)
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, self._on_read)
        self.list.Bind(wx.EVT_KEY_DOWN, self._on_key)
        self._refresh()
        self.CentreOnParent()
        wx.CallAfter(self.list.SetFocus)

    def _selected(self) -> ConversationItem | None:
        index = self.list.GetSelection()
        return self.visible[index] if 0 <= index < len(self.visible) else None

    def _refresh(self, _event=None) -> None:
        term = self.filter.GetValue().casefold()
        self.visible = [
            item for item in self.items if term in f"{item.label}\n{item.payload}".casefold()
        ]
        self.list.Set([item.label for item in self.visible])
        self.list.SetName(f"Items, {len(self.visible)} of {len(self.items)}")
        if self.visible:
            self.list.SetSelection(0)
        self._select()

    def _select(self, _event=None) -> None:
        item = self._selected()
        self.text.ChangeValue(item.payload if item else "No matching items.")
        self.text.SetInsertionPoint(0)
        self.read_button.Enable(item is not None)
        self.copy_button.Enable(item is not None)
        if self.open_button is not None:
            self.open_button.Enable(item is not None and can_open_link(item.payload))

    def _on_read(self, _event=None) -> None:
        item = self._selected()
        if item:
            self._read(item)
            self.list.SetFocus()

    def _on_copy(self, _event=None) -> None:
        item = self._selected()
        if item:
            self._announce(
                "Copied item" if self._copy(item.payload) else "Error: Could not access clipboard"
            )

    def _on_open(self, _event=None) -> None:
        item = self._selected()
        if item and can_open_link(item.payload) and not wx.LaunchDefaultBrowser(item.payload):
            self._announce("Error: Could not open link")

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.CmdDown() and event.GetKeyCode() == ord("C"):
            self._on_copy()
        else:
            event.Skip()
