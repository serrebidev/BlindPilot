"""Connected-tools and open-sessions browsers read only known locations."""

from __future__ import annotations

import json

from connected_dialogs import (
    _parse_copilot_mcp,
    _parse_mcp_servers,
    list_connected_tools,
    open_session_rows,
)


def test_mcp_json_servers_parse(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {"command": "npx", "args": ["-y", "server-memory"]},
                    "remote": {"url": "https://example.test/mcp", "type": "http"},
                    "broken": "not-a-server",
                }
            }
        ),
        encoding="utf-8",
    )
    found = _parse_mcp_servers(path, "test")
    by_name = {server.name: server for server in found}
    assert by_name["memory"].transport == "npx"
    assert by_name["remote"].transport == "Remote (URL)"
    assert "broken" not in by_name


def test_mcp_json_malformed_is_unavailable_not_fatal(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{oops", encoding="utf-8")
    found = _parse_mcp_servers(path, "test")
    assert len(found) == 1
    assert found[0].available is False
    assert "not valid JSON" in found[0].note


def test_copilot_config_list_shape(tmp_path):
    path = tmp_path / "mcp-config.json"
    path.write_text(json.dumps([{"name": "search", "command": "search-server"}]), encoding="utf-8")
    found = _parse_copilot_mcp(path, "test")
    assert found[0].name == "search" and found[0].transport == "search-server"


def test_list_connected_tools_uses_known_locations(tmp_path, monkeypatch):
    monkeypatch.setattr("connected_dialogs.Path.home", lambda: tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"proj": {"command": "p"}}}), encoding="utf-8"
    )
    servers, notes = list_connected_tools(str(project))
    assert any(server.name == "proj" for server in servers)


def test_open_session_rows_flag_running():
    class Page:
        tab_title = "Project"
        _session_id = "abc"

        def _run_in_progress(self):
            return True

    class Notebook:
        def GetPages(self):
            return [Page()]

    class Frame:
        notebook = Notebook()

    rows = open_session_rows(Frame())
    assert rows == [("Project", "abc", True)]
