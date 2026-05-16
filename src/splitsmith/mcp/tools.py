"""Read-only MCP tools for splitsmith (issue #211 layer 1).

Pure functions, easy to unit-test. The FastMCP server in
:mod:`splitsmith.mcp.server` registers these as tools via the
high-level decorator API; tests call them directly to exercise the
business logic without booting a server.

Tool conventions:

* First argument is always either a path (``project_root``,
  ``directory``) or a video ID. No mutable state lives in this
  module.
* Returns are JSON-serialisable dicts / lists so the FastMCP layer
  can hand them to the wire format unchanged.
* Path arguments go through :mod:`.sandbox` so an opt-in
  ``SPLITSMITH_MCP_ALLOWED_ROOT`` constrains where the agent can
  read.
* Errors raise standard exceptions (``FileNotFoundError``,
  ``ValueError``, ``SandboxError``). FastMCP turns those into
  protocol-level errors with the message preserved.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import automation as automation_module
from .. import video_probe
from ..ui.project import MatchProject, StageEntry, StageVideo
from .sandbox import resolve_project_root, resolve_within_sandbox

# Video extensions the discovery tool surfaces. Mirrors the legacy
# default in :func:`splitsmith.cli._video_files` so the MCP-driven
# discover_videos and the CLI agree on what a "video" is.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v")


def probe_video(path: str) -> dict[str, Any]:
    """Run ffprobe against ``path`` and return its
    :class:`~splitsmith.video_probe.ProbeResult` as a plain dict.

    The agent uses this to verify a video is readable + to read
    duration / frame rate before assigning it to a stage.
    """
    resolved = resolve_within_sandbox(path, label="path")
    if not resolved.exists():
        raise FileNotFoundError(f"video not found: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"path is not a file: {resolved}")
    result = video_probe.probe(resolved)
    return result.model_dump(mode="json")


def discover_videos(directory: str, *, recursive: bool = False) -> list[dict[str, Any]]:
    """List video files under ``directory`` with size + mtime metadata.

    ``recursive=True`` walks the tree (depth-first). Hidden directories
    (those starting with ``.``) are skipped both at the top level and
    during recursive walks -- ``.git``, ``.cache``, ``.DS_Store``-like
    cruft would otherwise spam the result.

    Result rows are sorted by path for stable agent-side reasoning.
    """
    resolved = resolve_within_sandbox(directory, label="directory")
    if not resolved.is_dir():
        raise FileNotFoundError(f"directory not found: {resolved}")
    rows: list[dict[str, Any]] = []
    paths: Iterable[Path]
    if recursive:
        paths = (
            p
            for p in resolved.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not _is_hidden(p, resolved)
        )
    else:
        paths = (
            p
            for p in resolved.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not p.name.startswith(".")
        )
    for p in sorted(paths):
        try:
            stat = p.stat()
        except OSError:
            continue
        rows.append(
            {
                "path": str(p),
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return rows


def _is_hidden(path: Path, root: Path) -> bool:
    """Return True if any segment between ``root`` and ``path`` starts with '.'."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def get_project(project_root: str) -> dict[str, Any]:
    """Load the splitsmith project at ``project_root`` and return it.

    Mirror of ``GET /api/project`` in the HTTP server -- same shape,
    same defaults, same Pydantic serialisation. Lets an agent inspect
    the full project state (stages, videos, scoreboard, settings) in
    one round-trip before deciding what to do next.
    """
    resolved = resolve_project_root(project_root)
    project = MatchProject.load(resolved)
    return project.model_dump(mode="json")


def list_stages(project_root: str) -> list[dict[str, Any]]:
    """Compact per-stage summary -- the part of ``get_project`` an agent
    most often needs.

    Each row carries enough state for the agent to decide whether the
    stage needs work: stage number + name + scoreboard time, the
    primary video's path / role / beep state, and the export-readiness
    flags. Secondaries are not listed here -- ``get_project`` is the
    full surface when those matter.
    """
    resolved = resolve_project_root(project_root)
    project = MatchProject.load(resolved)
    return [_stage_summary(s) for s in sorted(project.stages, key=lambda s: s.stage_number)]


def _stage_summary(stage: StageEntry) -> dict[str, Any]:
    primary = next((v for v in stage.videos if v.role == "primary"), None)
    return {
        "stage_number": stage.stage_number,
        "stage_name": stage.stage_name,
        "time_seconds": stage.time_seconds,
        "secondary_count": sum(1 for v in stage.videos if v.role == "secondary"),
        "primary": _video_summary(primary) if primary is not None else None,
    }


def _video_summary(video: StageVideo) -> dict[str, Any]:
    return {
        "video_id": video.video_id,
        "path": str(video.path),
        "role": video.role,
        "processed": dict(video.processed),
        "beep_time": video.beep_time,
        "beep_source": video.beep_source,
        "beep_reviewed": video.beep_reviewed,
        "beep_confidence": video.beep_confidence,
        "beep_auto_detect_failed": video.beep_auto_detect_failed,
    }


def get_hitl_queue(project_root: str) -> dict[str, Any]:
    """Project-level work queue (issue #219). Same shape as
    ``GET /api/hitl-queue``: ``{items: [...], threshold: float}``.

    Computed via the canonical automation resolver so a project-level
    threshold override flows through to the agent's view of "what
    needs human attention" without requiring a separate read.
    """
    resolved = resolve_project_root(project_root)
    project = MatchProject.load(resolved)
    automation_resolved = automation_module.resolve_automation(
        project_override=project.automation,
    )
    threshold = automation_resolved.settings.beep_low_confidence_threshold
    items: list[dict[str, Any]] = []
    for stage in sorted(project.stages, key=lambda s: s.stage_number):
        primary = next((v for v in stage.videos if v.role == "primary"), None)
        if primary is None:
            continue
        if primary.beep_auto_detect_failed:
            items.append(
                {
                    "kind": "beep_missing",
                    "stage_number": stage.stage_number,
                    "video_id": primary.video_id,
                    "confidence": None,
                    "suggested_action": (
                        "Set the beep manually on the waveform: open the "
                        "stage's ingest panel and click the beep marker."
                    ),
                }
            )
            continue
        if primary.beep_source == "auto" and primary.beep_time is not None and not primary.beep_reviewed:
            items.append(
                {
                    "kind": "beep_low_confidence",
                    "stage_number": stage.stage_number,
                    "video_id": primary.video_id,
                    "confidence": primary.beep_confidence,
                    "suggested_action": (
                        "Listen to the ranked candidates and pick the "
                        "correct beep, or nudge the timestamp on the "
                        "waveform."
                    ),
                }
            )
    return {"items": items, "threshold": threshold}
