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
    VIDEO_EXTENSIONS,
    MatchProject,
    RawVideo,
    StageEntry,
    StageStatus,
    StageVideo,
    atomic_write_json,
    stage_audit_status,
)


def test_video_extensions_includes_common_camera_formats() -> None:
    expected = {
        ".mp4",
        ".mov",
        ".m4v",
        ".mts",
        ".m2ts",
        ".mkv",
        ".avi",
        ".mxf",
        ".lrv",
        ".360",
        ".webm",
    }
    assert expected.issubset(VIDEO_EXTENSIONS)


def test_init_creates_layout(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "match-a", name="Match A")

    assert project.name == "Match A"
    assert project.schema_version == SCHEMA_VERSION
    assert (tmp_path / "match-a" / PROJECT_FILE).exists()
    for sub in SUBDIRS:
        assert (tmp_path / "match-a" / sub).is_dir()


def test_load_migrates_v1_caches_to_v2(tmp_path: Path) -> None:
    """Opening a v1 project deletes the legacy role-named cache files so
    the new per-video naming can take over without serving stale audio.

    Regression for the bug where a primary swap silently reused
    ``stage<N>_primary.wav`` content from the previous primary.
    """
    root = tmp_path / "legacy-match"
    # Bootstrap a project, then forge an on-disk v1 representation so the
    # load() migration path runs against it.
    project = MatchProject.init(root, name="Legacy")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary")],
        )
    ]
    project.save(root)
    data = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    data["schema_version"] = 1
    (root / PROJECT_FILE).write_text(json.dumps(data), encoding="utf-8")

    audio = root / "audio"
    audio.mkdir(exist_ok=True)
    legacy_full = audio / "stage1_primary.wav"
    legacy_full.write_bytes(b"OLD")
    legacy_audit = audio / "stage1_audit.wav"
    legacy_audit.write_bytes(b"OLD")
    legacy_peaks = audio / "stage1_primary.peaks-1200.json"
    legacy_peaks.write_bytes(b"[]")
    trimmed = root / "trimmed"
    trimmed.mkdir(exist_ok=True)
    legacy_trim = trimmed / "stage1_trimmed.mp4"
    legacy_trim.write_bytes(b"OLD")
    legacy_params = trimmed / "stage1_trimmed.params.json"
    legacy_params.write_bytes(b"{}")
    # A pre-existing v2 cam-cache must survive the migration untouched.
    survivor = audio / "stage1_cam_abc123_audit.wav"
    survivor.write_bytes(b"KEEP")

    reloaded = MatchProject.load(root)

    # v1 chains through v2 + v3 to the current SCHEMA_VERSION.
    assert reloaded.schema_version == SCHEMA_VERSION
    assert not legacy_full.exists()
    assert not legacy_audit.exists()
    assert not legacy_peaks.exists()
    assert not legacy_trim.exists()
    assert not legacy_params.exists()
    assert survivor.exists()
    # And the version bump is persisted, so a second load is a no-op.
    persisted = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SCHEMA_VERSION


