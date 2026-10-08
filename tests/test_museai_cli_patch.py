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
    monkeypatch.setattr(sitecustomize, "version", lambda _name: "0.3.2")
    monkeypatch.setitem(sys.modules, "muse_cli", SimpleNamespace(gateway=gateway))
    sitecustomize.install()
    assert gateway.ROUTES["tasks.run"] == {
        "method": "tasks.run",
        "http": "POST",
        "path": "/tasks/{job_id}/run",
    }
