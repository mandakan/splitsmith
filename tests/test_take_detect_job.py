"""Take-aware detect-beep job: window derivation, soft-fail, chaining, persistence.

Each test calls the job body directly via a stub handle, monkeypatching
audio_helpers.detect_video_beep so no ffmpeg or audio processing runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from splitsmith.config import BeepWindowConfig
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.jobs import JobBodyRegistry
from splitsmith.ui.project import MatchProject, RawVideo, StageEntry, StageVideo
from splitsmith.ui.server import create_app, current_match_root

from .conftest import scaffold_match

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
DURATION = 1800.0
CANNED_BEEP = 462.0  # seconds into source; returned by fake detect


# ---------------------------------------------------------------------------
# Stub infra (duck-typed, no real registry/thread-pool)
# ---------------------------------------------------------------------------


class _StubTimer:
    @contextmanager
    def phase(self, _name: str):  # type: ignore[override]
        yield

    def set_meta(self, **_kw: object) -> None:
        pass


class _StubHandle:
    def __init__(self) -> None:
        self.id = "stub-job"
        self.timer = _StubTimer()

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        pass

    def check_cancel(self) -> None:
        pass

    def set_result(self, _payload: dict[str, Any]) -> None:
        pass


class _FakeJobBackend:
    """Records submitted jobs without running them."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.bodies = JobBodyRegistry()

    # -- sync lifecycle methods (JobBackend Protocol) --

    @property
    def is_shutting_down(self) -> bool:
        return False

    def active_count(self) -> int:
        return 0

    def begin_shutdown(self) -> None:
        pass

    def wait_for_drain(self, _timeout_s: float) -> bool:
        return True

    # -- async query/mutation methods --

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> None:
        self.submitted.append(
            {"kind": kind, "args": args, "stage_number": stage_number, "video_id": video_id}
        )

    async def find_active(
        self,
        *,
        kind: str | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> None:
        return None

    async def get(self, _job_id: str) -> None:
        return None

    async def list(self) -> list:
        return []

    async def cancel(self, _job_id: str) -> None:
        return None

    async def cancel_active_for_user(self) -> int:
        return 0

    async def acknowledge(self, _job_id: str) -> None:
        return None

    async def acknowledge_all_failures(self) -> list:
        return []


# ---------------------------------------------------------------------------
# Project builder
# ---------------------------------------------------------------------------


def _build_project(
    shooter_root: Path,
    *,
    with_scorecard: bool = True,
    single_stage: bool = False,
) -> MatchProject:
    """Load the scaffolded project, add stages + shared take, save."""
    proj = MatchProject.load(shooter_root)

    stage_specs = [
        (1, 22.0, 5),
        (2, 18.0, 10),
        (3, 25.0, 17),
    ]
    covers: list[int] = [1] if single_stage else [2, 3, 1]

    for sn, time_s, lead_min in stage_specs:
        if single_stage and sn != 1:
            continue
        scat = T0 + timedelta(minutes=lead_min) if with_scorecard else None
        stg = StageEntry(
            stage_number=sn,
            stage_name=f"Stage {sn}",
            time_seconds=time_s,
            scorecard_updated_at=scat,
        )
        vid = StageVideo(path=Path("raw/take.mp4"), role="primary", stage_number=sn)
        stg.videos.append(vid)
        proj.stages.append(stg)

    raw = RawVideo(
        original_filename="take.mp4",
        storage_path="raw/take.mp4",
        covers_stages=covers,
        duration_seconds=DURATION,
        recorded_start=T0,
    )
    proj.raw_videos.append(raw)
    proj.save(shooter_root)
    return proj


# ---------------------------------------------------------------------------
# Common setup helper
# ---------------------------------------------------------------------------


def _setup(
    tmp_path: Path,
    *,
    with_scorecard: bool = True,
    single_stage: bool = False,
) -> tuple[Path, Path, _FakeJobBackend, Any]:
    """Scaffold match, build project, wire fake job backend.

    Returns (match_root, shooter_root, fake_jobs, body).
    """
    match_root, shooter_root = scaffold_match(tmp_path)
    _build_project(shooter_root, with_scorecard=with_scorecard, single_stage=single_stage)

    # Create the fake video file so resolve_video_path / stat don't fail.
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "take.mp4").write_bytes(b"\x00")

    app = create_app(project_root=match_root)
    state = app.state.splitsmith_state
    fake_jobs = _FakeJobBackend()
    state.jobs = fake_jobs

    body = state.job_bodies.get("detect_beep")
    return match_root, shooter_root, fake_jobs, body