def test_load_backfills_raw_videos_from_existing_stagevideos(tmp_path: Path) -> None:
    """A v2 project on disk lands on v3 with ``raw_videos[]`` populated from
    its StageVideo entries -- one RawVideo per unique source path, with
    ``covers_stages`` aggregated across stages.
    """
    root = tmp_path / "v2-project"
    project = MatchProject.init(root, name="V2 Project")
    # One source covering stages 1-3 + a second source on stage 1 only +
    # an unassigned video. The migration should collapse the multi-stage
    # source into a single RawVideo with covers_stages=[1, 2, 3].
    shared = Path("raw/headcam.mp4")
    side = Path("raw/sidecam.mp4")
    unassigned = Path("raw/leftover.mp4")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[
                StageVideo(path=shared, role="primary"),
                StageVideo(path=side, role="secondary"),
            ],
        ),
        StageEntry(
            stage_number=2,
            stage_name="Two",
            time_seconds=15.0,
            videos=[StageVideo(path=shared, role="primary")],
        ),
        StageEntry(
            stage_number=3,
            stage_name="Three",
            time_seconds=20.0,
            videos=[StageVideo(path=shared, role="primary")],
        ),
    ]
    project.unassigned_videos = [StageVideo(path=unassigned)]
    project.save(root)

    # Materialize one of the sources on disk so the migration can read
    # its size; leave the others offline to confirm we tolerate that.
    (root / "raw").mkdir(exist_ok=True)
    (root / "raw" / "headcam.mp4").write_bytes(b"x" * 4096)

    # Force the on-disk version back to 2 so load() runs the v2->v3 path.
    data = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    data["schema_version"] = 2
    data.pop("raw_videos", None)
    (root / PROJECT_FILE).write_text(json.dumps(data), encoding="utf-8")

    reloaded = MatchProject.load(root)

    assert reloaded.schema_version == SCHEMA_VERSION
    by_path = {rv.storage_path: rv for rv in reloaded.raw_videos}
    assert set(by_path) == {"raw/headcam.mp4", "raw/sidecam.mp4", "raw/leftover.mp4"}
    assert by_path["raw/headcam.mp4"].covers_stages == [1, 2, 3]
    assert by_path["raw/sidecam.mp4"].covers_stages == [1]
    # Unassigned videos have no covers_stages -- they exist but aren't on
    # any stage yet. Empty list, not missing.
    assert by_path["raw/leftover.mp4"].covers_stages == []
    # Source-on-disk has its size populated; offline sources stay at 0.
    assert by_path["raw/headcam.mp4"].size_bytes == 4096
    assert by_path["raw/sidecam.mp4"].size_bytes == 0
    # sha256 is None on legacy backfill -- we never had one to record.
    assert by_path["raw/headcam.mp4"].sha256 is None
    # original_filename is the basename for SPA display.
    assert by_path["raw/headcam.mp4"].original_filename == "headcam.mp4"


def test_v2_to_v3_migration_is_idempotent(tmp_path: Path) -> None:
    """Re-loading a project doesn't duplicate raw_videos[] entries.

    The migration runs once on the first v2 load; subsequent loads see
    schema_version == 3 on disk and skip the backfill entirely.
    """
    root = tmp_path / "idempotent"
    project = MatchProject.init(root, name="Idempotent")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary")],
        )
    ]
    project.save(root)
    data = json.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    data["schema_version"] = 2
    data.pop("raw_videos", None)
    (root / PROJECT_FILE).write_text(json.dumps(data), encoding="utf-8")

    first = MatchProject.load(root)
    second = MatchProject.load(root)

    assert len(first.raw_videos) == 1
    assert len(second.raw_videos) == 1
    assert second.raw_videos[0].covers_stages == [1]


def test_attach_raw_video_merges_covers_stages(tmp_path: Path) -> None:
    """Calling ``attach_raw_video`` twice with the same storage_path unions
    the covers_stages list rather than appending a duplicate entry.
    """
    root = tmp_path / "attach"
    project = MatchProject.init(root, name="Attach")

    first = project.attach_raw_video(
        RawVideo(
            original_filename="clip.mp4",
            size_bytes=1024,
            storage_path="raw/clip.mp4",
            covers_stages=[1, 2],
        )
    )
    second = project.attach_raw_video(
        RawVideo(
            original_filename="clip.mp4",
            size_bytes=1024,
            storage_path="raw/clip.mp4",
            covers_stages=[2, 3],
        )
    )

    assert first is second
    assert len(project.raw_videos) == 1
    assert project.raw_videos[0].covers_stages == [1, 2, 3]


def test_attach_raw_video_fills_in_missing_size_and_sha256(tmp_path: Path) -> None:
    """A legacy backfill entry (size=0, sha256=None) gets upgraded when a
    real upload lands -- preserves history while filling in the gaps.
    """
    root = tmp_path / "upgrade"
    project = MatchProject.init(root, name="Upgrade")
    project.attach_raw_video(
        RawVideo(
            original_filename="clip.mp4",
            size_bytes=0,
            sha256=None,
            storage_path="raw/clip.mp4",
        )
    )
    project.attach_raw_video(
        RawVideo(
            original_filename="clip.mp4",
            size_bytes=2048,
            sha256="deadbeef",
            storage_path="raw/clip.mp4",
        )
    )

    assert len(project.raw_videos) == 1
    assert project.raw_videos[0].size_bytes == 2048
    assert project.raw_videos[0].sha256 == "deadbeef"


