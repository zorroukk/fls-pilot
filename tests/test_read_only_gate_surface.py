from __future__ import annotations

import asyncio

from fl_studio_mcp.server import build_server

READ_ONLY_GATE_TOOLS = {
    "fl_ping",
    "fl_get_play_state",
    "fl_get_tempo",
    "fl_get_project_state",
}


def _tools_by_name() -> dict:
    return {tool.name: tool for tool in asyncio.run(build_server().list_tools())}


def test_specialized_read_only_gate_tools_are_public() -> None:
    tools = _tools_by_name()

    assert tools.keys() >= READ_ONLY_GATE_TOOLS
    for name in READ_ONLY_GATE_TOOLS:
        tool = tools[name]
        assert (tool.meta or {}).get("safetyClass") == "read-only"
        assert tool.annotations.read_only_hint is True


def test_adjacent_transport_mutators_remain_retired() -> None:
    names = _tools_by_name().keys()

    assert {
        "fl_set_tempo",
        "fl_play",
        "fl_stop",
        "fl_toggle_play",
        "fl_record",
        "fl_set_song_position",
        "fl_set_time_signature",
    }.isdisjoint(names)
