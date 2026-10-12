"""``verify_match_media``: will this match play on hosted (spec 2026-10-12 s5)."""

from __future__ import annotations

from pathlib import Path

from splitsmith import match_model
from splitsmith.sync.state import SyncedItem, SyncState, save_sync_state
from splitsmith.sync.verify import verify_match_media
from tests.media_bytes import corrupt_moov_mp4, tiny_mp4
from tests.test_sync_migrate import _match


def test_says_per_stage_video_whether_hosted_can_play_it(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1, 2, 3])
    t = sh / "trimmed"
    t.mkdir(exist_ok=True)
    v1, v2 = (project.stages[i].videos[0] for i in (0, 1))
    for v, n in ((v1, 1), (v2, 2)):
        (t / f"stage{n}_cam_{v.video_id}_trimmed.mp4").write_bytes(tiny_mp4())
    (t / f"stage1_cam_{v1.video_id}_web.mp4").write_bytes(tiny_mp4())
    (t / f"stage2_cam_{v2.video_id}_web.mp4").write_bytes(corrupt_moov_mp4())
    states = {r.stage_number: (r.state, r.reason) for r in verify_match_media(tmp_path / "m")}
    assert states[1][0] == "healing"  # valid locally, not yet uploaded
    assert states[2] == ("healing", "web rendition invalid: re-cut on the next sync")
    assert states[3][0] == "blocked" and "original" in (states[3][1] or "")


def test_an_uploaded_valid_rendition_is_playable(tmp_path: Path) -> None:
    root = tmp_path / "m"
    sh, project = _match(root, stages=[1])
    v = project.stages[0].videos[0]
    t = sh / "trimmed"
    t.mkdir(exist_ok=True)
    (t / f"stage1_cam_{v.video_id}_trimmed.mp4").write_bytes(tiny_mp4())
    web = t / f"stage1_cam_{v.video_id}_web.mp4"
    web.write_bytes(tiny_mp4())
    match_id = match_model.Match.load(root).match_id
    key = f"matches/{match_id}/shooters/me/trimmed/{web.name}"
    state = SyncState()
    state.items[key] = SyncedItem(sha256="x", size=web.stat().st_size, mtime_ns=0)
    save_sync_state(root, state)
    [row] = verify_match_media(root)
    assert (row.state, row.reason) == ("playable", None)
    # An upload of another size is a different file: the next push sends this one.
    state.items[key] = SyncedItem(sha256="x", size=1, mtime_ns=0)
    save_sync_state(root, state)
    assert verify_match_media(root)[0].state == "healing"


def test_an_unconfirmed_beep_is_blocked_on_the_user(tmp_path: Path) -> None:
    root = tmp_path / "m"
    sh, project = _match(root, stages=[1])
    project.stages[0].videos[0].beep_reviewed = False
    project.save(sh)
    [row] = verify_match_media(root)
    assert (row.state, row.reason) == ("blocked", "beep not confirmed")


def test_a_missing_trim_with_its_original_present_heals(tmp_path: Path) -> None:
    root = tmp_path / "m"
    sh, _project = _match(root, stages=[1])
    (sh / "raw").mkdir(exist_ok=True)
    (sh / "raw" / "v.mp4").write_bytes(tiny_mp4())
    [row] = verify_match_media(root)
    assert (row.state, row.reason) == ("healing", "trim missing: re-trimmed on the next sync")