def test_find_raw_video_returns_none_when_absent(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "find", name="Find")
    assert project.find_raw_video("raw/missing.mp4") is None


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


def _bare_project(tmp_path: Path) -> tuple[Path, MatchProject]:
    """Helper for stage-status tests: empty project with 1 stage."""
    root = tmp_path / "match-status"
    project = MatchProject.init(root, name="Status Match")
    project.stages.append(StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=0.0))
    project.save(root)
    return root, project


def test_stage_status_todo_when_no_primary_video(tmp_path: Path) -> None:
    """Fresh stage with no video assigned is ``todo`` -- nothing to do."""
    root, project = _bare_project(tmp_path)
    assert stage_audit_status(project.stages[0], project.audit_path(root)) == StageStatus.todo


def test_stage_status_partial_when_video_but_no_time(tmp_path: Path) -> None:
    """Primary video assigned but no stage time imported = ``partial``."""
    root, project = _bare_project(tmp_path)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v.mp4"), role="primary"))
    assert stage_audit_status(project.stages[0], project.audit_path(root)) == StageStatus.partial


def test_stage_status_ready_when_prereqs_met_but_no_detection(
    tmp_path: Path,
) -> None:
    """Video + stage time but no audit file = ``ready``. The Audit page's
    PrereqGate handles the in-page beep/trim sub-states; this status
    just means "set up to audit, detection hasn't run yet"."""
    root, project = _bare_project(tmp_path)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v.mp4"), role="primary"))
    project.stages[0].time_seconds = 12.5
    assert stage_audit_status(project.stages[0], project.audit_path(root)) == StageStatus.ready


def test_stage_status_in_progress_when_detection_run_but_no_save(
    tmp_path: Path,
) -> None:
    """Audit JSON exists with a shot_detect_run event but no save event
    means detection ran and the operator hasn't committed yet."""
    root, project = _bare_project(tmp_path)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v.mp4"), role="primary"))
    project.stages[0].time_seconds = 12.5
    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "shots": [],
                "audit_events": [{"ts": "2026-05-22T12:00:00Z", "kind": "shot_detect_run"}],
            }
        ),
        encoding="utf-8",
    )
    assert stage_audit_status(project.stages[0], audit_dir) == StageStatus.in_progress


def test_stage_status_audited_when_save_event_present(tmp_path: Path) -> None:
    """One ``save`` event in audit_events is enough: that's the operator
    explicitly committing the stage."""
    root, project = _bare_project(tmp_path)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v.mp4"), role="primary"))
    project.stages[0].time_seconds = 12.5
    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "shots": [],
                "audit_events": [
                    {"ts": "2026-05-22T12:00:00Z", "kind": "shot_detect_run"},
                    {"ts": "2026-05-22T12:01:00Z", "kind": "save"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert stage_audit_status(project.stages[0], audit_dir) == StageStatus.audited


def test_stage_status_skipped_overrides_everything(tmp_path: Path) -> None:
    """``skipped`` is terminal; we never fall through to other checks even
    if the stage has audit data. Skipped is operator intent."""
    root, project = _bare_project(tmp_path)
    project.stages[0].skipped = True
    # Add audit data that would otherwise read as audited.
    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "audit_events": [{"ts": "2026-05-22T12:01:00Z", "kind": "save"}],
            }
        ),
        encoding="utf-8",
    )
    assert stage_audit_status(project.stages[0], audit_dir) == StageStatus.skipped


def test_stage_status_ready_on_corrupt_audit_json(tmp_path: Path) -> None:
    """Garbage in the audit JSON shouldn't lock the stage into a wrong
    state. Treat as ``ready`` so the operator can re-run detection;
    the Audit page surfaces the read error in-page."""
    root, project = _bare_project(tmp_path)
    project.stages[0].videos.append(StageVideo(path=Path("raw/v.mp4"), role="primary"))
    project.stages[0].time_seconds = 12.5
    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text("not json {", encoding="utf-8")
    assert stage_audit_status(project.stages[0], audit_dir) == StageStatus.ready


