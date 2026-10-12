"""Rename caches cut under the pre-take-spec video id to the current keys.

The take-spec id change left trims, renditions, params and audit audio under
the old path-only id. The desktop read them through ``_legacy_cache_fallback``;
hosted reads only the current key, found no rendition and sent the player to
an original no sync uploads (spec 2026-10-12). Same bytes, new names, only
where the fallback itself would have used the old file (one registration of
the source in the project); recorded in ``auto_sync.json`` so it runs once.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from ..trim import web_trim_path
from ..ui.audio import _legacy_cache_fallback, trim_params_path, trimmed_video_path, video_audit_audio_path
from .auto_state import load_auto_prefs, update_auto_prefs

CACHE_SCHEMA = 1


class MigrationReport(BaseModel):
    renamed: list[str] = []
    ambiguous: list[str] = []


def _pairs(new_trim: Path, legacy_trim: Path, new_wav: Path, legacy_wav: Path) -> list[tuple[Path, Path]]:
    pairs = [
        (legacy_trim, new_trim),
        (web_trim_path(legacy_trim), web_trim_path(new_trim)),
        (trim_params_path(legacy_trim), trim_params_path(new_trim)),
        (legacy_wav, new_wav),
    ]
    for peaks in legacy_wav.parent.glob(f"{legacy_wav.stem}.peaks-*.json"):
        pairs.append((peaks, new_wav.with_name(peaks.name.replace(legacy_wav.stem, new_wav.stem, 1))))
    return pairs


def migrate_legacy_caches(match_root: Path) -> MigrationReport:
    report = MigrationReport()
    if load_auto_prefs(match_root).cache_schema >= CACHE_SCHEMA:
        return report
    match, shooter_roots = load_match_or_legacy(match_root)
    for slug in match.shooters:
        root = shooter_roots[slug]
        project = MatchProject.load(root)
        for stage in project.stages:
            for video in stage.videos:
                new_trim = trimmed_video_path(root, stage.stage_number, video, project=project)
                legacy_trim = new_trim.with_name(
                    f"stage{stage.stage_number}_cam_{video.legacy_video_id}_trimmed.mp4"
                )
                if new_trim.exists() or not legacy_trim.exists() or legacy_trim == new_trim:
                    continue
                label = f"{slug} stage {stage.stage_number} {video.path.name}"
                if _legacy_cache_fallback(project, video, new_trim, legacy_trim) is None:
                    report.ambiguous.append(label)
                    continue
                new_wav = video_audit_audio_path(root, stage.stage_number, video, project=project)
                legacy_wav = new_wav.with_name(
                    f"stage{stage.stage_number}_cam_{video.legacy_video_id}_audit.wav"
                )
                for old, new in _pairs(new_trim, legacy_trim, new_wav, legacy_wav):
                    if old.exists() and not new.exists():
                        old.rename(new)
                report.renamed.append(label)
    update_auto_prefs(match_root, lambda p: setattr(p, "cache_schema", CACHE_SCHEMA))
    return report