def _run_body(
    body: Any,
    match_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    slug: str = "me",
    stage_number: int,
    video_id: str,
    beep_time: float | None = CANNED_BEEP,
    raise_not_found: bool = False,
) -> list[tuple[float, float] | None]:
    """Run the body synchronously, capturing window kwargs passed to detect_video_beep."""
    from splitsmith import beep_detect as _bd

    captured_windows: list[tuple[float, float] | None] = []

    def fake_detect(_root, _sn, _video, _source, *, window=None, **_kw):
        captured_windows.append(window)
        if raise_not_found:
            raise _bd.BeepNotFoundError("no beep")
        from splitsmith.config import BeepDetection

        return BeepDetection(
            time=beep_time,  # type: ignore[arg-type]
            peak_amplitude=0.5,
            duration_ms=200.0,
            confidence=0.9,
            candidates=[],
        )

    monkeypatch.setattr(audio_helpers, "detect_video_beep", fake_detect)
    monkeypatch.setattr(audio_helpers, "ensure_video_audit_trim", lambda *a, **kw: Path("/dev/null"))

    token = current_match_root.set(match_root)
    try:
        body(_StubHandle(), slug=slug, stage_number=stage_number, video_id=video_id)
    finally:
        current_match_root.reset(token)

    return captured_windows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scoreboard_window_derived_and_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 2 with a scorecard timestamp gets the scoreboard-derived window."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=True)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    captured = _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    # Expected: offset = (T0+10min - T0) - 18 - 120 = 600 - 18 - 120 = 462
    # window = [462 - 180, 462 + 180] = [282, 642]
    assert len(captured) == 1
    assert captured[0] == pytest.approx((282.0, 642.0), abs=0.01)

    # Persisted on disk
    proj_after = MatchProject.load(shooter_root)
    stg2_after = proj_after.stage(2)
    vid2_after = stg2_after.primary()
    assert vid2_after is not None
    assert vid2_after.beep_window == pytest.approx((282.0, 642.0), abs=0.01)
    assert vid2_after.beep_window_source == "scoreboard"


def test_manual_window_short_circuits_derivation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-set manual window is passed verbatim; scoreboard derivation is skipped."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=True)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    # Pre-set a manual window on stage 2.
    vid2.beep_window = (30.0, 210.0)
    vid2.beep_window_source = "manual"
    proj.save(shooter_root)

    captured = _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    assert len(captured) == 1
    assert captured[0] == (30.0, 210.0)


def test_windowed_primary_soft_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowed primary BeepNotFoundError sets beep_auto_detect_failed, does not raise."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=True)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    # Should not raise even though beep detection fails.
    _run_body(
        body,
        match_root,
        monkeypatch,
        stage_number=2,
        video_id=vid2.video_id,
        raise_not_found=True,
    )

    proj_after = MatchProject.load(shooter_root)
    vid_after = proj_after.stage(2).primary()
    assert vid_after is not None
    assert vid_after.beep_auto_detect_failed is True
    assert vid_after.beep_time is None


def test_sequential_first_stage_gets_full_file_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequential mode: first covered stage (stage 2 in [2,3,1]) gets (0, duration)."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=False)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    captured = _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    assert len(captured) == 1
    assert captured[0] == pytest.approx((0.0, DURATION), abs=0.01)


def test_sequential_chains_next_stage_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After sequential stage 2, detect_beep is submitted for stage 3."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=False)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    # Should have chained to stage 3 (next in covers_stages=[2,3,1] after stage 2).
    chained = [s for s in fake_jobs.submitted if s["kind"] == "detect_beep"]
    assert len(chained) == 1
    assert chained[0]["stage_number"] == 3