def test_audited_count_only_counts_saved_stages(tmp_path: Path) -> None:
    """``MatchProject.audited_count`` is the single source of truth the
    shooter-list / project-detail endpoints use. Skipped + in_progress
    + ready stages must NOT count."""
    root = tmp_path / "match-counts"
    project = MatchProject.init(root, name="Counts")
    project.stages = [StageEntry(stage_number=i, stage_name=f"S{i}", time_seconds=10.0) for i in range(1, 6)]
    for s in project.stages:
        s.videos.append(StageVideo(path=Path(f"raw/v{s.stage_number}.mp4"), role="primary"))
    # Stage 5: skipped (not audited).
    project.stages[4].skipped = True
    # Stage 4: time still zero -> partial (not audited).
    project.stages[3].time_seconds = 0.0
    project.save(root)

    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    # Stage 1: saved (audited).
    (audit_dir / "stage1.json").write_text(json.dumps({"audit_events": [{"kind": "save"}]}), encoding="utf-8")
    # Stage 2: detection ran but no save (in_progress).
    (audit_dir / "stage2.json").write_text(
        json.dumps({"audit_events": [{"kind": "shot_detect_run"}]}),
        encoding="utf-8",
    )
    # Stage 3: ready, no audit file at all.

    assert project.audited_count(root) == 1


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


def test_merge_stage_rounds_backfills_only_missing_entries(tmp_path: Path) -> None:
    """Existing projects can pick up ``stage_rounds`` without re-importing.

    Stages with ``stage_rounds=None`` get filled from the cached
    ``MatchData``; stages with a value already set are left alone so a
    user-edited override survives a refresh.
    """
    from splitsmith.config import StageRounds
    from splitsmith.ui.scoreboard.models import CacheInfo, MatchData, StageInfo

    root = tmp_path / "match-backfill"
    project = MatchProject.init(root, name="x")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=42.76,
            stage_rounds=None,
        ),
        StageEntry(
            stage_number=2,
            stage_name="100m",
            time_seconds=12.48,
            # User-edited override; backfill must NOT touch this.
            stage_rounds=StageRounds(expected=99),
        ),
    ]

    match_data = MatchData.model_construct(
        name="x",
        ssi_url=None,
        stages_count=2,
        competitors_count=0,
        scoring_completed=0.0,
        match_status="open",
        results_status="open",
        registration_status="closed",
        is_registration_possible=False,
        is_squadding_possible=False,
        stages=[
            StageInfo(
                id=1,
                stage_number=1,
                name="K-vallen",
                min_rounds=31,
                paper_targets=14,
                steel_targets=3,
            ),
            StageInfo(
                id=2,
                stage_number=2,
                name="100m",
                min_rounds=12,
                paper_targets=6,
                steel_targets=0,
            ),
        ],
        competitors=[],
        squads=[],
        cacheInfo=CacheInfo.model_construct(),
    )

    updated = project.merge_stage_rounds(match_data)
    assert updated == 1
    assert project.stages[0].stage_rounds.expected == 31
    assert project.stages[0].stage_rounds.paper_targets == 14
    # User override is preserved.
    assert project.stages[1].stage_rounds.expected == 99


def test_populate_from_match_data_populates_stage_rounds(tmp_path: Path) -> None:
    """``StageInfo.min_rounds`` flows through to ``StageEntry.stage_rounds`` (online path)."""
    from splitsmith.ui.scoreboard.models import CacheInfo, MatchData, StageInfo

    root = tmp_path / "match-md"
    project = MatchProject.init(root, name="placeholder")

    # Bypass MatchData's full-shape validation -- we only care about the
    # fields populate_from_match_data actually reads (name, ssi_url,
    # stages). model_construct skips validation for this focused test.
    match_data = MatchData.model_construct(
        name="Blacksmith Handgun Open 2026",
        ssi_url="https://shootnscoreit.com/event/match/22/27046/",
        stages_count=2,
        competitors_count=0,
        scoring_completed=0.0,
        match_status="open",
        results_status="open",
        registration_status="closed",
        is_registration_possible=False,
        is_squadding_possible=False,
        stages=[
            StageInfo(
                id=94151,
                stage_number=1,
                name="K-vallen",
                min_rounds=31,
                paper_targets=14,
                steel_targets=3,
            ),
            StageInfo(
                id=94152,
                stage_number=2,
                name="100m",
                min_rounds=12,
                paper_targets=6,
                steel_targets=0,
            ),
        ],
        competitors=[],
        squads=[],
        cacheInfo=CacheInfo.model_construct(),
    )
    project.populate_from_match_data(match_data)

    s1 = project.stages[0]
    assert s1.stage_rounds is not None
    assert s1.stage_rounds.expected == 31
    assert s1.stage_rounds.paper_targets == 14
    assert s1.stage_rounds.steel_targets == 3
    assert project.stages[1].stage_rounds.expected == 12


