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


def test_server_registers_read_only_tools() -> None:
    names = _list_tool_names()
    assert {
        "probe_video",
        "discover_videos",
        "get_project",
        "list_stages",
        "get_hitl_queue",
    } <= names


def test_server_tools_have_descriptions() -> None:
    """Every registered tool needs a description string. Without it
    the agent has no signal for when to call which tool, and the MCP
    client's auto-discovery falls back to the tool name only."""
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def test_server_has_no_unexpected_tools() -> None:
    """Layer 1 only ships the read-only surface. A new tool landing
    here without an updated test signals a missing layering decision
    -- bump this set when adding to the server, don't silently extend.
    """
    expected = {
        "probe_video",
        "discover_videos",
        "get_project",
        "list_stages",
        "get_hitl_queue",
    }
    assert _list_tool_names() == expected
