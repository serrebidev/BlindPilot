"""Searchable code and link lists shared by Agent and Chat conversations."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit
from urllib.request import url2pathname

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
    # For a link, the words the link was written with, when it had any;
    # empty for code blocks and for bare addresses.
    text: str = ""
    # For a link to a file on this computer, its resolved path.
    path: str = ""


# File types that run code when "opened": they get Open Containing Folder and
# Copy, never Open, so a reply cannot launch a program with one Enter.
RUNS_CODE = frozenset(
    ".exe .com .bat .cmd .msi .msp .scr .pif .cpl .ps1 .psm1 .vbs .vbe .js .jse .wsf .wsh "
    ".hta .lnk .url .reg .jar .py .pyw .sh .bash .zsh .command .app .desktop .appimage .run "
    ".scpt .workflow .action .apk".split()
)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?![\\/])")
_POSITION = re.compile(r":\d+(?::\d+)?$")
_PATHLIKE = re.compile(r"[\\/]|\.[A-Za-z0-9]{1,8}$")


def file_target(address: str, cwd: str = "") -> Path | None:
    """The local file a link or a lone path names, or None when it is not one.

    ``file://`` addresses and plain paths both count; a relative path is taken
    from the conversation's working folder. Web addresses and other schemes
    are not files.
    """
    address = address.strip().strip("<>")
    if not address or len(address) > 1024 or "\n" in address:
        return None
    if address.lower().startswith("file:"):
        parts = urlsplit(address)
        local = url2pathname(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost":
            local = f"//{parts.netloc}{local}"
        return Path(local)
    if _SCHEME.match(address) or address.startswith("#"):
        return None  # http:, mailto: and the like; C:\ and C:/ are paths
    # A trailing :line or :line:column is a position in the file, not its name.
    address = _POSITION.sub("", address.split("#", 1)[0])
    if not _PATHLIKE.search(address):
        return None
    path = Path(os.path.expanduser(address))
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd) / path
    return path


def file_notes(path: Path) -> str:
    """What the row says about a file: missing, or why it only opens its folder."""
    if not path.exists():
        return ", file, not found, copy only"
    if path.suffix.lower() in RUNS_CODE:
        return ", file that runs code, folder and copy only"
    return ", file"


def can_open_file(path: str) -> bool:
    """Checked again just before opening: the file may have changed since."""
    if not path:
        return False
    target = Path(path)
    return target.exists() and target.suffix.lower() not in RUNS_CODE


def markdown_link(item: ConversationItem) -> str:
    """An item as a Markdown link: its words, or the address when it had none."""
    words = (item.text or item.payload).replace("[", "(").replace("]", ")")
    return f"[{words}]({item.payload})"


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


def collect_items(
    messages: Sequence[tuple[str, str]], kind: str, cwd: str = ""
) -> list[ConversationItem]:
    """Parse original Markdown, preserving code and link destinations exactly.

    Links to files on this computer are listed with their resolved path, and
    so are code spans and one-line code blocks that hold nothing but the path
    of a file that exists.
    """
    items: list[ConversationItem] = []
    seen: set[str] = set()

    def add_link(address: str, source: str, words: str = "", in_code: bool = False) -> None:
        if not address or address in seen:
            return
        seen.add(address)
        notes = ", in code" if in_code else ""
        target = None if can_open_link(address) else file_target(address, cwd)
        if target is not None:
            notes += file_notes(target)
        elif not can_open_link(address):
            notes += ", copy only"
        label = f"{source}: {words + ' — ' if words else ''}{address}{notes}"
        items.append(ConversationItem(label, address, words, str(target or "")))

    def lone_path(text: str, source: str) -> None:
        text = text.strip().strip("\"'")
        if not text or "\n" in text or " " in text:
            return
        target = file_target(text, cwd)
        if target is not None and target.exists():
            add_link(text, source, in_code=True)

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
                    lone_path(token.content, source)
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
                        if child.type == "code_inline":
                            lone_path(child.content, source)
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
        read_label: str = "&Read Item",
        open_file: Callable[[str], bool] | None = None,
    ):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.items = list(items)
        self.visible = list(items)
        self._copy = copy
        self._read = read
        self._announce = announce
        self._open_file = open_file
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
        self.read_button = wx.Button(self, label=read_label)
        self.read_button.SetDefault()
        self.read_button.Bind(wx.EVT_BUTTON, self._on_read)
        buttons.Add(self.read_button, 0, wx.RIGHT, self.FromDIP(8))
        self.copy_button = wx.Button(self, label="&Copy")
        self.copy_button.Bind(wx.EVT_BUTTON, self._on_copy)
        buttons.Add(self.copy_button, 0, wx.RIGHT, self.FromDIP(8))
        self.open_button = None
        self.markdown_button = None
        self.folder_button = None
        if links:
            self.open_button = wx.Button(self, label="&Open")
            self.open_button.Bind(wx.EVT_BUTTON, self._on_open)
            buttons.Add(self.open_button, 0, wx.RIGHT, self.FromDIP(8))
            if open_file is not None:
                self.folder_button = wx.Button(self, label="Open Containing &Folder")
                self.folder_button.Bind(wx.EVT_BUTTON, self._on_open_folder)
                buttons.Add(self.folder_button, 0, wx.RIGHT, self.FromDIP(8))
            self.markdown_button = wx.Button(self, label="Copy as &Markdown")
            self.markdown_button.Bind(wx.EVT_BUTTON, self._on_copy_markdown)
            buttons.Add(self.markdown_button, 0, wx.RIGHT, self.FromDIP(8))
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
            self.open_button.Enable(
                item is not None
                and (
                    can_open_link(item.payload)
                    or (self._open_file is not None and can_open_file(item.path))
                )
            )
        if self.folder_button is not None:
            self.folder_button.Enable(
                item is not None and bool(item.path) and Path(item.path).parent.is_dir()
            )
        if self.markdown_button is not None:
            self.markdown_button.Enable(item is not None)

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
        if not item:
            return
        if item.path and self._open_file is not None:
            if not can_open_file(item.path):
                self._announce("This file is missing or runs code, so it is not opened")
            elif not self._open_file(item.path):
                self._announce(f"Error: Could not open {item.path}")
            return
        if can_open_link(item.payload) and not wx.LaunchDefaultBrowser(item.payload):
            self._announce("Error: Could not open link")

    def _on_open_folder(self, _event=None) -> None:
        item = self._selected()
        if not item or not item.path or self._open_file is None:
            return
        folder = Path(item.path).parent
        if not folder.is_dir() or not self._open_file(str(folder)):
            self._announce(f"Error: Could not open {folder}")

    def _on_copy_markdown(self, _event=None) -> None:
        item = self._selected()
        if item:
            self._announce(
                "Copied Markdown link"
                if self._copy(markdown_link(item))
                else "Error: Could not access clipboard"
            )

    def _on_key(self, event: wx.KeyEvent) -> None:
        if event.CmdDown() and event.GetKeyCode() == ord("C"):
            self._on_copy()
        else:
            event.Skip()
