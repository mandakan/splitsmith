"""Smoke test for the FastMCP server wiring (issue #211 layer 1).

Doesn't boot stdio; just instantiates the server, asks it for its
tool list, and confirms the read-only set is registered with the
right schemas. This catches breakage from the FastMCP API drifting
under us without dragging stdio / asyncio into the unit suite.
"""

from __future__ import annotations

import asyncio
import typing

import pytest

from splitsmith.mcp import create_server, export_tools


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

EXPORT_TOOLS = {
    "list_templates",
    "export_stage",
    "export_match",
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


def test_server_registers_export_tools() -> None:
    """Layer 3e adds the export pipeline tools."""
    names = _list_tool_names()
    assert EXPORT_TOOLS <= names


def test_server_tools_have_descriptions() -> None:
    """Every registered tool needs a description string. Without it
    the agent has no signal for when to call which tool, and the MCP
    client's auto-discovery falls back to the tool name only."""
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"


def _literal_params(fn) -> dict[str, tuple[str, ...]]:
    """The ``Literal``-typed parameters of an ``export_tools`` entry point."""
    return {
        name: typing.get_args(hint)
        for name, hint in typing.get_type_hints(fn).items()
        if typing.get_origin(hint) is typing.Literal
    }


CONSTRAINED_PARAMS = [
    ("export_stage", name, values) for name, values in _literal_params(export_tools.export_stage_tool).items()
] + [
    ("export_match", name, values) for name, values in _literal_params(export_tools.export_match_tool).items()
]


@pytest.mark.parametrize(("tool_name", "param", "expected"), CONSTRAINED_PARAMS)
def test_a_closed_set_reaches_the_agent_as_a_closed_set(
    tool_name: str, param: str, expected: tuple[str, ...]
) -> None:
    """A ``Literal`` in ``export_tools`` must not widen to ``str`` at the tool.

    The MCP wrappers are the only description an agent ever sees. Typing
    one of these as a bare ``str`` tells the agent any string will do, so
    it invents plausible-but-wrong values (``h264`` for an overlay codec)
    and the failure surfaces deep in the export instead of at the call.
    The tool schema is where the constraint has to be legible.

    Driven off ``export_tools``' own annotations so the two cannot drift:
    adding a value there without re-exposing it here fails this.
    """
    tools = asyncio.run(create_server().list_tools())
    schema = next(t for t in tools if t.name == tool_name).inputSchema
    prop = schema["properties"].get(param)

    assert prop is not None, f"{tool_name} does not expose {param}"
    assert prop.get("enum") == list(expected), prop


def test_server_has_no_unexpected_tools() -> None:
    """Bump this set when a new layer adds tools -- silent extension
    would skip the design conversation about the new surface."""
    expected = READ_ONLY_TOOLS | WRITE_TOOLS | DETECT_TOOLS | EXPORT_TOOLS
    assert _list_tool_names() == expected
