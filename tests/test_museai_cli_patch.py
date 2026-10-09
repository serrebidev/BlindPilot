# SPDX-License-Identifier: MIT
"""Large muse-cli responses must be assembled before protobuf decoding."""

from types import SimpleNamespace

import pytest

import agent_backends


def test_muse_cli_processes_load_the_compatibility_hook_but_other_backends_do_not(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = agent_backends.subprocess_env("muse-cli")
    assert "museai_cli_patch" in env.get("PYTHONPATH", "")
    assert "PYTHONPATH" not in agent_backends.subprocess_env("codex")


def _chunk(group, index, count, payload):
    return SimpleNamespace(chunk_id=group, chunk_index=index, total_chunks=count, payload=payload)


def test_fragments_are_ordered_and_interleaved_messages_stay_independent():
    from museai_cli_patch.sitecustomize import assemble

    groups = {}
    assert assemble(groups, _chunk("a", 1, 2, b"second")) is None
    assert assemble(groups, _chunk("b", 0, 2, b"other")) is None
    assert assemble(groups, _chunk("", 0, 0, b"small")) == b"small"
    assert assemble(groups, _chunk("a", 0, 2, b"first")) == b"firstsecond"
    assert assemble(groups, _chunk("b", 1, 2, b"reply")) == b"otherreply"
    assert groups == {}


@pytest.mark.parametrize("index,count", [(2, 2), (-1, 2), (0, 2000)])
def test_invalid_chunk_metadata_is_rejected(index, count):
    from museai_cli_patch.sitecustomize import assemble

    with pytest.raises(ValueError):
        assemble({}, _chunk("a", index, count, b"bad"))


def test_conflicting_duplicate_fragments_are_rejected():
    from museai_cli_patch.sitecustomize import assemble

    groups = {}
    assert assemble(groups, _chunk("a", 0, 2, b"one")) is None
    with pytest.raises(ValueError):
        assemble(groups, _chunk("a", 0, 2, b"different"))


def test_current_web_schedule_run_route_is_available_to_upstream_cli(monkeypatch):
    import sys
    from museai_cli_patch import sitecustomize

    gateway = SimpleNamespace(ROUTES={}, Gateway=type("Gateway", (), {}))
    cli = SimpleNamespace(cmd_watch=None)
    monkeypatch.setattr(sitecustomize, "version", lambda _name: "0.3.2")
    monkeypatch.setitem(sys.modules, "muse_cli", SimpleNamespace(gateway=gateway, cli=cli))
    sitecustomize.install()
    assert gateway.ROUTES["tasks.run"] == {
        "method": "tasks.run",
        "http": "POST",
        "path": "/tasks/{job_id}/run",
    }


def _frame(stream, kind, data, end=False):
    part = SimpleNamespace(body=data, data=data, end_body=end)
    return SimpleNamespace(stream_id=stream, WhichOneof=lambda _k: kind, **{kind: part})


def test_watch_streams_a_chats_live_status_the_way_the_web_client_subscribes(monkeypatch, capsys):
    """muse-cli 0.3.2's watch never sees a side chat's live status (it sends
    capabilities as an object and no session), and prints nothing until it
    ends. With BLINDPILOT_MUSEAI_WATCH set it subscribes to that chat as the web
    client does and prints each event line as soon as it is whole."""
    import sys
    from museai_cli_patch import sitecustomize

    opened = []
    frames = iter(
        [
            _frame(1, "response", b'{"event":"agent.status"}\n{"event":'),
            _frame(2, "body_chunk", b'{"event":"other stream"}\n'),
            _frame(1, "body_chunk", b'"task.status"}\n', end=True),
        ]
    )
    gw = SimpleNamespace(
        _open=lambda method, body: opened.append((method, body)) or 1,
        _read_frame=lambda: next(frames),
        close=lambda: opened.append("closed"),
    )
    plain = []
    cli = SimpleNamespace(cmd_watch=plain.append, connect=lambda _cfg: gw, load_config=dict)
    gateway = SimpleNamespace(ROUTES={}, Gateway=type("Gateway", (), {}))
    monkeypatch.setattr(sitecustomize, "version", lambda _name: "0.3.2")
    monkeypatch.setitem(sys.modules, "muse_cli", SimpleNamespace(gateway=gateway, cli=cli))
    sitecustomize.install()

    monkeypatch.delenv("BLINDPILOT_MUSEAI_WATCH", raising=False)
    cli.cmd_watch("args")
    assert plain == ["args"] and opened == []

    monkeypatch.setenv("BLINDPILOT_MUSEAI_WATCH", "chat-7")
    cli.cmd_watch("args")
    method, body = opened[0]
    assert method == "chat.subscribe"
    assert body["session_id"] == "chat-7"
    assert isinstance(body["capabilities"], list)
    assert opened[-1] == "closed"
    assert capsys.readouterr().out.splitlines() == [
        '{"event":"agent.status"}',
        '{"event":"task.status"}',
    ]
