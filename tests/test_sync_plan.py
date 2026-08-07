"""Tests for the local sync engine's push planning layer (``splitsmith.sync``).

Task 7 of the desktop-to-hosted sync MVP (#631): pure functions over the
filesystem that decide what a local match should push, with rsync-style
size+mtime digest skipping. No network code lives here - the plan is
consumed by a later push executor.

Cases:
  1. A fresh plan over a one-shooter, one-stage match emits the match doc,
     the sanitized project doc, the one audit doc, and both trimmed-media
     items (clip + params sidecar) with the exact remote keys.
  2. A plan built with the prior plan's digests already recorded in
     ``SyncState`` skips every media item (rsync-style: size+mtime match).
  3. Touching the trimmed clip (new bytes, new mtime) makes it reappear in
     the plan even though the sidecar is still skipped.
  4. A project with an absolute ``StageVideo.path`` fails validation: the
     plan's ``errors`` names the offending stage and path.
  5. A corrupt ``sync_state.json`` loads as a fresh ``SyncState`` (not a
     crash), and a plan built against it is the full, unfiltered plan.
  6. ``save_sync_state`` / ``load_sync_state`` round-trip.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from splitsmith import match_model
from splitsmith.sync.docs import STRIPPED_PROJECT_FIELDS
from splitsmith.sync.plan import build_push_plan
from splitsmith.sync.state import SYNC_STATE_FILE, SyncedItem, SyncState, load_sync_state, save_sync_state
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

TRIMMED_NAME = "stage1_cam_abc123_trimmed.mp4"
SIDECAR_NAME = "stage1_cam_abc123_trimmed.params.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_basic_match(tmp_path: Path) -> tuple[Path, str]:
    """One match, one shooter ("alice"), one stage, with a trimmed clip +
    sidecar + audit doc already on disk. Returns (match_root, slug)."""
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Test Match")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(root)

    slug = "alice"
    shooter = match_model.Shooter(slug=slug, name="Alice")
    match.add_shooter(root, shooter)
    shooter_root = match_model.Match.shooter_root(root, slug)

    project = MatchProject.init(shooter_root, name="Test Match")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0)]
    project.save(shooter_root)

    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(exist_ok=True)
    (trimmed_dir / TRIMMED_NAME).write_bytes(b"x" * 1024)
    (trimmed_dir / SIDECAR_NAME).write_text(json.dumps({"pre_buffer_seconds": 5.0}), encoding="utf-8")

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps({"detection": "ensemble", "shots": []}), encoding="utf-8"
    )

    return root, slug


def _synced_state_from(plan) -> SyncState:  # type: ignore[no-untyped-def]
    """Build a SyncState whose items match every media item in ``plan``
    exactly - simulating "everything was already pushed last time"."""
    return SyncState(
        items={
            item.remote_key: SyncedItem(sha256="0" * 64, size=item.size, mtime_ns=item.mtime_ns)
            for item in plan.media
        }
    )


# ---------------------------------------------------------------------------
# Case 1: fresh plan
# ---------------------------------------------------------------------------


def test_fresh_plan_emits_docs_and_media_with_exact_remote_keys(tmp_path: Path) -> None:
    root, slug = _build_basic_match(tmp_path)
    match_id = match_model.Match.load(root).match_id
    assert match_id is not None

    plan = build_push_plan(root, sync_state=SyncState())

    assert plan.match_id == match_id
    assert plan.match_name == "Test Match"

    kinds = [d.kind for d in plan.docs]
    assert kinds.count("match") == 1
    assert kinds.count("project") == 1
    assert kinds.count("audit") == 1
    assert len(plan.docs) == 3

    project_doc = next(d for d in plan.docs if d.kind == "project")
    assert project_doc.slug == slug
    for field in STRIPPED_PROJECT_FIELDS:
        assert field not in project_doc.body

    audit_doc = next(d for d in plan.docs if d.kind == "audit")
    assert audit_doc.slug == slug
    assert audit_doc.stage_number == 1
    assert audit_doc.body == {"detection": "ensemble", "shots": []}

    assert len(plan.media) == 2
    remote_keys = {m.remote_key for m in plan.media}
    assert remote_keys == {
        f"matches/{match_id}/shooters/{slug}/trimmed/{TRIMMED_NAME}",
        f"matches/{match_id}/shooters/{slug}/trimmed/{SIDECAR_NAME}",
    }
    assert plan.media_skipped == 0
    assert plan.errors == []


# ---------------------------------------------------------------------------
# Case 2: digests already recorded -> everything skipped
# ---------------------------------------------------------------------------


def test_second_plan_skips_media_already_recorded_in_sync_state(tmp_path: Path) -> None:
    root, _slug = _build_basic_match(tmp_path)
    first_plan = build_push_plan(root, sync_state=SyncState())

    state = _synced_state_from(first_plan)
    second_plan = build_push_plan(root, sync_state=state)

    assert second_plan.media == []
    assert second_plan.media_skipped == 2
    # Docs are always pushed, regardless of sync state.
    assert len(second_plan.docs) == 3


# ---------------------------------------------------------------------------
# Case 3: touching a file puts it back in the plan
# ---------------------------------------------------------------------------


def test_touching_the_clip_reenters_the_plan(tmp_path: Path) -> None:
    root, slug = _build_basic_match(tmp_path)
    first_plan = build_push_plan(root, sync_state=SyncState())
    state = _synced_state_from(first_plan)

    clip = match_model.Match.shooter_root(root, slug) / "trimmed" / TRIMMED_NAME
    clip.write_bytes(b"y" * 2048)
    fresh_ns = time.time_ns() + 5_000_000_000
    os.utime(clip, ns=(fresh_ns, fresh_ns))

    third_plan = build_push_plan(root, sync_state=state)

    assert len(third_plan.media) == 1
    assert third_plan.media[0].local_path == clip
    assert third_plan.media[0].size == 2048
    # The untouched sidecar is still skipped.
    assert third_plan.media_skipped == 1


# ---------------------------------------------------------------------------
# Case 4: absolute StageVideo.path -> validation error
# ---------------------------------------------------------------------------


def test_absolute_video_path_is_named_in_errors(tmp_path: Path) -> None:
    root, slug = _build_basic_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(root, slug)
    project = MatchProject.load(shooter_root)

    abs_path = tmp_path / "external-drive" / "GH010023.mp4"
    project.stages[0].videos.append(StageVideo(path=abs_path, role="secondary"))
    project.save(shooter_root)

    plan = build_push_plan(root, sync_state=SyncState())

    assert len(plan.errors) == 1
    assert "1" in plan.errors[0]
    assert str(abs_path) in plan.errors[0]


# ---------------------------------------------------------------------------
# Case 5: corrupt sync_state.json -> fresh state, full plan
# ---------------------------------------------------------------------------


def test_corrupt_sync_state_file_loads_fresh_and_plan_is_unfiltered(tmp_path: Path) -> None:
    root, _slug = _build_basic_match(tmp_path)
    (root / SYNC_STATE_FILE).write_text("{not valid json at all", encoding="utf-8")

    state = load_sync_state(root)
    assert state == SyncState()

    plan = build_push_plan(root, sync_state=state)
    assert len(plan.media) == 2
    assert plan.media_skipped == 0


# ---------------------------------------------------------------------------
# Case 6: save/load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_sync_state_round_trip(tmp_path: Path) -> None:
    root, _slug = _build_basic_match(tmp_path)
    item = SyncedItem(sha256="ab" * 32, size=10, mtime_ns=123)
    state = SyncState(items={"matches/m/shooters/a/trimmed/x.mp4": item})

    save_sync_state(root, state)
    assert (root / SYNC_STATE_FILE).exists()

    loaded = load_sync_state(root)
    assert loaded == state