def test_import_scoreboard_populates_stage_rounds_from_top_level_stages(
    tmp_path: Path,
) -> None:
    """Top-level ``stages`` array carries ``min_rounds`` / target counts; legacy
    drop-JSON path picks them up so adaptive Voter C + apriori boost can fire."""
    root = tmp_path / "match-rounds"
    project = MatchProject.init(root, name="placeholder")

    scoreboard = {
        "match": {"id": "27046", "name": "Blacksmith"},
        "stages": [
            {"stage_number": 1, "min_rounds": 31, "paper_targets": 14, "steel_targets": 3},
            {"stage_number": 2, "min_rounds": 12, "paper_targets": 6, "steel_targets": 0},
        ],
        "competitors": [
            {
                "name": "Sample",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "K-vallen",
                        "time_seconds": 42.76,
                        "scorecard_updated_at": "2026-04-12T11:29:28.833034+00:00",
                    },
                    {
                        "stage_number": 2,
                        "stage_name": "100m",
                        "time_seconds": 12.48,
                        "scorecard_updated_at": "2026-04-12T12:03:32.729554+00:00",
                    },
                ],
            }
        ],
    }
    project.import_scoreboard(scoreboard)

    assert project.stages[0].stage_rounds is not None
    assert project.stages[0].stage_rounds.expected == 31
    assert project.stages[0].stage_rounds.paper_targets == 14
    assert project.stages[0].stage_rounds.steel_targets == 3
    assert project.stages[1].stage_rounds.expected == 12


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

    vid = project.stages[0].videos[0].video_id

    plan = project.remove_video(video.path, root)

    assert plan.was_primary is True
    assert plan.stage_number == 1
    assert plan.audio_cache_path == (root / "audio" / f"stage1_cam_{vid}.wav")
    assert plan.trimmed_cache_path == (root / "trimmed" / f"stage1_cam_{vid}_trimmed.mp4")
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


