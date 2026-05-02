"""Tests for the production UI's match-project model.

Covers:
- ``MatchProject.init`` creates the standard subdirectory layout
- Round-trip: write -> read -> deep equality
- Incremental write: adding a video to an existing project preserves prior state
- Atomic write: an interrupted save never leaves a corrupt project.json
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest

from splitsmith.ui.project import (
    PROJECT_FILE,
    SCHEMA_VERSION,
    SUBDIRS,
    MatchProject,
    StageEntry,
    StageVideo,
    atomic_write_json,
)


def test_init_creates_layout(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "match-a", name="Match A")

    assert project.name == "Match A"
    assert project.schema_version == SCHEMA_VERSION
    assert (tmp_path / "match-a" / PROJECT_FILE).exists()
    for sub in SUBDIRS:
        assert (tmp_path / "match-a" / sub).is_dir()


def test_init_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "match-b"
    first = MatchProject.init(root, name="Match B")
    first.competitor_name = "Test Shooter"
    first.save(root)

    # Re-init must NOT clobber existing state.
    second = MatchProject.init(root, name="Different Name")
    assert second.name == "Match B"
    assert second.competitor_name == "Test Shooter"


def test_round_trip_preserves_full_project(tmp_path: Path) -> None:
    root = tmp_path / "match-c"
    project = MatchProject.init(root, name="Round Trip Match")
    project.competitor_name = "Mathias"
    project.scoreboard_match_id = "ssi-12345"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Per told me to do it",
            time_seconds=14.74,
            videos=[
                StageVideo(
                    path=Path("raw/VID_001.mp4"),
                    role="primary",
                    beep_time=12.453,
                    processed={"beep": True, "shot_detect": True, "trim": True},
                ),
                StageVideo(
                    path=Path("raw/VID_002.mp4"),
                    role="secondary",
                    beep_time=12.501,
                    processed={"beep": True, "shot_detect": False, "trim": True},
                    notes="bay cam from friend, added 2026-05-02",
                ),
            ],
        ),
        StageEntry(
            stage_number=2,
            stage_name="Speedy",
            time_seconds=9.12,
            videos=[],
            skipped=True,
        ),
    ]
    project.save(root)

    reloaded = MatchProject.load(root)

    # ``updated_at`` is bumped on save, so compare on the dumped form rather
    # than direct equality of the live model. Drop ``updated_at`` for the
    # comparison since both sides will have it set after save.
    a = project.model_dump(mode="json")
    b = reloaded.model_dump(mode="json")
    a.pop("updated_at")
    b.pop("updated_at")
    assert a == b


def test_incremental_add_video_preserves_prior_state(tmp_path: Path) -> None:
    """The Sub 1 incremental-write test from issue #12.

    Programmatically add a ``StageVideo`` to an existing project's stage,
    write, re-read; the original state plus the new video must be preserved.
    """
    root = tmp_path / "match-incremental"
    project = MatchProject.init(root, name="Incremental Match")
    project.stages = [
        StageEntry(
            stage_number=3,
            stage_name="Stage Three",
            time_seconds=20.0,
            videos=[
                StageVideo(
                    path=Path("raw/headcam.mp4"),
                    role="primary",
                    beep_time=15.0,
                    processed={"beep": True, "shot_detect": True, "trim": True},
                )
            ],
        )
    ]
    project.save(root)

    # Day N: a friend sends bay-cam footage.
    later = MatchProject.load(root)
    later.stage(3).videos.append(
        StageVideo(
            path=Path("raw/baycam.mp4"),
            role="secondary",
            processed={"beep": False, "shot_detect": False, "trim": False},
        )
    )
    later.save(root)

    final = MatchProject.load(root)
    stage = final.stage(3)
    assert len(stage.videos) == 2
    primary = stage.primary()
    assert primary is not None
    assert primary.path == Path("raw/headcam.mp4")
    assert primary.beep_time == 15.0
    assert primary.processed["shot_detect"] is True
    secondary = stage.videos[1]
    assert secondary.role == "secondary"
    assert secondary.processed["shot_detect"] is False


def test_atomic_write_no_partial_file_on_crash(tmp_path: Path) -> None:
    """An exception mid-write must leave the destination either absent or a
    fully-valid earlier version -- never a half-written file."""
    target = tmp_path / "config.json"

    # Start with a valid file the writer would otherwise overwrite.
    target.write_text('{"version": 1}\n')

    class BoomError(RuntimeError):
        pass

    def serializer_that_blows_up(obj: object) -> object:
        raise BoomError("simulated failure mid-serialization")

    with pytest.raises(BoomError):
        # Force the json.dump path through ``default`` by passing an
        # unserializable object plus our exploding default.
        from splitsmith.ui import project as project_module

        original_default = project_module._json_default
        project_module._json_default = serializer_that_blows_up
        try:
            atomic_write_json(target, {"x": object()})
        finally:
            project_module._json_default = original_default

    # Original file untouched.
    assert json.loads(target.read_text()) == {"version": 1}
    # No leftover .tmp file in the directory.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".config.json.")]
    assert leftovers == []


def test_concurrent_saves_never_corrupt(tmp_path: Path) -> None:
    """Hammer the project with parallel saves; the file is always a valid JSON
    document corresponding to one of the writers' dumps."""
    root = tmp_path / "match-concurrent"
    MatchProject.init(root, name="Concurrent Match")

    def bump(thread_id: int) -> None:
        for i in range(20):
            p = MatchProject.load(root)
            p.competitor_name = f"thread-{thread_id}-iter-{i}"
            p.save(root)

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Final state: file is valid JSON, parses as a MatchProject.
    final = MatchProject.load(root)
    assert final.competitor_name is not None
    assert final.competitor_name.startswith("thread-")


