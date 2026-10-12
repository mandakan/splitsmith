"""Will this match play on hosted? (spec 2026-10-12 s5)

One row per stage video: ``playable`` when its web rendition is valid and the
last push recorded it at this size, ``healing`` when the next sync rebuilds or
uploads what is missing, ``blocked`` when no sync can (an original not on this
disk, a beep nobody confirmed). Reads files and ``sync_state.json`` only; asks
hosted nothing, so "playable" means "pushed as it is now".
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject, StageEntry, StageVideo
from ..media_check import valid_mp4_cached
from ..trim import rendition_ok, web_trim_path
from .plan import _remote_key
from .reconcile import present_sources_for
from .state import SyncedItem, load_sync_state

MediaState = Literal["playable", "healing", "blocked"]


class StageVideoMedia(BaseModel):
    slug: str
    stage_number: int
    video: str
    state: MediaState
    reason: str | None = None


def _video_state(
    *,
    stage: StageEntry,
    video: StageVideo,
    trim: Path,
    source_present: bool,
    uploaded: dict[str, SyncedItem],
    remote_key: str,
) -> tuple[MediaState, str | None]:
    if not video.beep_reviewed or video.beep_time is None:
        return "blocked", "beep not confirmed"
    if stage.time_seconds <= 0:
        return "blocked", "no stage time"
    if not valid_mp4_cached(trim).ok:
        if source_present:
            return "healing", "trim missing: re-trimmed on the next sync"
        return "blocked", f"original not on this disk: {video.path.name}"
    web = web_trim_path(trim)
    if not rendition_ok(trim):
        what = "invalid" if web.exists() else "missing"
        return "healing", f"web rendition {what}: re-cut on the next sync"
    item = uploaded.get(remote_key)
    if item is None or item.size != web.stat().st_size:
        return "healing", "valid locally, uploads on the next sync"
    return "playable", None


def verify_match_media(match_root: Path) -> list[StageVideoMedia]:
    from ..ui.audio import trimmed_video_path

    match, shooter_roots = load_match_or_legacy(match_root)
    projects = {slug: MatchProject.load(shooter_roots[slug]) for slug in match.shooters}
    present = present_sources_for(match_root, projects)
    uploaded = load_sync_state(match_root).items
    rows: list[StageVideoMedia] = []
    for slug, project in projects.items():
        root = shooter_roots[slug]
        for stage in project.stages:
            for video in stage.videos:
                trim = trimmed_video_path(root, stage.stage_number, video, project=project)
                state, reason = _video_state(
                    stage=stage,
                    video=video,
                    trim=trim,
                    source_present=(slug, video.video_id) in present,
                    uploaded=uploaded,
                    remote_key=_remote_key(match.match_id, slug, web_trim_path(trim).name),
                )
                rows.append(
                    StageVideoMedia(
                        slug=slug,
                        stage_number=stage.stage_number,
                        video=video.path.name,
                        state=state,
                        reason=reason,
                    )
                )
    return rows