def test_register_video_stamps_camera_mount_from_make_heuristic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering an iPhone clip stamps ``camera_mount='hand'`` (issue #143).

    The probe is monkeypatched so the test doesn't need ffprobe / a
    real iPhone container; what we're verifying is the wiring + the
    make-vendor heuristic, not ffprobe.
    """
    root = tmp_path / "match-iphone"
    project = MatchProject.init(root, name="iPhone Match")
    source = tmp_path / "external" / "IMG_1234.MOV"
    source.parent.mkdir()
    source.write_bytes(b"\x00\x00\x00 ftypqt  ")

    from splitsmith import fixture_schema

    monkeypatch.setattr(
        "splitsmith.ui.project.probe_camera_metadata",
        lambda _path: fixture_schema.CameraProbeResult(make="Apple", model="iPhone 15 Pro"),
        raising=False,
    )
    # The import in register_video is local; patch the symbol on the
    # fixture_schema module too so the local ``from .. import`` lookup
    # picks up the stub.
    monkeypatch.setattr(
        fixture_schema,
        "probe_camera_metadata",
        lambda _path: fixture_schema.CameraProbeResult(make="Apple", model="iPhone 15 Pro"),
    )

    video = project.register_video(source, root)
    assert video.camera_mount == "hand"


def test_register_video_unknown_make_leaves_camera_mount_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognised vendor leaves ``camera_mount=None`` -- the user can override."""
    root = tmp_path / "match-unknown"
    project = MatchProject.init(root, name="Unknown Cam")
    source = tmp_path / "external" / "RANDOM.mp4"
    source.parent.mkdir()
    source.write_bytes(b"")

    from splitsmith import fixture_schema

    monkeypatch.setattr(
        fixture_schema,
        "probe_camera_metadata",
        lambda _path: fixture_schema.CameraProbeResult(make="ObscureCam Inc.", model="X1"),
    )

    video = project.register_video(source, root)
    assert video.camera_mount is None


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


def test_assign_video_secondary_to_empty_stage_auto_upgrades_to_primary(
    tmp_path: Path,
) -> None:
    """First video onto a stage becomes primary even when role='secondary'.

    Bootstraps placeholder-mode projects (no scoreboard -> ``auto_match``
    returns nothing) so the user doesn't have to manually promote.
    """
    root = tmp_path / "match-auto-primary"
    project = MatchProject.init(root, name="Auto Primary Match")
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    src = tmp_path / "VID.mp4"
    src.write_bytes(b"")
    v = project.register_video(src, root)

    # Default role from the SPA's drag-drop is "secondary".
    project.assign_video(v.path, to_stage_number=1, role="secondary")

    assert project.stages[0].videos[0].role == "primary"


def test_assign_video_secondary_when_primary_exists_stays_secondary(
    tmp_path: Path,
) -> None:
    """A second 'secondary' assignment does NOT demote the existing primary."""
    root = tmp_path / "match-no-demote"
    project = MatchProject.init(root, name="No Demote Match")
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    src1 = tmp_path / "VID_a.mp4"
    src2 = tmp_path / "VID_b.mp4"
    src1.write_bytes(b"")
    src2.write_bytes(b"")
    v1 = project.register_video(src1, root)
    v2 = project.register_video(src2, root)
    project.assign_video(v1.path, to_stage_number=1, role="primary")
    project.assign_video(v2.path, to_stage_number=1, role="secondary")

    stage = project.stages[0]
    primaries = [v for v in stage.videos if v.role == "primary"]
    secondaries = [v for v in stage.videos if v.role == "secondary"]
    assert len(primaries) == 1 and primaries[0].path == v1.path
    assert len(secondaries) == 1 and secondaries[0].path == v2.path


def test_assign_video_ignored_does_not_auto_upgrade(tmp_path: Path) -> None:
    """role='ignored' is the explicit opt-out; never gets upgraded to primary."""
    root = tmp_path / "match-ignored"
    project = MatchProject.init(root, name="Ignored Match")
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    src = tmp_path / "VID.mp4"
    src.write_bytes(b"")
    v = project.register_video(src, root)

    project.assign_video(v.path, to_stage_number=1, role="ignored")

    assert project.stages[0].videos[0].role == "ignored"
    assert project.stages[0].primary() is None


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


def test_swap_primary_no_audit_no_warning(tmp_path: Path) -> None:
    """primary_swap_warns is False when no audit JSON exists."""
    project = MatchProject.init(tmp_path / "m", name="m")
    project.stages.append(StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0))
    project.stages[0].videos.append(StageVideo(path=Path("raw/a.mp4"), role="primary"))
    assert project.primary_swap_warns(tmp_path / "m", stage_number=1) is False


def test_swap_primary_warns_when_shots_audited(tmp_path: Path) -> None:
    """primary_swap_warns is True when audit JSON has at least one shot."""
    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0))
    audit = root / "audit" / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "S1",
                "shots": [{"shot_number": 1, "candidate_number": 1, "time": 1.0, "ms_after_beep": 100}],
            }
        )
    )
    assert project.primary_swap_warns(root, stage_number=1) is True


def test_swap_primary_does_not_warn_for_pending_candidates_only(tmp_path: Path) -> None:
    """A fresh detection run leaves _candidates_pending_audit but no shots; the
    swap should be silent because re-detection on the new primary will produce
    a fresh candidate list anyway."""
    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0))
    audit = root / "audit" / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "S1",
                "shots": [],
                "_candidates_pending_audit": {
                    "candidates": [{"candidate_number": 1, "time": 1.0, "ms_after_beep": 0}]
                },
            }
        )
    )
    assert project.primary_swap_warns(root, stage_number=1) is False


