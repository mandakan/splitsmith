"""The bulk trim-cache rebuild covers every angle, not just the primary (#351).

``POST /api/match/shooters/{slug}/build-trim-caches`` and the
``stages_missing_trim`` count that gates its button both walked
``stage.primary()`` only. But the audit-mode short-GOP cache is keyed per
``video_id`` for *every* role -- Audit plays secondaries alongside the
primary, synced by their own beep offsets -- so a secondary whose cache
was never built (or was reclaimed by the cleanup flow) had no rebuild
path at all. It degrades rather than breaks: ``stream_video?kind=auto``
falls back to the source, which in hosted mode means scrubbing a
multi-hundred-MB raw file over presigned ranged reads instead of a short
clip.

These tests pin the per-video contract. Ignored videos stay out, and a
stage with no primary is still skipped whole -- it isn't auditable, so
rebuilding its angles buys nothing.

Four of the seven fail against the pre-change code (the secondary is
absent from ``jobs_submitted`` and ``skipped`` alike, and the count reads
0). The last three -- the primary still queues, ignored angles stay out,
a primary-less stage skips whole -- pass before and after by design:
they are regression guards on ``de9cc1f``'s behaviour, not evidence of
the fix.
"""

from pathlib import Path
from typing import Any

from splitsmith.ui.project import MatchProject

from .test_ui_server import _seed_match_export_project


def _add_video(
    project_root: Path,
    stage_number: int,
    *,
    role: str,
    name: str,
    beep_time: float | None = 5.0,
) -> str:
    """Register ``name`` on ``stage_number`` in the seeded shooter and
    return its ``video_id``."""
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    src = shooter_root / "raw" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00")
    registered = project.register_video(src, project_root)
    project.assign_video(registered.path, to_stage_number=stage_number, role=role)
    added = next(v for v in project.stage(stage_number).videos if v.path == registered.path)
    added.beep_time = beep_time
    project.save(shooter_root)
    return added.video_id


def _write_cache(project_root: Path, stage_number: int, video_id: str) -> None:
    """Put a non-empty audit trim on disk for ``video_id``."""
    trimmed = project_root / "shooters" / "me" / "trimmed"
    trimmed.mkdir(parents=True, exist_ok=True)
    (trimmed / f"stage{stage_number}_cam_{video_id}_trimmed.mp4").write_bytes(b"MP4")


def _seed(tmp_path: Path, *, stage_count: int = 1) -> tuple[Any, Path]:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=stage_count)
    # The trim body would shell out to ffmpeg; the queue mechanics are what
    # these tests are about.
    client.app.state.splitsmith_state.job_bodies.register("trim", lambda handle, **args: None)
    return client, project_root


def _rebuild(client: Any) -> dict[str, Any]:
    resp = client.post("/api/match/shooters/me/build-trim-caches")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _queued_video_ids(body: dict[str, Any]) -> set[str]:
    return {job["video_id"] for job in body["jobs_submitted"]}


def _skip_reason(body: dict[str, Any], video_id: str) -> str | None:
    for entry in body["skipped"]:
        if entry.get("video_id") == video_id:
            return entry["reason"]
    return None


def test_rebuild_queues_a_job_for_a_secondary_with_a_missing_cache(tmp_path: Path) -> None:
    """The gap this issue closes: a secondary angle Audit will play, with
    no cache and previously no way to build one from the UI."""
    client, project_root = _seed(tmp_path)
    secondary_id = _add_video(project_root, 1, role="secondary", name="VID1_B.mp4")

    body = _rebuild(client)

    assert secondary_id in _queued_video_ids(body)


def test_stages_missing_trim_counts_a_stage_whose_only_gap_is_a_secondary(tmp_path: Path) -> None:
    """The button is hidden at count 0, so an uncounted stage is an
    unreachable rebuild however well the endpoint behaves."""
    client, project_root = _seed(tmp_path)
    primary_id = _add_video(project_root, 1, role="secondary", name="VID1_B.mp4")
    # Cache the primary so the secondary is the stage's only remaining gap.
    project = MatchProject.load(project_root / "shooters" / "me")
    primary = project.stage(1).primary()
    assert primary is not None
    assert primary.video_id != primary_id
    _write_cache(project_root, 1, primary.video_id)

    listing = client.get("/api/match/shooters").json()["shooters"][0]

    assert listing["stages_missing_trim"] == 1


def test_rebuild_reports_an_already_cached_secondary_as_skipped(tmp_path: Path) -> None:
    """A cached angle must be visible as a considered-and-skipped angle,
    not silently absent from both lists the way it was before."""
    client, project_root = _seed(tmp_path)
    secondary_id = _add_video(project_root, 1, role="secondary", name="VID1_B.mp4")
    _write_cache(project_root, 1, secondary_id)

    body = _rebuild(client)

    assert secondary_id not in _queued_video_ids(body)
    assert _skip_reason(body, secondary_id) == "already_cached"


def test_rebuild_skips_a_secondary_that_has_no_beep(tmp_path: Path) -> None:
    """Same window rule as the per-video trim endpoint: no beep, no trim."""
    client, project_root = _seed(tmp_path)
    secondary_id = _add_video(project_root, 1, role="secondary", name="VID1_B.mp4", beep_time=None)

    body = _rebuild(client)

    assert secondary_id not in _queued_video_ids(body)
    assert _skip_reason(body, secondary_id) == "no_beep"


def test_rebuild_leaves_ignored_videos_alone(tmp_path: Path) -> None:
    """``ignored`` is the operator saying this angle is not part of the
    take; it has no audit surface, so it gets no job."""
    client, project_root = _seed(tmp_path)
    ignored_id = _add_video(project_root, 1, role="ignored", name="VID1_C.mp4")

    body = _rebuild(client)

    assert ignored_id not in _queued_video_ids(body)
    assert _skip_reason(body, ignored_id) is None


def test_rebuild_still_queues_the_primary(tmp_path: Path) -> None:
    """Regression guard on the behaviour that shipped in de9cc1f."""
    client, project_root = _seed(tmp_path)
    project = MatchProject.load(project_root / "shooters" / "me")
    primary = project.stage(1).primary()
    assert primary is not None

    body = _rebuild(client)

    assert _queued_video_ids(body) == {primary.video_id}


def test_rebuild_skips_a_stage_with_no_primary_whole(tmp_path: Path) -> None:
    """A stage without a primary is not auditable, so its other angles
    are not worth an encode -- the pre-change stage-level skip stands."""
    client, project_root = _seed(tmp_path)
    secondary_id = _add_video(project_root, 1, role="secondary", name="VID1_B.mp4")
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    stage = project.stage(1)
    stage.videos = [v for v in stage.videos if v.role != "primary"]
    project.save(shooter_root)

    body = _rebuild(client)

    assert body["jobs_submitted"] == []
    assert {"stage": 1, "reason": "no_primary"} in body["skipped"]
    assert _skip_reason(body, secondary_id) is None