def test_sequential_second_stage_anchors_off_prior_beep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequential stage 3 anchors window on stage 2's beep + its stage time + reset."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=False)
    proj = MatchProject.load(shooter_root)

    vid2 = proj.stage(2).primary()
    assert vid2 is not None

    # Run stage 2 first so its beep (462.0) is persisted.
    _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    # Now run stage 3.
    proj2 = MatchProject.load(shooter_root)
    vid3 = proj2.stage(3).primary()
    assert vid3 is not None

    captured = _run_body(body, match_root, monkeypatch, stage_number=3, video_id=vid3.video_id)

    # anchor = 462.0 (beep) + 18.0 (stage 2 time) + 45.0 (reset_margin_s) = 525.0
    cfg = BeepWindowConfig()
    expected_anchor = CANNED_BEEP + 18.0 + cfg.reset_margin_s  # 525.0
    assert len(captured) == 1
    assert captured[0] == pytest.approx((expected_anchor, DURATION), abs=0.01)


def test_single_coverage_raw_passes_no_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-stage raw (covers_stages=[1]) -> window kwarg is None (whole-file detect)."""
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=True, single_stage=True)
    proj = MatchProject.load(shooter_root)
    stg1 = proj.stage(1)
    vid1 = stg1.primary()
    assert vid1 is not None

    captured = _run_body(body, match_root, monkeypatch, stage_number=1, video_id=vid1.video_id)

    assert len(captured) == 1
    assert captured[0] is None


def test_manual_window_rescue_chains_next_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual window on stage 2 of a sequential take still chains stage 3 on success.

    Covers B1: the chain-advance guard no longer filters on window source
    being 'sequential', so a user rescue (manual window after soft-fail) also
    advances the chain when detection succeeds.
    """
    match_root, shooter_root, fake_jobs, body = _setup(tmp_path, with_scorecard=False)
    proj = MatchProject.load(shooter_root)
    stg2 = proj.stage(2)
    vid2 = stg2.primary()
    assert vid2 is not None

    # Simulate a prior soft-fail + user rescue: pre-set a manual window on stage 2.
    vid2.beep_window = (100.0, 300.0)
    vid2.beep_window_source = "manual"
    proj.save(shooter_root)

    _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    chained = [s for s in fake_jobs.submitted if s["kind"] == "detect_beep"]
    assert len(chained) == 1, f"expected 1 chained detect_beep, got {chained}"
    assert chained[0]["stage_number"] == 3


def test_mixed_mode_take_chains_after_scoreboard_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed-mode take: stage 2 has scorecard (scoreboard window), stages 3+1 don't.

    After stage 2 detect succeeds, detect_beep for stage 3 is chained.
    Covers B1: the chain-advance guard no longer filters on 'sequential' source.
    """
    match_root, shooter_root = scaffold_match(tmp_path)

    proj = MatchProject.load(shooter_root)
    for sn, time_s, has_scat in [(2, 18.0, True), (3, 25.0, False), (1, 22.0, False)]:
        scat = T0 + timedelta(minutes=10) if has_scat else None
        stg = StageEntry(
            stage_number=sn,
            stage_name=f"Stage {sn}",
            time_seconds=time_s,
            scorecard_updated_at=scat,
        )
        vid = StageVideo(path=Path("raw/take.mp4"), role="primary", stage_number=sn)
        stg.videos.append(vid)
        proj.stages.append(stg)
    raw = RawVideo(
        original_filename="take.mp4",
        storage_path="raw/take.mp4",
        covers_stages=[2, 3, 1],
        duration_seconds=DURATION,
        recorded_start=T0,
    )
    proj.raw_videos.append(raw)
    proj.save(shooter_root)

    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "take.mp4").write_bytes(b"\x00")

    app = create_app(project_root=match_root)
    state = app.state.splitsmith_state
    fake_jobs = _FakeJobBackend()
    state.jobs = fake_jobs
    body = state.job_bodies.get("detect_beep")

    proj2 = MatchProject.load(shooter_root)
    vid2 = proj2.stage(2).primary()
    assert vid2 is not None

    _run_body(body, match_root, monkeypatch, stage_number=2, video_id=vid2.video_id)

    chained = [s for s in fake_jobs.submitted if s["kind"] == "detect_beep"]
    assert len(chained) == 1, f"expected 1 chained detect_beep after scoreboard stage, got {chained}"
    assert chained[0]["stage_number"] == 3