def test_stage_lookup_raises_for_missing(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "match-d", name="Match D")
    with pytest.raises(KeyError):
        project.stage(99)


def test_default_video_is_secondary(tmp_path: Path) -> None:
    """First-time-added videos default to ``secondary``; primary designation is
    explicit (handled by the ingest screen, not the data model)."""
    v = StageVideo(path=Path("raw/v.mp4"))
    assert v.role == "secondary"
    assert v.processed == {"beep": False, "shot_detect": False, "trim": False}
    assert v.beep_time is None


def test_datetime_round_trips_as_iso8601(tmp_path: Path) -> None:
    """The on-disk JSON uses ISO-8601 for datetimes; round-trip must be lossless."""
    root = tmp_path / "match-dt"
    project = MatchProject.init(root, name="DT Match")
    project.save(root)

    raw = json.loads((root / PROJECT_FILE).read_text())
    # ISO-8601 is parseable.
    parsed = datetime.fromisoformat(raw["created_at"])
    assert parsed.tzinfo is not None  # we always store UTC


def test_project_file_ends_with_newline(tmp_path: Path) -> None:
    """File hygiene: a trailing newline keeps git diffs clean."""
    root = tmp_path / "match-nl"
    project = MatchProject.init(root, name="NL Match")
    project.save(root)
    contents = (root / PROJECT_FILE).read_text()
    assert contents.endswith("\n")


def test_import_scoreboard_populates_stages(tmp_path: Path) -> None:
    """Dropping an SSI Scoreboard JSON populates stages, match name, and competitor."""
    root = tmp_path / "match-import"
    project = MatchProject.init(root, name="placeholder")

    scoreboard = {
        "match": {"id": "27046", "ct": "22", "name": "Blacksmith Handgun Open 2026"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Sample Shooter",
                "division": "Production Optics",
                "club": "Sample IPSC Club",
                "stages": [
                    {
                        "stage_number": 2,  # intentionally out of order
                        "stage_name": "100m",
                        "time_seconds": 12.48,
                        "scorecard_updated_at": "2026-04-12T12:03:32.729554+00:00",
                    },
                    {
                        "stage_number": 1,
                        "stage_name": "K-vallen",
                        "time_seconds": 42.76,
                        "scorecard_updated_at": "2026-04-12T11:29:28.833034+00:00",
                    },
                ],
            }
        ],
    }
    project.import_scoreboard(scoreboard)

    assert project.name == "Blacksmith Handgun Open 2026"
    assert project.scoreboard_match_id == "27046"
    assert project.competitor_name == "Sample Shooter"
    # Stages sorted by stage_number.
    assert [s.stage_number for s in project.stages] == [1, 2]
    assert project.stages[0].stage_name == "K-vallen"
    assert project.stages[0].time_seconds == 42.76


