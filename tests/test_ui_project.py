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
