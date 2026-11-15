"""Browse connected tools and open sessions, read-only about both.

Connected tools come from the MCP config files BlindPilot already knows
(Claude's project ``.mcp.json``, Copilot's ``mcp-config.json``, Muse's
settings). Enabling, disabling and signing in stay with the backend that owns
the file; this browser only reads and reports, and says so in words.

Open sessions come from the frame's own session panels. A running or
currently open conversation cannot be hidden from here; hiding lives in
Recent Conversations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PAD = 8
PAD_DIALOG = 12


@dataclass
class ConnectedServer:
    """One MCP server found in one known config file."""

    name: str
    source: str  # the config file naming it, for the dialog
    transport: str
    available: bool
    note: str = ""


def _parse_mcp_servers(path: Path, source: str) -> list[ConnectedServer]:
    """Servers from one ``.mcp.json``-shaped file; junk yields nothing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [ConnectedServer("", source, "", False, "Config is not valid JSON.")]
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    found = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        command = str(spec.get("command") or "")
        url = str(spec.get("url") or "")
        if command:
            transport = command
        elif url:
            transport = "Remote (URL)"
        else:
            transport = "No command or URL"
        found.append(ConnectedServer(str(name), source, transport, True))
    return found


def _parse_copilot_mcp(path: Path, source: str) -> list[ConnectedServer]:
    """Copilot's ``mcp-config.json``: a list of server definitions."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [ConnectedServer("", source, "", False, "Config is not valid JSON.")]
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        return _parse_mcp_servers(path, source)
    if isinstance(data, list):
        found = []
        for index, spec in enumerate(data):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or f"server {index + 1}")
            command = str(spec.get("command") or "")
            url = str(spec.get("url") or "")
            transport = command or ("Remote (URL)" if url else "No command or URL")
            found.append(ConnectedServer(name, source, transport, True))
        return found
    return []


def list_connected_tools(cwd: str | None) -> tuple[list[ConnectedServer], list[str]]:
    """(servers, unreadable-config notes) from known MCP config locations."""
    servers: list[ConnectedServer] = []
    notes: list[str] = []
    candidates: list[tuple[Path, str, str]] = []
    if cwd:
        candidates.append((Path(cwd) / ".mcp.json", "Claude Code, this folder", "this folder"))
    candidates.append(
        (Path.home() / ".copilot" / "mcp-config.json", "Copilot CLI", "Applies to every project")
    )
    for path, backend, _scope in candidates:
        if not path.is_file():
            continue
        source = f"{backend}: {path}"
        if path.name == "mcp-config.json":
            found = _parse_copilot_mcp(path, source)
        else:
            found = _parse_mcp_servers(path, source)
        for server in found:
            if server.available:
                servers.append(server)
            elif server.note:
                notes.append(f"{source}: {server.note}")
    return servers, notes


def show_connected_tools_dialog(parent, cwd: str | None, speak: Callable[[str], None]) -> None:
    import wx

    class ToolsDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent, title="Connected Tools", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            )
            self.SetSize(self.FromDIP(wx.Size(680, 440)))
            outer = wx.BoxSizer(wx.VERTICAL)
            pad = self.FromDIP(PAD)
            outer.Add(wx.StaticText(self, label="&Connected tools (MCP servers):"), 0, wx.ALL, pad)
            self.list = wx.ListBox(self)
            self.list.SetName("Connected tools")
            outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            servers, notes = list_connected_tools(cwd)
            if not servers:
                self.list.Append("No MCP servers found in the known config files.")
            for server in servers:
                self.list.Append(f"{server.name} ({server.transport}), in {server.source}")
            for note in notes:
                self.list.Append(note)
            outer.Add(
                wx.StaticText(
                    self,
                    label="Enabling, disabling and signing in stay with the backend that owns "
                    "the file (for Claude Code, its /mcp command). This list only reads.",
                ),
                0,
                wx.ALL,
                pad,
            )
            self.status = wx.StaticText(self, label="")
            outer.Add(self.status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, pad)
            row = wx.BoxSizer(wx.HORIZONTAL)
            reveal = wx.Button(self, label="&Open config folder")
            close = wx.Button(self, wx.ID_CANCEL, "&Close")
            row.Add(reveal, 0, wx.RIGHT, pad)
            row.Add(close, 0, wx.RIGHT, pad)
            outer.Add(row, 0, wx.ALL, pad)
            self.SetSizer(outer)
            reveal.Bind(wx.EVT_BUTTON, self._reveal)
            close.Bind(wx.EVT_BUTTON, self._close)
            self.Bind(wx.EVT_CLOSE, self._close)
            self.list.SetFocus()
            self.CentreOnParent()

        def _reveal(self, _event) -> None:
            import os

            target = (
                Path.home() / ".copilot" if (Path.home() / ".copilot").is_dir() else Path.home()
            )
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except AttributeError:
                speak("Open the config folder from your file manager.")

        def _close(self, _event) -> None:
            self.Destroy()

    ToolsDialog().ShowModal()


def open_session_rows(frame) -> list[tuple[str, str, bool]]:
    """(label, session id, running) for every open session panel."""
    rows = []
    for page in frame.notebook.GetPages():
        title = getattr(page, "tab_title", None) or getattr(page, "cwd", "") or "Session"
        session_id = str(getattr(page, "_session_id", "") or "")
        running = bool(getattr(page, "_run_in_progress", None) and page._run_in_progress())
        rows.append((str(title), session_id, running))
    return rows


def show_open_sessions_dialog(parent, frame, speak: Callable[[str], None]) -> None:
    import wx

    class SessionsDialog(wx.Dialog):
        def __init__(self):
            super().__init__(
                parent, title="Open Sessions", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
            )
            self.SetSize(self.FromDIP(wx.Size(640, 380)))
            outer = wx.BoxSizer(wx.TYPE)
            pad = self.FromDIP(PAD)
            outer.Add(wx.StaticText(self, label="&Open sessions:"), 0, wx.ALL, pad)
            self.list = wx.ListBox(self, style=wx.LB_EXTENDED)
            self.list.SetName("Open sessions")
            outer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            rows = open_session_rows(frame)
            if not rows:
                self.list.Append("No sessions are open.")
            for label, session_id, running in rows:
                suffix = ", running" if running else ""
                self.list.Append(f"{label}, {session_id or 'no session yet'}{suffix}")
            outer.Add(
                wx.StaticText(
                    self,
                    label="A running or currently open conversation cannot be hidden here. "
                    "Hiding lives in Recent Conversations.",
                ),
                0,
                wx.ALL,
                pad,
            )
            self.SetSizer(outer)
            self.list.SetFocus()
            self.CentreOnParent()

    SessionsDialog().ShowModal()