def test_import_scoreboard_refuses_overwrite_by_default(tmp_path: Path) -> None:
    from splitsmith.ui.project import ScoreboardImportConflictError

    root = tmp_path / "match-conflict"
    project = MatchProject.init(root, name="x")
    sb1 = {
        "match": {"id": "1", "name": "First"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "A",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S1",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    project.import_scoreboard(sb1)

    sb2 = {
        "match": {"id": "2", "name": "Second"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "A",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S2-different",
                        "time_seconds": 20.0,
                        "scorecard_updated_at": "2026-02-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    with pytest.raises(ScoreboardImportConflictError):
        project.import_scoreboard(sb2)

    # Forced overwrite works.
    project.import_scoreboard(sb2, overwrite=True)
    assert project.stages[0].stage_name == "S2-different"


def test_init_placeholder_stages_creates_n_stages(tmp_path: Path) -> None:
    from datetime import date

    root = tmp_path / "match-placeholder"
    project = MatchProject.init(root, name="x")
    project.init_placeholder_stages(
        6,
        match_name="Tällmilan Pre-Match",
        match_date=date(2026, 4, 12),
    )

    assert project.name == "Tällmilan Pre-Match"
    assert project.match_date == date(2026, 4, 12)
    assert [s.stage_number for s in project.stages] == [1, 2, 3, 4, 5, 6]
    assert all(s.placeholder for s in project.stages)
    assert project.stages[0].stage_name == "Stage 1"
    assert project.stages[0].scorecard_updated_at is None


def test_init_placeholder_rejects_zero_or_negative(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "match", name="x")
    with pytest.raises(ValueError):
        project.init_placeholder_stages(0)


def test_init_placeholder_refuses_when_real_stages_exist(tmp_path: Path) -> None:
    from splitsmith.ui.project import ScoreboardImportConflictError

    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.import_scoreboard(
        {
            "match": {"id": "1", "name": "M"},
            "competitors": [
                {
                    "competitor_id": 1,
                    "name": "A",
                    "stages": [
                        {
                            "stage_number": 1,
                            "stage_name": "S1",
                            "time_seconds": 10.0,
                            "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                        }
                    ],
                }
            ],
        }
    )
    with pytest.raises(ScoreboardImportConflictError):
        project.init_placeholder_stages(5)


def test_init_placeholder_replaces_existing_placeholders_returning_videos(
    tmp_path: Path,
) -> None:
    """Re-bootstrapping with a different stage count moves existing placeholder
    videos back to unassigned so the user can re-bind."""
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.init_placeholder_stages(3)
    project.stages[0].videos.append(StageVideo(path=Path("raw/clip.mp4"), role="primary"))

    project.init_placeholder_stages(5)
    assert len(project.stages) == 5
    assert all(s.videos == [] for s in project.stages)
    assert len(project.unassigned_videos) == 1
    assert str(project.unassigned_videos[0].path) == "raw/clip.mp4"


def test_import_scoreboard_overlays_placeholders_preserving_videos(tmp_path: Path) -> None:
    """A scoreboard import on top of placeholders keeps assignments by stage_number."""
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.init_placeholder_stages(2)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v1.mp4"), role="primary"))
    project.stages[1].videos.append(StageVideo(path=Path("raw/v2.mp4"), role="primary"))

    project.import_scoreboard(
        {
            "match": {"id": "5", "name": "Real Match"},
            "competitors": [
                {
                    "competitor_id": 1,
                    "name": "A",
                    "stages": [
                        {
                            "stage_number": 1,
                            "stage_name": "Real S1",
                            "time_seconds": 11.0,
                            "scorecard_updated_at": "2026-04-01T00:00:00+00:00",
                        },
                        {
                            "stage_number": 2,
                            "stage_name": "Real S2",
                            "time_seconds": 22.0,
                            "scorecard_updated_at": "2026-04-01T00:00:00+00:00",
                        },
                    ],
                }
            ],
        }
    )
    assert project.name == "Real Match"
    assert all(not s.placeholder for s in project.stages)
    assert project.stages[0].stage_name == "Real S1"
    assert len(project.stages[0].videos) == 1
    assert str(project.stages[0].videos[0].path) == "raw/v1.mp4"
    assert project.stages[0].videos[0].role == "primary"
    assert len(project.stages[1].videos) == 1


def test_import_scoreboard_overlay_drops_extras_to_unassigned(tmp_path: Path) -> None:
    """When the scoreboard has fewer stages than placeholders, orphaned videos
    move to unassigned_videos -- we never silently lose user data."""
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.init_placeholder_stages(3)
    project.stages[2].videos.append(StageVideo(path=Path("raw/v3.mp4"), role="primary"))

    project.import_scoreboard(
        {
            "match": {"id": "5", "name": "M"},
            "competitors": [
                {
                    "competitor_id": 1,
                    "name": "A",
                    "stages": [
                        {
                            "stage_number": 1,
                            "stage_name": "S1",
                            "time_seconds": 10.0,
                            "scorecard_updated_at": "2026-04-01T00:00:00+00:00",
                        },
                        {
                            "stage_number": 2,
                            "stage_name": "S2",
                            "time_seconds": 20.0,
                            "scorecard_updated_at": "2026-04-01T00:00:00+00:00",
                        },
                    ],
                }
            ],
        }
    )
    assert len(project.stages) == 2
    assert len(project.unassigned_videos) == 1
    assert str(project.unassigned_videos[0].path) == "raw/v3.mp4"
    assert project.unassigned_videos[0].role == "secondary"


def test_remove_video_unassigned_returns_plan(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    src = tmp_path / "ext" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    video = project.register_video(src, root)

    plan = project.remove_video(video.path, root)

    assert project.find_video(video.path) is None
    assert plan.was_primary is False
    assert plan.stage_number is None
    assert plan.audio_cache_path is None
    assert plan.trimmed_cache_path is None
    assert plan.audit_path is None
    assert plan.raw_link_path == (root / "raw" / "clip.mp4")


def test_remove_primary_includes_audio_and_trimmed_paths(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.import_scoreboard(
        {
            "match": {"id": "1", "name": "M"},
            "competitors": [
                {
                    "competitor_id": 1,
                    "name": "A",
                    "stages": [
                        {
                            "stage_number": 1,
                            "stage_name": "S1",
                            "time_seconds": 10.0,
                            "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                        }
                    ],
                }
            ],
        }
    )
    src = tmp_path / "ext" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    video = project.register_video(src, root)
    project.assign_video(video.path, to_stage_number=1, role="primary")
    project.stages[0].videos[0].processed = {"beep": True, "shot_detect": True, "trim": True}

    plan = project.remove_video(video.path, root)

    assert plan.was_primary is True
    assert plan.stage_number == 1
    assert plan.audio_cache_path == (root / "audio" / "stage1_primary.wav")
    assert plan.trimmed_cache_path == (root / "trimmed" / "stage1_trimmed.mp4")
    assert plan.audit_path is None  # default: preserve audit
    assert plan.audit_reset is False
    assert project.stages[0].videos == []


def test_remove_with_reset_audit_includes_audit_path(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    project.import_scoreboard(
        {
            "match": {"id": "1", "name": "M"},
            "competitors": [
                {
                    "competitor_id": 1,
                    "name": "A",
                    "stages": [
                        {
                            "stage_number": 1,
                            "stage_name": "S1",
                            "time_seconds": 10.0,
                            "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                        }
                    ],
                }
            ],
        }
    )
    src = tmp_path / "ext" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    video = project.register_video(src, root)
    project.assign_video(video.path, to_stage_number=1, role="primary")

    plan = project.remove_video(video.path, root, reset_audit=True)

    assert plan.audit_reset is True
    assert plan.audit_path == (root / "audit" / "stage1.json")


def test_remove_video_unknown_path_raises(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="x")
    with pytest.raises(KeyError):
        project.remove_video(Path("raw/missing.mp4"), root)


def test_register_video_symlinks_into_raw(tmp_path: Path) -> None:
    """Registering a video creates a symlink in raw/ and adds to unassigned_videos."""
    root = tmp_path / "match-vid"
    project = MatchProject.init(root, name="Vid Match")

    source = tmp_path / "external" / "VID_001.mp4"
    source.parent.mkdir()
    source.write_bytes(b"\x00\x00\x00 ftypisom")  # any bytes; we don't probe in this test

    video = project.register_video(source, root)

    assert video.path == Path("raw") / "VID_001.mp4"
    assert video.role == "secondary"
    assert (root / "raw" / "VID_001.mp4").exists()
    # Symlink resolves back to the source.
    assert (root / "raw" / "VID_001.mp4").resolve() == source
    assert len(project.unassigned_videos) == 1


def test_register_video_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "match-idem"
    project = MatchProject.init(root, name="Idempotent Match")
    source = tmp_path / "external" / "VID_002.mp4"
    source.parent.mkdir()
    source.write_bytes(b"")

    a = project.register_video(source, root)
    b = project.register_video(source, root)
    assert a.path == b.path
    assert len(project.unassigned_videos) == 1


def test_register_video_rejects_non_video(tmp_path: Path) -> None:
    root = tmp_path / "match-bad"
    project = MatchProject.init(root, name="Bad Match")
    bad = tmp_path / "notes.txt"
    bad.write_text("not a video")
    with pytest.raises(ValueError):
        project.register_video(bad, root)


def test_assign_video_unassigned_to_stage_as_primary(tmp_path: Path) -> None:
    root = tmp_path / "match-assign"
    project = MatchProject.init(root, name="Assign Match")
    project.stages = [
        StageEntry(stage_number=1, stage_name="A", time_seconds=10.0),
    ]
    src = tmp_path / "VID.mp4"
    src.write_bytes(b"")
    video = project.register_video(src, root)

    project.assign_video(video.path, to_stage_number=1, role="primary")

    assert len(project.unassigned_videos) == 0
    assert len(project.stages[0].videos) == 1
    assert project.stages[0].videos[0].role == "primary"


def test_assign_video_demotes_existing_primary(tmp_path: Path) -> None:
    root = tmp_path / "match-demote"
    project = MatchProject.init(root, name="Demote Match")
    src1 = tmp_path / "VID_a.mp4"
    src2 = tmp_path / "VID_b.mp4"
    src1.write_bytes(b"")
    src2.write_bytes(b"")
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    v1 = project.register_video(src1, root)
    v2 = project.register_video(src2, root)
    project.assign_video(v1.path, to_stage_number=1, role="primary")
    project.assign_video(v2.path, to_stage_number=1, role="primary")

    stage = project.stages[0]
    assert len(stage.videos) == 2
    primaries = [v for v in stage.videos if v.role == "primary"]
    secondaries = [v for v in stage.videos if v.role == "secondary"]
    assert len(primaries) == 1
    assert len(secondaries) == 1
    # The newest assignment wins.
    assert primaries[0].path == v2.path


def test_assign_video_back_to_unassigned(tmp_path: Path) -> None:
    root = tmp_path / "match-back"
    project = MatchProject.init(root, name="Back Match")
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    src = tmp_path / "VID.mp4"
    src.write_bytes(b"")
    v = project.register_video(src, root)
    project.assign_video(v.path, to_stage_number=1, role="primary")
    project.assign_video(v.path, to_stage_number=None)

    assert project.unassigned_videos[0].path == v.path
    assert project.stages[0].videos == []


def test_auto_match_returns_suggestions_without_mutation(tmp_path: Path) -> None:
    """auto_match runs the heuristic and returns suggestions; project is unchanged."""
    from datetime import UTC, datetime, timedelta

    root = tmp_path / "match-auto"
    project = MatchProject.init(root, name="Auto Match")
    base = datetime.now(UTC).replace(microsecond=0)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="A",
            time_seconds=10.0,
            scorecard_updated_at=base,
        ),
        StageEntry(
            stage_number=2,
            stage_name="B",
            time_seconds=10.0,
            scorecard_updated_at=base + timedelta(minutes=30),
        ),
    ]
    src = tmp_path / "VID.mp4"
    src.write_bytes(b"")
    video = project.register_video(src, root)
    # Set mtime so it lands in stage 1's window (scorecard - tolerance ≤ mtime ≤ scorecard).
    target_ts = (base - timedelta(minutes=5)).timestamp()
    os.utime(src, (target_ts, target_ts))

    suggestions = project.auto_match(root)

    assert suggestions == {1: video.path}
    # No mutation -- the video is still unassigned.
    assert len(project.unassigned_videos) == 1
    assert project.stages[0].videos == []


def test_storage_paths_default_to_project_subdirs(tmp_path: Path) -> None:
    root = tmp_path / "match-paths"
    project = MatchProject.init(root, name="Paths Match")
    assert project.raw_path(root) == root / "raw"
    assert project.audio_path(root) == root / "audio"
    assert project.trimmed_path(root) == root / "trimmed"
    assert project.exports_path(root) == root / "exports"


def test_storage_paths_relative_resolve_against_root(tmp_path: Path) -> None:
    root = tmp_path / "match-relative"
    project = MatchProject.init(root, name="Relative Match")
    project.audio_dir = "cache/audio"
    project.trimmed_dir = "cache/trimmed"
    assert project.audio_path(root) == root / "cache" / "audio"
    assert project.trimmed_path(root) == root / "cache" / "trimmed"


def test_storage_paths_absolute_used_as_is(tmp_path: Path) -> None:
    root = tmp_path / "match-absolute"
    project = MatchProject.init(root, name="Absolute Match")
    scratch = tmp_path / "scratch"
    project.audio_dir = str(scratch / "audio")
    project.trimmed_dir = str(scratch / "trimmed")
    assert project.audio_path(root) == scratch / "audio"
    assert project.trimmed_path(root) == scratch / "trimmed"


def test_register_video_uses_configured_raw_dir(tmp_path: Path) -> None:
    """When raw_dir points outside the project, the symlink lives there and
    the StageVideo path is stored as absolute (since it's not under root)."""
    root = tmp_path / "match-raw-config"
    project = MatchProject.init(root, name="Raw Config Match")
    external_raw = tmp_path / "external-raw"
    project.raw_dir = str(external_raw)

    src = tmp_path / "external" / "VID.mp4"
    src.parent.mkdir()
    src.write_bytes(b"")

    video = project.register_video(src, root)
    assert video.path.is_absolute()
    assert (external_raw / "VID.mp4").exists()
    assert (external_raw / "VID.mp4").resolve() == src


def test_atomic_write_uses_same_directory_for_tmp(tmp_path: Path) -> None:
    """The temp file must live in the destination's directory so ``os.replace``
    is atomic (cross-filesystem renames aren't)."""
    target = tmp_path / "out.json"

    seen_dirs: list[Path] = []
    original_mkstemp = os.path.dirname  # placeholder so name exists

    import tempfile as _tempfile

    real_mkstemp = _tempfile.mkstemp

    def spy(*args: object, **kwargs: object) -> tuple[int, str]:
        if "dir" in kwargs:
            seen_dirs.append(Path(str(kwargs["dir"])))
        return real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]

    _tempfile.mkstemp = spy  # type: ignore[assignment]
    try:
        atomic_write_json(target, {"hello": "world"})
    finally:
        _tempfile.mkstemp = real_mkstemp  # type: ignore[assignment]

    assert seen_dirs == [tmp_path]
    assert json.loads(target.read_text()) == {"hello": "world"}

    # Touch ``original_mkstemp`` so flake8 doesn't flag it; harmless.
    _ = original_mkstemp
