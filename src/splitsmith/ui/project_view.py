"""The project payload as the SPA sees it.

``GET /api/shooters/{slug}/project`` and the move route answer the stored
``MatchProject`` plus fields computed per request: each stage's status and
split figures, each video's proxy, trim and scrub state, and the match's
origin and capabilities. The models here describe that wire shape for the
OpenAPI document (``pnpm gen:api`` turns it into the SPA's ``MatchProject``,
``StageEntry`` and ``StageVideo``); :func:`build_project_view` is the one
place the fields are added.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .. import events as events_module
from ..audit_data import audit_shots_to_engine_shots
from ..coach import statistic_splits
from ..fixture_schema import CameraMount
from ..match_project import MatchProject, StageEntry, StageStatus, StageVideo
from ..share_card import stage_figures
from . import server as _server
from .capabilities import MatchCapability, MatchOrigin
from .context import current_match_origin, current_share_request
from .presence import StoragePresence
from .state import AppState


class StageFiguresView(BaseModel):
    """Per-stage splits summary (UX PR 4). The split rule is
    ``coach.statistic_splits``; ``share_card.stage_figures`` shapes draw and
    average, the region figures add the breakdown counts."""

    draw: float | None
    avg_split: float | None
    fastest_split: float | None
    shot_count: int
    split_count: int
    moving_shots: int | None = None
    reloads: int | None = None
    reload_avg_s: float | None = None
    exposed_reload_s: float | None = None
    regions: int | None = None


class StageVideoView(StageVideo):
    """A video as the SPA reads it. An unassigned video has no stage to trim
    for, so its trim and scrub versions are ``None``; the shape is one type
    on purpose, since the client moves videos between the two lists."""

    proxy_ready: bool
    trim_version: str | None
    scrub_version: str | None
    # The stored field is a plain string; on the wire it is one of the mounts.
    camera_mount: CameraMount | None = None  # type: ignore[assignment]


class StageEntryView(StageEntry):
    status: StageStatus | None
    figures: StageFiguresView | None
    videos: list[StageVideoView]  # type: ignore[assignment]  # narrowed for the schema


class ProjectView(MatchProject):
    stages: list[StageEntryView]  # type: ignore[assignment]
    unassigned_videos: list[StageVideoView]  # type: ignore[assignment]
    origin: MatchOrigin
    capabilities: list[MatchCapability]


def stage_figures_payload(doc: dict | None, project: MatchProject, stage_number: int) -> dict | None:
    """Per-stage splits summary for the project payload (UX PR 4). The
    split rule is ``coach.statistic_splits``; ``share_card.stage_figures``
    shapes draw + average, this adds the fastest split and the counts.
    ``None`` (not zeros) when the stage has no audit doc."""
    if doc is None:
        return None
    stage = next((s for s in project.stages if s.stage_number == stage_number), None)
    prim = stage.primary() if stage is not None else None
    beep = prim.beep_time if prim is not None and prim.beep_time is not None else 0.0
    shots = audit_shots_to_engine_shots(doc, beep_time_in_source=beep)
    fig = stage_figures(
        shots, regions=events_module.confirmed_from_doc(doc, log_context=f"stage {stage_number}")
    )
    splits = statistic_splits(shots) if shots else []
    return {
        "draw": fig.draw,
        "avg_split": fig.avg_split,
        "fastest_split": min(splits) if splits else None,
        "shot_count": len(shots),
        "split_count": fig.split_count,
        **_server._stage_region_figures(doc, stage_number),
    }


def build_project_view(state: AppState, slug: str, project: MatchProject) -> dict[str, Any]:
    """``project`` as the SPA reads it: the model dump with the per-stage,
    per-video and per-match fields :class:`ProjectView` describes."""
    root = state.shooter_root(slug)
    audit_docs = state.load_audit_docs(slug)
    statuses = project.stage_statuses(audit_docs=audit_docs)
    payload = project.model_dump(mode="json")
    storage = state.storage
    proxy_keys: set[str] = set()
    if storage is not None:
        proxy_keys = {obj.path for obj in storage.list("raw_proxy/")}
    presence = StoragePresence(storage) if storage is not None and storage.supports_presigned_get else None
    for stage_dict in payload.get("stages", []):
        n = stage_dict.get("stage_number")
        if n is None:
            continue
        status = statuses.get(int(n))
        stage_dict["status"] = status.value if status is not None else None
        stage_dict["figures"] = stage_figures_payload(audit_docs.get(int(n)), project, int(n))
        stage_videos = project.stage(int(n)).videos
        for video_dict, video in zip(stage_dict.get("videos", []), stage_videos, strict=True):
            video_dict["proxy_ready"] = _server._proxy_ready_for(
                storage, proxy_keys, str(video_dict.get("path", ""))
            )
            video_dict["trim_version"] = _server._trim_version_for(root, int(n), video, project)
            video_dict["scrub_version"] = (
                _server._hosted_scrub_version_for(presence, root, int(n), video, project)
                if presence is not None and not video.path.is_absolute()
                else _server._scrub_version_for(root, int(n), video, project)
            )
    for video_dict in payload.get("unassigned_videos", []):
        video_dict["proxy_ready"] = _server._proxy_ready_for(
            storage, proxy_keys, str(video_dict.get("path", ""))
        )
        video_dict["trim_version"] = None
        video_dict["scrub_version"] = None
    payload["origin"] = current_match_origin.get() or "local"
    payload["capabilities"] = _server._serialized_capabilities()
    if current_share_request.get():
        payload["last_scanned_dir"] = None
    return payload
