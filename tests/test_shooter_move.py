"""Tests for the cross-shooter move service and its endpoint.

All cases use local-mode Match fixtures built from ``Match.init`` +
``add_shooter`` + ``MatchProject`` on real ``tmp_path`` dirs. No audio
fixtures are needed: derived-cache moves are exercised with empty marker
files; audit JSON is hand-written; symlinks point at ``touch``-ed dummies.

Cases:
  1.  Symlink relink (source link gone, target link present, same real file).
  2.  Copy-mode move (real file renamed, not a symlink).
  3.  Carried StageVideo fields (beep_time, beep_source, etc.) -- no
      assign_video normalization.
  4.  Carried audit doc (target in, source out).
  5.  Primary-collision demotion (target has primary but no audited shots ->
      moved video lands as secondary; demoted_to_secondary=True).
  6.  Occupied-stage block (target primary + shots -> refusal, source intact).
  7.  Batch partial block (one moves, one blocked, both reported).
  8.  Unassigned video moves with no stage/audit/collision handling.
  9.  Unknown path fails the batch pre-mutation (source unchanged).
  10. raw_videos[] entry moves to target, drops from source when
      no other reference remains.
  11. Store-backed (hosted) audit round-trip (in-memory sqlite).
  12. Endpoint: POST /api/match/videos/move-shooter returns 200 + outcome;
      source project drops video, target gains it.
  13. Endpoint: same-slug -> 400.
  14. Endpoint: unknown slug -> 404.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import match_model
from splitsmith.ui.project import MatchProject, RawVideo, StageEntry, StageVideo
from splitsmith.ui.shooter_move import move_shooter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_shooter_match(tmp_path: Path, *, stages: int = 4) -> tuple[Path, str, str]:
    """Return (match_root, slug_a, slug_b) with N stages each."""
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Test Match")
    stage_defs = [
        match_model.MatchStageDefinition(stage_number=n, stage_name=f"Stage {n}")
        for n in range(1, stages + 1)
    ]
    match.stages = stage_defs
    match.save(root)

    for slug, name in [("alice", "Alice"), ("bob", "Bob")]:
        sh = match_model.Shooter(slug=slug, name=name)
        match.add_shooter(root, sh)
        sroot = match_model.Match.shooter_root(root, slug)
        proj = MatchProject.init(sroot, name="Test Match")
        proj.stages = [
            StageEntry(stage_number=n, stage_name=f"Stage {n}", time_seconds=60.0)
            for n in range(1, stages + 1)
        ]
        proj.save(sroot)

    return root, "alice", "bob"


def _add_video_symlink(
    project: MatchProject,
    root: Path,
    *,
    stage_number: int,
    filename: str,
    role: str = "primary",
    dummy_target: Path | None = None,
    beep_time: float | None = None,
    beep_source: str | None = None,
    beep_reviewed: bool = False,
    match_timestamp: datetime | None = None,
    processed: dict | None = None,
) -> StageVideo:
    """Register a video symlink on the given stage of the project."""
    raw_dir = project.raw_path(root)
    raw_dir.mkdir(parents=True, exist_ok=True)
    link = raw_dir / filename
    if dummy_target is None:
        dummy_target = root / f"_src_{filename}"
        dummy_target.write_bytes(b"dummy")
    if not link.exists() and not link.is_symlink():
        link.symlink_to(dummy_target)

    video = StageVideo(
        path=Path(f"raw/{filename}"),
        role=role,
        beep_time=beep_time,
        beep_source=beep_source,
        beep_reviewed=beep_reviewed,
        match_timestamp=match_timestamp,
        processed=processed or {"beep": False, "shot_detect": False, "trim": False},
    )
    stage = project.stage(stage_number)
    stage.videos.append(video)
    return video


def _add_video_real_file(
    project: MatchProject,
    root: Path,
    *,
    stage_number: int,
    filename: str,
    role: str = "primary",
) -> StageVideo:
    """Register a real (non-symlink) file on the given stage."""
    raw_dir = project.raw_path(root)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / filename).write_bytes(b"real bytes")

    video = StageVideo(path=Path(f"raw/{filename}"), role=role)
    stage = project.stage(stage_number)
    stage.videos.append(video)
    return video


def _write_audit(project: MatchProject, root: Path, stage_number: int, shots: list) -> None:
    audit_dir = project.audit_path(root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"stage{stage_number}.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")


def _read_audit(project: MatchProject, root: Path, stage_number: int) -> dict | None:
    p = project.audit_path(root) / f"stage{stage_number}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _local_audit_closures(project_a: MatchProject, root_a: Path, project_b: MatchProject, root_b: Path):
    """Return injected audit callables for local-mode tests."""

    def load_target(n: int) -> dict | None:
        return _read_audit(project_b, root_b, n)

    def save_target(n: int, doc: dict) -> None:
        audit_dir = project_b.audit_path(root_b)
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"stage{n}.json").write_text(json.dumps(doc), encoding="utf-8")

    def load_source(n: int) -> dict | None:
        return _read_audit(project_a, root_a, n)

    def clear_source(n: int) -> None:
        p = project_a.audit_path(root_a) / f"stage{n}.json"
        p.unlink(missing_ok=True)

    return load_target, save_target, load_source, clear_source


# ---------------------------------------------------------------------------
# Case 1: Symlink relink
# ---------------------------------------------------------------------------


def test_symlink_relink(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    dummy = tmp_path / "GH01.MP4"
    dummy.write_bytes(b"footage")
    _add_video_symlink(proj_a, root_a, stage_number=1, filename="GH01.MP4", dummy_target=dummy)
    proj_a.save(root_a)

    src_link = proj_a.raw_path(root_a) / "GH01.MP4"
    assert src_link.is_symlink()

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH01.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 1
    assert len(outcome.blocked) == 0
    assert outcome.moved[0].video_path == "raw/GH01.MP4"
    assert outcome.moved[0].stage_number == 1

    dst_link = proj_b.raw_path(root_b) / "GH01.MP4"
    # Target has the symlink pointing at the same real file.
    assert dst_link.is_symlink()
    assert Path(dst_link.readlink()).resolve() == dummy.resolve()
    # Source symlink is gone.
    assert not src_link.exists() and not src_link.is_symlink()

    # Source stage no longer has the video.
    assert proj_a.stage(1).primary() is None
    # Target stage has it as primary.
    assert proj_b.stage(1).primary() is not None
    assert str(proj_b.stage(1).primary().path) == "raw/GH01.MP4"


# ---------------------------------------------------------------------------
# Case 2: Copy-mode move (real file, not symlink)
# ---------------------------------------------------------------------------


def test_copy_mode_move(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    _add_video_real_file(proj_a, root_a, stage_number=2, filename="GH02.MP4")
    proj_a.save(root_a)

    src_path = proj_a.raw_path(root_a) / "GH02.MP4"
    assert src_path.is_file() and not src_path.is_symlink()

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH02.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 1
    assert len(outcome.blocked) == 0

    dst_path = proj_b.raw_path(root_b) / "GH02.MP4"
    assert dst_path.is_file()
    assert dst_path.read_bytes() == b"real bytes"
    assert not src_path.exists()


# ---------------------------------------------------------------------------
# Case 3: Carried StageVideo fields
# ---------------------------------------------------------------------------


def test_carried_stage_video_fields(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    _add_video_symlink(
        proj_a,
        root_a,
        stage_number=3,
        filename="GH03.MP4",
        role="primary",
        beep_time=2.345,
        beep_source="manual",
        beep_reviewed=True,
        match_timestamp=ts,
        processed={"beep": True, "shot_detect": True, "trim": True},
    )
    proj_a.save(root_a)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH03.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    target_video = proj_b.stage(3).primary()
    assert target_video is not None
    assert target_video.beep_time == pytest.approx(2.345)
    assert target_video.beep_source == "manual"
    assert target_video.beep_reviewed is True
    assert target_video.match_timestamp == ts
    assert target_video.processed == {"beep": True, "shot_detect": True, "trim": True}
    assert str(target_video.path) == "raw/GH03.MP4"


# ---------------------------------------------------------------------------
# Case 4: Carried audit doc
# ---------------------------------------------------------------------------


def test_carried_audit_doc(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    _add_video_symlink(proj_a, root_a, stage_number=3, filename="GH03.MP4")
    _write_audit(proj_a, root_a, 3, [1.0, 2.5, 3.8])
    proj_a.save(root_a)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH03.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    # Target has the audit.
    target_audit = _read_audit(proj_b, root_b, 3)
    assert target_audit is not None
    assert target_audit["shots"] == [1.0, 2.5, 3.8]

    # Source audit is gone.
    assert _read_audit(proj_a, root_a, 3) is None


# ---------------------------------------------------------------------------
# Case 5: Primary-collision demotion
# ---------------------------------------------------------------------------


def test_primary_collision_demotion(tmp_path: Path) -> None:
    """Target stage has a primary but no audited shots -> moved video lands as secondary."""
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    # Source: primary on stage 3, WITH reviewed shots.
    _add_video_symlink(proj_a, root_a, stage_number=3, filename="GH03A.MP4", role="primary")
    proj_a.save(root_a)
    _write_audit(proj_a, root_a, 3, [0.3, 0.5])

    # Target: also has a primary on stage 3, but NO audit JSON.
    _add_video_symlink(proj_b, root_b, stage_number=3, filename="GH03B.MP4", role="primary")
    proj_b.save(root_b)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH03A.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.blocked) == 0
    assert len(outcome.moved) == 1
    assert outcome.moved[0].demoted_to_secondary is True

    # The target's original primary is untouched; the moved video is secondary.
    primaries = [v for v in proj_b.stage(3).videos if v.role == "primary"]
    secondaries = [v for v in proj_b.stage(3).videos if v.role == "secondary"]
    assert len(primaries) == 1
    assert str(primaries[0].path) == "raw/GH03B.MP4"
    assert len(secondaries) == 1
    assert str(secondaries[0].path) == "raw/GH03A.MP4"

    # No audit is carried to the target (moved video was demoted to secondary)...
    assert _read_audit(proj_b, root_b, 3) is None
    # ...and the source audit is cleared, not orphaned: the source stage must
    # not keep reviewed shots for a video that has moved away.
    assert _read_audit(proj_a, root_a, 3) is None


# ---------------------------------------------------------------------------
# Case 6: Occupied-stage block
# ---------------------------------------------------------------------------


def test_occupied_stage_block(tmp_path: Path) -> None:
    """Target primary + audited shots -> move refused; source fully intact."""
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    _add_video_symlink(proj_a, root_a, stage_number=3, filename="GH03A.MP4", role="primary")
    _write_audit(proj_a, root_a, 3, [1.0])
    proj_a.save(root_a)

    _add_video_symlink(proj_b, root_b, stage_number=3, filename="GH03B.MP4", role="primary")
    _write_audit(proj_b, root_b, 3, [0.9])
    proj_b.save(root_b)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH03A.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 0
    assert len(outcome.blocked) == 1
    b = outcome.blocked[0]
    assert b.code == "occupied_stage"
    assert b.stage_number == 3
    assert b.video_path == "raw/GH03A.MP4"

    # Source unchanged: video still there, audit still there.
    assert proj_a.stage(3).primary() is not None
    assert str(proj_a.stage(3).primary().path) == "raw/GH03A.MP4"
    assert _read_audit(proj_a, root_a, 3) == {"shots": [1.0]}

    # Source raw link still present.
    src_link = proj_a.raw_path(root_a) / "GH03A.MP4"
    assert src_link.exists() or src_link.is_symlink()


# ---------------------------------------------------------------------------
# Case 7: Batch partial block
# ---------------------------------------------------------------------------


def test_batch_partial_block(tmp_path: Path) -> None:
    """Stage 3 blocked (occupied), stage 4 moves -- both reported."""
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    # Source: primary on stages 3 and 4.
    _add_video_symlink(proj_a, root_a, stage_number=3, filename="GH03.MP4", role="primary")
    _add_video_symlink(proj_a, root_a, stage_number=4, filename="GH04.MP4", role="primary")
    proj_a.save(root_a)

    # Target: stage 3 has primary + audited shots (blocked); stage 4 free.
    _add_video_symlink(proj_b, root_b, stage_number=3, filename="GH03B.MP4", role="primary")
    _write_audit(proj_b, root_b, 3, [0.9])
    proj_b.save(root_b)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH03.MP4", "raw/GH04.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 1
    assert len(outcome.blocked) == 1

    assert outcome.moved[0].video_path == "raw/GH04.MP4"
    assert outcome.moved[0].stage_number == 4

    assert outcome.blocked[0].video_path == "raw/GH03.MP4"
    assert outcome.blocked[0].code == "occupied_stage"

    # Stage 4 video is now on target.
    assert proj_b.stage(4).primary() is not None
    # Stage 3 source video still on source.
    assert proj_a.stage(3).primary() is not None


# ---------------------------------------------------------------------------
# Case 8: Unassigned video moves
# ---------------------------------------------------------------------------


def test_unassigned_video_moves(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    # Add to unassigned_videos directly (no stage number).
    raw_dir = proj_a.raw_path(root_a)
    raw_dir.mkdir(parents=True, exist_ok=True)
    dummy = root_a / "_src_GH05.MP4"
    dummy.write_bytes(b"x")
    (raw_dir / "GH05.MP4").symlink_to(dummy)

    video = StageVideo(path=Path("raw/GH05.MP4"), role="secondary")
    proj_a.unassigned_videos.append(video)
    proj_a.save(root_a)

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH05.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    assert len(outcome.moved) == 1
    assert outcome.moved[0].stage_number is None
    assert len(outcome.blocked) == 0

    # Removed from source unassigned.
    assert all(str(v.path) != "raw/GH05.MP4" for v in proj_a.unassigned_videos)
    # Appeared in target unassigned.
    assert any(str(v.path) == "raw/GH05.MP4" for v in proj_b.unassigned_videos)


# ---------------------------------------------------------------------------
# Case 9: Unknown path fails the batch pre-mutation
# ---------------------------------------------------------------------------


def test_unknown_path_fails_pre_mutation(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    # Add one legitimate video to source.
    _add_video_symlink(proj_a, root_a, stage_number=1, filename="GH01.MP4", role="primary")
    proj_a.save(root_a)

    # Request a path that does NOT exist on source.
    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH01.MP4", "raw/GHOST.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    # Entire batch aborted; source unchanged.
    assert len(outcome.moved) == 0
    assert any(b.code == "unknown_path" for b in outcome.blocked)

    # Source video still on source.
    assert proj_a.stage(1).primary() is not None
    # Target untouched.
    assert proj_b.stage(1).primary() is None


# ---------------------------------------------------------------------------
# Case 10: raw_videos[] bookkeeping
# ---------------------------------------------------------------------------


def test_raw_videos_bookkeeping(tmp_path: Path) -> None:
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    _add_video_symlink(proj_a, root_a, stage_number=1, filename="GH01.MP4")
    proj_a.save(root_a)

    # Attach a RawVideo entry on source.
    rv = RawVideo(
        original_filename="GH01.MP4",
        size_bytes=100,
        sha256=None,
        storage_path="raw/GH01.MP4",
        covers_stages=[1],
    )
    proj_a.attach_raw_video(rv)

    assert proj_a.find_raw_video("raw/GH01.MP4") is not None
    assert proj_b.find_raw_video("raw/GH01.MP4") is None

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH01.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    # Moved to target.
    assert proj_b.find_raw_video("raw/GH01.MP4") is not None
    # Dropped from source (no other StageVideo references it).
    assert proj_a.find_raw_video("raw/GH01.MP4") is None


# ---------------------------------------------------------------------------
# Case 11: Hosted-parity -- state_docs-backed audit round-trip
# ---------------------------------------------------------------------------


def test_hosted_audit_roundtrip(tmp_path: Path) -> None:
    """move_shooter with state_docs-backed audit closures: audit
    writes to the store for the target and deletes from the store
    for the source, leaving no local audit files."""
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="hosted@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = asyncio.run(_setup())
    store = ProjectStateStore(sf, user_id=uid)
    match_id = "test-match-1"

    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    _add_video_symlink(proj_a, root_a, stage_number=2, filename="GH02.MP4")

    # Seed source audit into the store.
    asyncio.run(store.save_audit(match_id, slug_a, 2, {"shots": [1.1, 2.2]}, expected_version=0))

    def load_target_audit(n: int) -> dict | None:
        doc, _ = asyncio.run(store.load_audit(match_id, slug_b, n))
        return doc

    def save_target_audit(n: int, doc: dict) -> None:
        _, version = asyncio.run(store.load_audit(match_id, slug_b, n))
        asyncio.run(store.save_audit(match_id, slug_b, n, doc, expected_version=version))

    def load_source_audit(n: int) -> dict | None:
        doc, _ = asyncio.run(store.load_audit(match_id, slug_a, n))
        return doc

    def clear_source_audit(n: int) -> None:
        asyncio.run(store.delete_audit(match_id, slug_a, n))

    outcome = move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH02.MP4"],
        load_target_audit=load_target_audit,
        save_target_audit=save_target_audit,
        load_source_audit=load_source_audit,
        clear_source_audit=clear_source_audit,
        storage=None,  # no S3 in tests
    )

    assert len(outcome.moved) == 1
    assert len(outcome.blocked) == 0

    # The StageVideo record itself relocated across the two project docs
    # (hosted mode persists these via project.save() -> state_docs). Assert
    # the record left the source stage and landed on the target stage so a
    # bind/save-ordering regression under a hosted move is caught here too.
    assert all(v.path.name != "GH02.MP4" for v in proj_a.stage(2).videos)
    assert any(v.path.name == "GH02.MP4" for v in proj_b.stage(2).videos)

    # Target audit in the store.
    doc_b, v_b = asyncio.run(store.load_audit(match_id, slug_b, 2))
    assert doc_b is not None
    assert doc_b["shots"] == [1.1, 2.2]
    assert v_b == 1

    # Source audit cleared from the store.
    doc_a, _ = asyncio.run(store.load_audit(match_id, slug_a, 2))
    assert doc_a is None

    # No local audit files created (hosted mode).
    assert not (proj_a.audit_path(root_a) / "stage2.json").exists()
    assert not (proj_b.audit_path(root_b) / "stage2.json").exists()


# ---------------------------------------------------------------------------
# Case 11b: delete_audit isolation (per-method discipline)
# ---------------------------------------------------------------------------


def test_delete_audit_isolated_by_user() -> None:
    """delete_audit on user A's store does not touch user B's same-slug row."""
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids: list[str] = []
        async with sf() as s:
            for email in ("a@test.se", "b@test.se"):
                user = User(email=email)
                s.add(user)
                await s.commit()
                await s.refresh(user)
                ids.append(user.id)
        return ids[0], ids[1]

    uid_a, uid_b = asyncio.run(_setup())
    store_a = ProjectStateStore(sf, user_id=uid_a)
    store_b = ProjectStateStore(sf, user_id=uid_b)

    asyncio.run(store_a.save_audit("match1", "alice", 3, {"shots": [1.0]}, expected_version=0))
    asyncio.run(store_b.save_audit("match1", "alice", 3, {"shots": [2.0]}, expected_version=0))

    rows_deleted = asyncio.run(store_a.delete_audit("match1", "alice", 3))
    assert rows_deleted == 1

    # A's row gone.
    assert asyncio.run(store_a.load_audit("match1", "alice", 3)) == (None, 0)
    # B's row untouched.
    assert asyncio.run(store_b.load_audit("match1", "alice", 3)) == ({"shots": [2.0]}, 1)


def test_delete_audit_absent_returns_zero() -> None:
    """Calling delete_audit on a non-existent row is a no-op."""
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="c@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = asyncio.run(_setup())
    store = ProjectStateStore(sf, user_id=uid)
    result = asyncio.run(store.delete_audit("ghost-match", "alice", 5))
    assert result == 0


# ---------------------------------------------------------------------------
# Derived cache relocation
# ---------------------------------------------------------------------------


def test_derived_cache_relocation(tmp_path: Path) -> None:
    """Audio WAV and trimmed MP4 are renamed to target dirs when present."""
    root, slug_a, slug_b = _two_shooter_match(tmp_path)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    proj_a = MatchProject.load(root_a)
    proj_b = MatchProject.load(root_b)

    video = _add_video_symlink(proj_a, root_a, stage_number=1, filename="GH01.MP4")
    proj_a.save(root_a)

    vid_id = video.video_id
    # Create dummy cache files at the expected source paths.
    wav_src = proj_a.audio_path(root_a) / f"stage1_cam_{vid_id}.wav"
    trim_src = proj_a.trimmed_path(root_a) / f"stage1_cam_{vid_id}_trimmed.mp4"
    for p in (wav_src, trim_src):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"cache")

    load_tgt, save_tgt, load_src, clear_src = _local_audit_closures(proj_a, root_a, proj_b, root_b)
    move_shooter(
        source_project=proj_a,
        source_root=root_a,
        target_project=proj_b,
        target_root=root_b,
        video_paths=["raw/GH01.MP4"],
        load_target_audit=load_tgt,
        save_target_audit=save_tgt,
        load_source_audit=load_src,
        clear_source_audit=clear_src,
        storage=None,
    )

    wav_dst = proj_b.audio_path(root_b) / f"stage1_cam_{vid_id}.wav"
    trim_dst = proj_b.trimmed_path(root_b) / f"stage1_cam_{vid_id}_trimmed.mp4"
    assert wav_dst.exists()
    assert trim_dst.exists()
    # Originals gone.
    assert not wav_src.exists()
    assert not trim_src.exists()


# ---------------------------------------------------------------------------
# Endpoint tests (Cases 12-14)
# ---------------------------------------------------------------------------


def _build_multi_shooter_app(tmp_path: Path) -> tuple:
    """Return (app, client, match_id, slug_a, slug_b)."""
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Endpoint Test")
    stage_defs = [match_model.MatchStageDefinition(stage_number=1, stage_name="S1")]
    match.stages = stage_defs
    match.save(root)

    for slug, name in [("alice", "Alice"), ("bob", "Bob")]:
        sh = match_model.Shooter(slug=slug, name=name)
        match.add_shooter(root, sh)
        sroot = match_model.Match.shooter_root(root, slug)
        proj = MatchProject.init(sroot, name="Endpoint Test")
        proj.stages = [StageEntry(stage_number=1, stage_name="S1", time_seconds=0.0)]
        proj.save(sroot)

    app = create_app()
    client = TestClient(app)

    # Register the match.
    resp = client.post("/api/me/recent-projects/bind", json={"path": str(root.resolve())})
    assert resp.status_code == 200, resp.text

    match_ids = app.state.splitsmith_state.matches.known_ids()
    assert len(match_ids) == 1
    mid = match_ids[0]
    return app, client, mid, "alice", "bob"


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def test_endpoint_move_shooter_200(tmp_path: Path) -> None:
    """POST /api/match/videos/move-shooter returns 200 + moved outcome."""
    app, client, mid, slug_a, slug_b = _build_multi_shooter_app(tmp_path)

    root = app.state.splitsmith_state.matches.resolve(mid)
    root_a = match_model.Match.shooter_root(root, slug_a)
    root_b = match_model.Match.shooter_root(root, slug_b)

    # Set up source video.
    proj_a = MatchProject.load(root_a)
    raw_dir = proj_a.raw_path(root_a)
    raw_dir.mkdir(parents=True, exist_ok=True)
    dummy = root / "_src.MP4"
    dummy.write_bytes(b"x")
    (raw_dir / "GH01.MP4").symlink_to(dummy)
    video = StageVideo(path=Path("raw/GH01.MP4"), role="primary")
    proj_a.stage(1).videos.append(video)
    proj_a.save(root_a)

    resp = client.post(
        f"/api/matches/{mid}/match/videos/move-shooter",
        json={"source_slug": slug_a, "target_slug": slug_b, "video_paths": ["raw/GH01.MP4"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"]["moved"][0]["video_path"] == "raw/GH01.MP4"
    assert body["outcome"]["blocked"] == []

    # Source project on disk no longer has the video.
    proj_a_after = MatchProject.load(root_a)
    assert proj_a_after.stage(1).primary() is None

    # Target project has it.
    proj_b_after = MatchProject.load(root_b)
    assert proj_b_after.stage(1).primary() is not None


def test_endpoint_same_slug_400(tmp_path: Path) -> None:
    _app, client, mid, slug_a, _slug_b = _build_multi_shooter_app(tmp_path)
    resp = client.post(
        f"/api/matches/{mid}/match/videos/move-shooter",
        json={"source_slug": slug_a, "target_slug": slug_a, "video_paths": []},
    )
    assert resp.status_code == 400


def test_endpoint_unknown_slug_404(tmp_path: Path) -> None:
    _app, client, mid, slug_a, _slug_b = _build_multi_shooter_app(tmp_path)
    resp = client.post(
        f"/api/matches/{mid}/match/videos/move-shooter",
        json={"source_slug": slug_a, "target_slug": "ghost", "video_paths": []},
    )
    assert resp.status_code == 404
