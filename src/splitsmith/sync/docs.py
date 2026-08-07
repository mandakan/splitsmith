"""Doc sanitization for the desktop-to-hosted sync push (#631).

A shooter's ``project.json`` carries filesystem-absolute path overrides
(``raw_dir``, ``trimmed_dir``, ...) that only make sense on the desktop
machine that wrote them - pushing them verbatim would let a hosted mirror
point at paths that don't exist on the server. :func:`sanitize_project_doc`
strips them before the doc is upserted.

Separately, an individual :class:`~splitsmith.ui.project.StageVideo` can
carry an absolute ``path`` (the user pointed the ingest screen at a raw
file outside the project tree). That's not sanitizable the way the
project-level dir overrides are: hosted streaming's presign branch
requires a relative path, and ``StageVideo.video_id`` is a hash of the
path itself, so rewriting it would silently mint a new identity for a
video the operator has already reviewed. Per CLAUDE.md's "default to the
conservative choice," we never rewrite it - :func:`absolute_path_videos`
just surfaces every offending (stage_number, path) so the planner can
fail the push with a clear message instead of shipping a document the
hosted side can't stream.
"""

from __future__ import annotations

from ..ui.project import MatchProject

#: Project-level fields that are filesystem-absolute (or absolute-capable)
#: path overrides, meaningless once pushed to a hosted mirror.
STRIPPED_PROJECT_FIELDS = (
    "raw_dir",
    "audio_dir",
    "trimmed_dir",
    "exports_dir",
    "probes_dir",
    "thumbs_dir",
    "last_scanned_dir",
)


def sanitize_project_doc(doc: dict) -> dict:
    """Return a copy of a project doc with :data:`STRIPPED_PROJECT_FIELDS` removed."""
    return {k: v for k, v in doc.items() if k not in STRIPPED_PROJECT_FIELDS}


def absolute_path_videos(project: MatchProject) -> list[tuple[int, str]]:
    """(stage_number, path) for every StageVideo whose path is absolute - unsyncable."""
    out: list[tuple[int, str]] = []
    for stage in project.stages:
        for video in stage.videos:
            if video.path.is_absolute():
                out.append((stage.stage_number, str(video.path)))
    return out
