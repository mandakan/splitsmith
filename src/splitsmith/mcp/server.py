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

from . import tools, write_tools


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

    # ----------------------------------------------------------------
    # Mutating tools (layer 3b). Each one loads the project, applies
    # a focused change, and saves. No background-job triggering --
    # that's the HTTP daemon's job runner or a future detection tool.
    # ----------------------------------------------------------------

    @mcp.tool()
    def assign_video(
        project_root: str,
        video_path: str,
        stage_number: int | None = None,
        role: str = "secondary",
    ) -> dict:
        """Assign a registered video to a stage (or back to unassigned).

        Equivalent to ``POST /api/assignments/move``. ``stage_number=
        None`` returns the video to the unassigned tray; otherwise
        ``role`` is one of ``primary | secondary | ignored``. A
        ``primary`` assignment demotes any existing primary on the
        target stage. A ``secondary`` assignment to a stage with no
        primary yet auto-upgrades to primary (matches the SPA's
        first-video-on-stage semantics).

        Returns ``{video_id, role, stage_number}`` so the agent can
        reference the placed video without re-reading the project.
        """
        return write_tools.assign_video(
            project_root,
            video_path,
            stage_number=stage_number,
            role=role,
        )

    @mcp.tool()
    def set_beep_manual(
        project_root: str,
        stage_number: int,
        video_id: str,
        time_seconds: float | None,
    ) -> dict:
        """Manually pin (or clear) a video's beep timestamp.

        ``time_seconds=None`` clears any existing beep. Otherwise the
        value is stored with ``beep_source="manual"`` and confidence
        pinned at 1.0 -- the auto-trust gate (#219) opens
        immediately. Cached audit trim is invalidated either way.
        """
        return write_tools.set_beep_manual(
            project_root,
            stage_number=stage_number,
            video_id=video_id,
            time_seconds=time_seconds,
        )

    @mcp.tool()
    def select_beep_candidate(
        project_root: str,
        stage_number: int,
        video_id: str,
        time_seconds: float,
    ) -> dict:
        """Promote one of ``video.beep_candidates`` (matched within 1
        ms of ``time_seconds``) as the authoritative beep.

        Mirror of ``POST /api/stages/{n}/videos/{vid}/beep/select``.
        Keeps ``beep_source="auto"`` since the time still came from
        the detector; resets ``beep_reviewed`` so the new pick needs
        its own confirmation. Audit trim cache is invalidated.
        """
        return write_tools.select_beep_candidate(
            project_root,
            stage_number=stage_number,
            video_id=video_id,
            time_seconds=time_seconds,
        )

    @mcp.tool()
    def mark_beep_reviewed(
        project_root: str,
        stage_number: int,
        video_id: str,
        reviewed: bool = True,
    ) -> dict:
        """Flip ``video.beep_reviewed`` (issue #71).

        Setting True requires ``beep_time`` to be present. The
        downstream chain (auto-trim + shot-detect when
        ``automation.shot_detect_on_beep_verified`` is on) fires on
        the HTTP server's job runner -- this tool only flips the
        flag.
        """
        return write_tools.mark_beep_reviewed(
            project_root,
            stage_number=stage_number,
            video_id=video_id,
            reviewed=reviewed,
        )

    return mcp
