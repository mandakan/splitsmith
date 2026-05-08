"""FastMCP server registering splitsmith's read-only tools.

The tool implementations live in :mod:`.tools` -- this file is just
the wiring layer. Two reasons to keep them split:

1. Unit tests can call the pure functions directly without spinning
   up an asyncio event loop / stdio transport.
2. Future MCP layers (write tools, detection orchestration) will
   add more registrations here without growing the implementation
   module unboundedly.

Run via ``python -m splitsmith.mcp`` or ``splitsmith mcp``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools


def create_server(name: str = "splitsmith") -> FastMCP:
    """Build a :class:`FastMCP` instance with every tool registered.

    Returning the server lets tests instantiate it without booting
    the transport, and lets future code wrap it with custom
    middleware / lifespan hooks before ``.run()``.
    """
    mcp = FastMCP(name)

    @mcp.tool()
    def probe_video(path: str) -> dict:
        """Run ffprobe against ``path``; return duration, frame rate,
        codec, and resolution as a JSON object.

        Use this before assigning a video to a stage to confirm
        readability + read its metadata. ``path`` is resolved to an
        absolute filesystem path; an opt-in ``SPLITSMITH_MCP_ALLOWED_ROOT``
        env var sandboxes which paths are reachable.
        """
        return tools.probe_video(path)

    @mcp.tool()
    def discover_videos(directory: str, recursive: bool = False) -> list[dict]:
        """List video files (.mp4 / .mov / .m4v) under ``directory``.

        Returns ``{path, size_bytes, modified_at}`` rows sorted by path.
        Hidden directories (.git, .cache, .DS_Store-like) are skipped.
        Pass ``recursive=True`` to walk the tree; default is shallow.
        """
        return tools.discover_videos(directory, recursive=recursive)

    @mcp.tool()
    def get_project(project_root: str) -> dict:
        """Load the splitsmith project at ``project_root`` and return its
        full state.

        Same shape as the ``GET /api/project`` HTTP endpoint -- stages,
        videos, scoreboard, automation overrides. Use ``list_stages``
        for the lighter per-stage summary when you only need to decide
        which stage needs work next.
        """
        return tools.get_project(project_root)

    @mcp.tool()
    def list_stages(project_root: str) -> list[dict]:
        """Per-stage summary: stage number + name + time + primary
        video state + secondary count.

        Lightweight alternative to ``get_project`` for agent loops
        that just need to enumerate work. Each row carries the
        primary video's beep state (time, source, reviewed,
        confidence) so the agent can prioritise without a second
        round-trip.
        """
        return tools.list_stages(project_root)

    @mcp.tool()
    def get_hitl_queue(project_root: str) -> dict:
        """Project-level work queue (#219): beeps the auto-trust gate
        didn't clear. Each item includes a ``suggested_action`` string
        the agent can present verbatim to the user.

        Returns ``{items: [...], threshold: float}`` where the
        threshold is the resolved
        ``automation.beep_low_confidence_threshold`` (CLI > project >
        global > default) so a per-project override flows through
        without a separate read.
        """
        return tools.get_hitl_queue(project_root)

    return mcp
