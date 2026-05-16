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

from . import detect_tools, export_tools, tools, write_tools


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

    # ----------------------------------------------------------------
    # Detection tools (layer 3c). Synchronous -- the call returns when
    # the detector finishes. Beep detection takes ~5 s on a 100 s
    # primary clip; the agent waits inline. Shot detection (heavier;
    # CLAP / GBDT / PANN) lands in a future layer.
    # ----------------------------------------------------------------

    @mcp.tool()
    def detect_beep(
        project_root: str,
        stage_number: int,
        video_id: str,
        force: bool = False,
    ) -> dict:
        """Run beep detection against a stage's video; persist the
        result on the project.

        Returns the detected ``beep_time``, calibrated ``confidence``,
        and the ranked candidate list. Honours the
        ``automation.beep_low_confidence_threshold`` auto-trust gate
        (#219): detections at or above the threshold flip
        ``beep_reviewed=True`` so the downstream chain can fire;
        below it the beep lands in the HITL queue.

        Skips when the video already has a beep unless ``force=True``.
        Manual entries (``beep_source="manual"``) are NEVER auto-
        rerun -- the agent must clear them via ``set_beep_manual``
        with ``time_seconds=None`` first.
        """
        return detect_tools.detect_beep_for_video(
            project_root,
            stage_number=stage_number,
            video_id=video_id,
            force=force,
        )

    @mcp.tool()
    def detect_shots(
        project_root: str,
        stage_number: int,
        reset: bool = False,
    ) -> dict:
        """Run the 4-voter shot-detection ensemble on a stage.

        Mirror of ``POST /api/stages/{n}/shot-detect``. Loads the
        audit clip's audio, runs CLAP + GBDT + PANN consensus, and
        writes the candidate universe + seeded ``shots[]`` into
        ``<project>/audit/stage<N>.json``.

        Preconditions: stage has a primary, primary has
        ``beep_time``, stage has ``time_seconds > 0``.

        First call in this server lifetime loads the ensemble
        runtime (~5 s for CLAP + GBDT + PANN, plus first-call
        download if not yet cached locally). Subsequent calls
        reuse the cached weights. A typical 60 s stage finishes in
        20-40 s on CPU. Voter E (CLIP, ~600 MB) requires
        ``SPLITSMITH_ENABLE_VOTER_E=1``.

        ``reset=True`` wipes the existing ``shots[]`` before
        seeding; default preserves curated lists.
        """
        return detect_tools.detect_shots_for_stage(
            project_root,
            stage_number=stage_number,
            reset=reset,
        )

    # ----------------------------------------------------------------
    # Export tools (layer 3e). Templates are read-only; export_stage
    # writes per-stage artefacts; export_match stitches them.
    # ----------------------------------------------------------------

    @mcp.tool()
    def list_templates(user_dir: str | None = None) -> list[dict]:
        """List the export-template catalogue.

        Returns merged builtin + user templates (user wins on id
        collision). ``user_dir`` defaults to
        ``~/.splitsmith/templates``. Each row carries the resolved
        ``settings`` dict the agent forwards to ``export_stage`` /
        ``export_match`` -- fields the template didn't set come back
        as ``None`` so agent code can fall back to tool defaults.
        """
        return export_tools.list_templates_tool(user_dir=user_dir)

    @mcp.tool()
    def export_stage(
        project_root: str,
        stage_number: int,
        write_trim: bool = True,
        write_csv: bool = True,
        write_fcpxml: bool = True,
        write_report: bool = True,
        write_overlay: bool = False,
        overlay_codec: str = "auto",
        overlay_max_height: int | None = None,
        overlay_max_fps: float | None = None,
        overlay_theme: str = "splitsmith",
    ) -> dict:
        """Run a single stage's export.

        Writes (subject to flags): the lossless trim under
        ``<project>/exports/stage<N>_<slug>_trimmed.mp4``, splits CSV,
        FCPXML, report, and optional overlay MOV. Preconditions:
        primary has ``beep_time``; ``audit/stage<N>.json`` exists
        with at least one shot (run ``detect_shots`` first).
        """
        return export_tools.export_stage_tool(
            project_root,
            stage_number=stage_number,
            write_trim=write_trim,
            write_csv=write_csv,
            write_fcpxml=write_fcpxml,
            write_report=write_report,
            write_overlay=write_overlay,
            overlay_codec=overlay_codec,  # type: ignore[arg-type]
            overlay_max_height=overlay_max_height,
            overlay_max_fps=overlay_max_fps,
            overlay_theme=overlay_theme,  # type: ignore[arg-type]
        )

    @mcp.tool()
    def export_match(
        project_root: str,
        stage_numbers: list[int],
        head_pad_seconds: float = 5.0,
        tail_pad_seconds: float = 5.0,
        include_secondaries: bool = True,
        include_overlay: bool = True,
        project_name: str | None = None,
        pip_layout: str = "stacked",
        output_format: str = "fcpxml",
        transition_kind: str = "none",
        transition_duration_seconds: float = 0.5,
        title_kind: str = "none",
        title_duration_seconds: float = 1.5,
        intro_path: str | None = None,
        outro_path: str | None = None,
        youtube_sidecar: bool = False,
        youtube_preset: bool = False,
    ) -> dict:
        """Stitch N stages into one match-level export (FCPXML / FCP7
        / MP4) plus optional YouTube sidecar.

        Mirror of ``POST /api/match/export``. Composes from each
        stage's already-written per-stage export -- run
        ``export_stage`` for every stage first; this tool errors when
        any required artefact is missing rather than auto-building
        them inline (the explicit-step shape matches the rest of the
        MCP surface and keeps timing predictable).
        """
        return export_tools.export_match_tool(
            project_root,
            stage_numbers=stage_numbers,
            head_pad_seconds=head_pad_seconds,
            tail_pad_seconds=tail_pad_seconds,
            include_secondaries=include_secondaries,
            include_overlay=include_overlay,
            project_name=project_name,
            pip_layout=pip_layout,  # type: ignore[arg-type]
            output_format=output_format,  # type: ignore[arg-type]
            transition_kind=transition_kind,  # type: ignore[arg-type]
            transition_duration_seconds=transition_duration_seconds,
            title_kind=title_kind,  # type: ignore[arg-type]
            title_duration_seconds=title_duration_seconds,
            intro_path=intro_path,
            outro_path=outro_path,
            youtube_sidecar=youtube_sidecar,
            youtube_preset=youtube_preset,
        )

    @mcp.tool()
    def trim_audit_clip(
        project_root: str,
        stage_number: int,
        video_id: str | None = None,
    ) -> dict:
        """Build (or return cached) the audit-mode short-GOP trim
        for a stage's video.

        ``video_id=None`` targets the stage's primary; pass an
        explicit ID to trim a secondary. The trim window is
        anchored to the chosen video's ``beep_time`` and spans
        ``[max(0, beep - pre_buffer), beep + stage_time +
        post_buffer]``.

        Idempotent: returns the cached output when source mtime +
        trim params match. Re-runs ffmpeg transparently on a
        params mismatch (beep moved, buffer settings changed) so
        the agent doesn't have to invalidate first. Sets
        ``processed["trim"] = True`` on the video and saves the
        project.

        Preconditions: video has ``beep_time``, stage has
        ``time_seconds > 0``, source video exists on disk.
        """
        return detect_tools.trim_audit_clip(
            project_root,
            stage_number=stage_number,
            video_id=video_id,
        )

    return mcp