def test_swap_primary_backs_up_audit_and_clears_processed(tmp_path: Path) -> None:
    """swap_primary renames the audit JSON to .bak when audit existed and
    clears the new primary's processed flags so detection re-runs."""
    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0))
    project.stages[0].videos.append(
        StageVideo(
            path=Path("raw/a.mp4"),
            role="primary",
            processed={"beep": True, "shot_detect": True, "trim": True},
            beep_time=1.234,
        )
    )
    project.stages[0].videos.append(StageVideo(path=Path("raw/b.mp4"), role="secondary"))
    audit = root / "audit" / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "S1",
                "shots": [{"shot_number": 1, "candidate_number": 1, "time": 1.0, "ms_after_beep": 100}],
            }
        )
    )

    new_primary = project.swap_primary(Path("raw/b.mp4"), root=root, stage_number=1, backup_audit=True)

    assert new_primary.role == "primary"
    assert new_primary.path == Path("raw/b.mp4")
    assert new_primary.processed == {"beep": False, "shot_detect": False, "trim": False}
    assert new_primary.beep_time is None
    # Old primary demoted to secondary.
    old = next(v for v in project.stages[0].videos if v.path == Path("raw/a.mp4"))
    assert old.role == "secondary"
    # Audit JSON renamed to .bak.
    assert not audit.exists()
    assert (root / "audit" / "stage1.json.bak").exists()


def test_swap_primary_skips_backup_when_no_audit(tmp_path: Path) -> None:
    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    project.stages.append(StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0))
    project.stages[0].videos.append(StageVideo(path=Path("raw/a.mp4"), role="primary"))
    project.stages[0].videos.append(StageVideo(path=Path("raw/b.mp4"), role="secondary"))

    project.swap_primary(Path("raw/b.mp4"), root=root, stage_number=1, backup_audit=False)

    assert not (root / "audit" / "stage1.json.bak").exists()


def test_match_analysis_uses_canonical_heuristic(tmp_path: Path) -> None:
    """match_analysis runs the same window math as video_match and returns
    per-stage windows + per-video classification."""
    from datetime import UTC, datetime, timedelta

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    sc1 = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    sc2 = sc1 + timedelta(minutes=30)
    project.stages.append(
        StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc1)
    )
    project.stages.append(
        StageEntry(stage_number=2, stage_name="S2", time_seconds=10.0, scorecard_updated_at=sc2)
    )
    project.stages[0].videos.append(
        StageVideo(
            path=Path("raw/a.mp4"),
            role="primary",
            match_timestamp=sc1 - timedelta(minutes=2),  # in window 1
        )
    )
    project.unassigned_videos.append(
        StageVideo(
            path=Path("raw/b.mp4"),
            match_timestamp=sc1 - timedelta(hours=2),  # orphan
        )
    )
    project.unassigned_videos.append(
        StageVideo(
            path=Path("raw/c.mp4"),
            match_timestamp=sc2 - timedelta(minutes=1),  # in window 2 only
        )
    )

    analysis = project.match_analysis()
    assert analysis.tolerance_minutes == 15  # VideoMatchConfig default
    assert len(analysis.stages) == 2
    assert analysis.stages[0].lower == sc1 - timedelta(minutes=15)
    assert analysis.stages[0].upper == sc1
    by_path = {str(e.path): e for e in analysis.videos}
    assert by_path["raw/a.mp4"].classification == "in_window"
    assert by_path["raw/a.mp4"].stage_numbers == [1]
    assert by_path["raw/b.mp4"].classification == "orphan"
    assert by_path["raw/c.mp4"].classification == "in_window"
    assert by_path["raw/c.mp4"].stage_numbers == [2]


def test_match_analysis_handles_missing_timestamp(tmp_path: Path) -> None:
    """Videos without ``match_timestamp`` get classification ``no_timestamp``;
    the rest of the analysis still runs."""
    from datetime import UTC, datetime

    root = tmp_path / "m"
    project = MatchProject.init(root, name="m")
    sc = datetime(2026, 5, 2, 14, 30, 0, tzinfo=UTC)
    project.stages.append(
        StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=sc)
    )
    project.unassigned_videos.append(StageVideo(path=Path("raw/legacy.mp4")))

    analysis = project.match_analysis()
    assert analysis.videos[0].classification == "no_timestamp"
    assert analysis.videos[0].stage_numbers == []
