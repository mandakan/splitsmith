"""Smoke test for the FastMCP server wiring (issue #211 layer 1).

Doesn't boot stdio; just instantiates the server, asks it for its
tool list, and confirms the read-only set is registered with the
right schemas. This catches breakage from the FastMCP API drifting
under us without dragging stdio / asyncio into the unit suite.
"""

from __future__ import annotations

import asyncio

from splitsmith.mcp import create_server


def _list_tool_names() -> set[str]:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    return {t.name for t in tools}


READ_ONLY_TOOLS = {
    "probe_video",
    "discover_videos",
    "get_project",
    "list_stages",
    "get_hitl_queue",
}

WRITE_TOOLS = {
    "assign_video",
    "set_beep_manual",
    "select_beep_candidate",
    "mark_beep_reviewed",
}

DETECT_TOOLS = {
    "detect_beep",
    "detect_shots",
    "trim_audit_clip",
}


def test_server_registers_read_only_tools() -> None:
    names = _list_tool_names()
    assert READ_ONLY_TOOLS <= names


def test_server_registers_write_tools() -> None:
    """Layer 3b adds the four mutating tools alongside the read-only set."""
    names = _list_tool_names()
    assert WRITE_TOOLS <= names


def test_server_registers_detect_tools() -> None:
    """Layer 3c adds detection tools (just detect_beep for now)."""
    names = _list_tool_names()
    assert DETECT_TOOLS <= names


def test_server_tools_have_descriptions() -> None:
    """Every registered tool needs a description string. Without it
    the agent has no signal for when to call which tool, and the MCP
    client's auto-discovery falls back to the tool name only."""
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def test_server_has_no_unexpected_tools() -> None:
    """Bump this set when a new layer adds tools -- silent extension
    would skip the design conversation about the new surface."""
    expected = READ_ONLY_TOOLS | WRITE_TOOLS | DETECT_TOOLS
    assert _list_tool_names() == expected
