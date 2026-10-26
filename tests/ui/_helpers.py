"""Shared set-up for the UI route tests: app builders, seeders, job waits.

Split out of the former ``tests/test_ui_server.py`` (#681); every file under
``tests/ui/`` and a few older files import from here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.conftest import scaffold_match as _scaffold_match


def _enable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt back into the on-assignment auto-fire for the calling test.
    Pairs with the autouse disable fixture above.
    """
    monkeypatch.delenv("SPLITSMITH_AUTO_BEEP_DISABLED", raising=False)


_SCOPED_PREFIXES = (
    "/api/shooters/",
    "/api/stages/",
    "/api/match/shooters",
    "/api/match/scoreboard",
    "/api/match/beep-queue",
    "/api/match/videos",
    "/api/match/compare-export",
    "/api/match/exports/",
    "/api/match/triage",
    "/api/project",
    "/api/assignments/",
    "/api/videos/",
    "/api/fs/",
    "/api/fixture/",
    "/api/thumbnails/",
    "/api/automation",
    "/api/dev/",
    "/api/lab/",
    "/api/calibrated-camera-models",
    "/api/jobs",
)


class _MatchClient:
    """TestClient wrapper that prefixes match-scoped URLs with
    ``/api/matches/{match_id}/`` after Tier 1 step 3 of doc 10.

    Reads ``bound_match_id`` lazily on every request so tests that
    bind via the picker endpoint after constructing the client see
    the correct match_id once the bind lands.
    """

    def __init__(self, app, **kwargs) -> None:
        self._client = TestClient(app, **kwargs)
        self._app = app

    @property
    def app(self):
        return self._app

    def _rewrite(self, url: str) -> str:
        # Pick the registered match -- tests scaffold a single
        # match folder per app. With zero (unbound) or more than
        # one (multi-match alias tests), the rewriter passes the
        # URL through so the test builds the prefix explicitly.
        ids = self._app.state.splitsmith_state.matches.known_ids()
        if len(ids) != 1:
            return url
        match_id = ids[0]
        for prefix in _SCOPED_PREFIXES:
            if url.startswith(prefix):
                rest = url[len("/api/") :]
                return f"/api/matches/{match_id}/{rest}"
        return url

    def get(self, url, **kw):
        return self._client.get(self._rewrite(url), **kw)

    def post(self, url, **kw):
        return self._client.post(self._rewrite(url), **kw)

    def put(self, url, **kw):
        return self._client.put(self._rewrite(url), **kw)

    def patch(self, url, **kw):
        return self._client.patch(self._rewrite(url), **kw)

    def delete(self, url, **kw):
        return self._client.delete(self._rewrite(url), **kw)

    def request(self, method, url, **kw):
        return self._client.request(method, self._rewrite(url), **kw)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _match_create_app(*, project_root, project_name=None, **kwargs):
    """create_app wrapper that scaffolds a Match folder at
    ``project_root`` (with one default shooter ``"me"``) if one
    isn't already there. Post Tier 1 step 3 of doc 10 the bind
    path refuses non-match paths -- tests that used to scaffold
    a legacy MatchProject inline now get a no-op-when-existing
    scaffold here for free.
    """
    from pathlib import Path as _Path

    from splitsmith import match_model

    project_root = _Path(project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    if not (project_root / "match.json").exists():
        match = match_model.Match.init(project_root, name=project_name or "Test Match")
        match.add_shooter(project_root, match_model.Shooter(slug="me", name="Me"))
        MatchProject.init(
            match_model.Match.shooter_root(project_root, "me"),
            name=project_name or "Test Match",
        )
    from splitsmith.ui.server import create_app as _real_create_app

    return _real_create_app(project_root=project_root, project_name=project_name, **kwargs)


def _wait_for_job(client, job_id: str, *, timeout: float = 5.0) -> dict:
    """Poll /api/jobs/{id} until the job is no longer running.

    Returns the final job snapshot. Raises ``AssertionError`` if the job
    doesn't finish in time -- ample margin since unit tests stub ffmpeg
    and beep_detect.
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/me/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


_SCOREBOARD_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "scoreboard"

_EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def _load_v1_match_fixture() -> dict:
    """Read the SSI v1 match fixture used by ``test_scoreboard_*``."""
    import json

    return json.loads((_SCOREBOARD_FIXTURES_DIR / "match_22_27190.json").read_text(encoding="utf-8"))


def _connect_fixture_shooter(tmp_path: Path):
    """Scaffold a match with a shooter whose name matches a fixture
    competitor, stage the offline match JSON, and return
    ``(client, match_root, shooter_slug, competitor)`` ready to connect."""
    import json as _json

    fixture = _load_v1_match_fixture()
    competitor = fixture["competitors"][0]  # Viktor Bergendahl / Production Optics

    match_root = tmp_path / "match"
    app = _match_create_app(project_root=match_root, project_name="Manual")
    client = _MatchClient(app)

    add = client.post("/api/match/shooters", json={"name": competitor["name"]})
    assert add.status_code == 200, add.text
    slug = next(s["slug"] for s in add.json()["shooters"] if s["name"] == competitor["name"])

    # Pre-place the match fixture so _resolve_scoreboard_client picks
    # LocalJsonScoreboard for the match root instead of SsiHttpClient.
    scoreboard_dir = match_root / "scoreboard"
    scoreboard_dir.mkdir(parents=True, exist_ok=True)
    (scoreboard_dir / "match.json").write_text(_json.dumps(fixture), encoding="utf-8")
    return client, match_root, slug, competitor


def _scan_paths(client, paths: list[Path]) -> dict:
    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_paths": [str(p) for p in paths], "auto_assign_primary": False},
    )
    assert resp.status_code == 200
    return resp.json()


def _primary_cache_paths(project_root: Path, stage_number: int = 1) -> tuple[Path, Path, Path]:
    """Schema v2 cache paths for the stage's primary, inside the
    Match folder's default shooter slot (``shooters/me``).

    Returns ``(full_wav, audit_wav, trimmed_mp4)`` keyed by the
    primary's ``video_id`` so tests don't have to hard-code the
    legacy ``stage<N>_primary.wav`` filename (which was retired in
    v2). Post Tier 1 step 3 of doc 10, per-shooter caches live under
    ``<match_root>/shooters/<slug>/``.
    """
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stage(stage_number).primary()
    assert primary is not None, f"stage {stage_number} has no primary"
    vid = primary.video_id
    audio_dir = shooter_root / "audio"
    trimmed_dir = shooter_root / "trimmed"
    return (
        audio_dir / f"stage{stage_number}_cam_{vid}.wav",
        audio_dir / f"stage{stage_number}_cam_{vid}_audit.wav",
        trimmed_dir / f"stage{stage_number}_cam_{vid}_trimmed.mp4",
    )


def _seed_project_with_primary(tmp_path: Path) -> tuple[TestClient, Path]:
    """Boot a server with one stage and a primary video assigned. Returns
    ``(client, video_source_path)`` so the caller can mutate the source if
    needed (e.g. to control mtime for video_match)."""
    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="Beep Match")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "Beep Match"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "Stage One",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    return client, src


def _stub_detect(
    monkeypatch,
    beep_time: float = 12.453,
    *,
    candidates: list | None = None,
    gate: threading.Event | None = None,
    confidence: float = 0.0,
) -> None:
    """Replace the per-video beep detector with a fast stub returning a
    deterministic BeepDetection. The endpoint is otherwise wrapped around
    ffmpeg + librosa, which we don't want to invoke from a unit test.
    Both the legacy ``detect_primary_beep`` shim and the new
    ``detect_video_beep`` are patched so callers on either path get the
    stubbed result.

    ``gate`` lets a test hold the fake detector until a barrier is
    released, simulating the real 2-10s detect-beep window so dedup tests
    aren't a race against a synchronous return.
    """
    from splitsmith.config import BeepCandidate, BeepDetection
    from splitsmith.ui import audio as audio_helpers

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        if gate is not None:
            assert gate.wait(timeout=5.0), "_stub_detect gate never released"
        cands: list[BeepCandidate] = candidates or []
        return BeepDetection(
            time=beep_time,
            peak_amplitude=0.42,
            duration_ms=320.0,
            candidates=cands,
            confidence=confidence,
        )

    monkeypatch.setattr(audio_helpers, "detect_primary_beep", fake)
    monkeypatch.setattr(audio_helpers, "detect_video_beep", fake)


def _three_candidates() -> list:
    from splitsmith.config import BeepCandidate

    return [
        BeepCandidate(time=12.453, score=18.0, peak_amplitude=0.42, duration_ms=320.0),
        BeepCandidate(time=8.100, score=9.0, peak_amplitude=0.30, duration_ms=180.0),
        BeepCandidate(time=27.500, score=4.5, peak_amplitude=0.55, duration_ms=210.0),
    ]


_BEEP_FIXTURE_WAV = Path(__file__).parent.parent / "fixtures" / "beep-test.wav"

_BEEP_FIXTURE_TRUTH = 4.877  # seconds (rise-foot, see beep-test.json)


def _stub_video_audio_with_fixture(monkeypatch) -> None:
    """Publish the real beep fixture WAV as the video's audio.

    The seeded primary's source is a zero-byte ``.mp4`` so no job could cut
    a WAV from it; the fixture WAV gives the snap detector a real signal to
    refine against. The snap reads what a job published, never extracts.
    """
    from splitsmith.ui import audio as audio_helpers

    def fake_published(root, n, video, **kwargs):  # type: ignore[no-untyped-def]
        return _BEEP_FIXTURE_WAV

    monkeypatch.setattr(audio_helpers, "published_video_audio", fake_published)


def _fake_ensemble_result(candidates: list[dict], consensus: int = 3):
    """Build an ``EnsembleResult`` from terse candidate dicts for tests."""
    from splitsmith.ensemble import EnsembleCandidate, EnsembleResult

    cands = [
        EnsembleCandidate(
            candidate_number=i + 1,
            time=c["time"],
            ms_after_beep=c.get("ms_after_beep", round((c["time"] - 5.0) * 1000)),
            peak_amplitude=c.get("peak_amplitude", 0.5),
            confidence=c.get("confidence", 0.8),
            vote_a=c.get("vote_a", 1),
            vote_b=c.get("vote_b", 1),
            vote_c=c.get("vote_c", 1),
            vote_total=c.get("vote_total", 3),
            apriori_boost=c.get("apriori_boost", 0.0),
            ensemble_score=c.get("ensemble_score", 3.0),
            score_c=c.get("score_c", 0.9),
            clap_diff=c.get("clap_diff", 0.5),
            gunshot_prob=c.get("gunshot_prob", 0.7),
            kept=c.get("kept", True),
        )
        for i, c in enumerate(candidates)
    ]
    return EnsembleResult(candidates=cands, consensus=consensus, expected_rounds=None)


def _seed_second_video(tmp_path: Path, project_root: Path) -> str:
    """Register a second raw video on stage 1 of the 'me' shooter project.

    Returns the new video's ``video_id`` so callers can target it in
    bulk-set requests.  The video file is created as an empty dummy so
    the project round-trip path validation doesn't complain.
    """
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    raw_dir = project.raw_path(shooter_root)
    raw_dir.mkdir(parents=True, exist_ok=True)
    second = raw_dir / "CAM2.mp4"
    second.write_bytes(b"")
    second_video = StageVideo(path=Path("raw/CAM2.mp4"), role="secondary")
    project.stages[0].videos.append(second_video)
    project.save(shooter_root)
    return second_video.video_id


def _seed_project_with_secondary(tmp_path: Path) -> tuple[TestClient, Path]:
    """Boot a server with one stage, a primary, and a secondary assigned.

    Returns ``(client, secondary_source_path)`` so the caller can mutate
    the secondary source if needed.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    src_dir = tmp_path / "videos"
    cam2 = src_dir / "CAM2.mp4"
    cam2.write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/CAM2.mp4", "to_stage_number": 1, "role": "secondary"},
    )
    return client, cam2


def _video_id_for(client: TestClient, stage_number: int, role: str) -> str:
    """Pull the ``video_id`` for the first video matching ``role`` on a stage."""
    proj = client.get("/api/shooters/me/project").json()
    stage = next(s for s in proj["stages"] if s["stage_number"] == stage_number)
    video = next(v for v in stage["videos"] if v["role"] == role)
    return video["video_id"]


def _as_placement(fake_align):  # noqa: ANN001, ANN202
    """The server places a secondary's beep with ``beep_cross.place_beep``
    (#1442); these tests describe the alignment as a ``CrossAlignResult``."""
    from splitsmith.beep.cross import BeepPlacement

    def place(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        r = fake_align(*args, **kwargs)
        return BeepPlacement(
            projected=r.secondary_beep_time, contrast=r.confidence, tone=None, time=r.secondary_beep_time
        )

    return place


def _wait_for_jobs_to_drain(client: TestClient, *, timeout: float = 5.0) -> list[dict]:
    """Poll /api/jobs until every job has left running/pending, then return
    the final snapshot. Used by the auto-fire tests since the SPA-equivalent
    flow doesn't return a job id from the assignment endpoint."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/api/me/jobs").json()
        active = [j for j in jobs if j["status"] in ("pending", "running")]
        if not active:
            return jobs
        time.sleep(0.02)
    raise AssertionError(f"jobs did not drain within {timeout}s: {jobs}")


def _seed_match_export_project(
    tmp_path: Path,
    *,
    stage_count: int = 2,
    stage_numbers: Sequence[int] | None = None,
    beep_time: float = 5.0,
) -> tuple[TestClient, Path]:
    """Seed a project with N stages, each having a primary + beep_time +
    audit JSON + lossless trim ready for match export.

    ``beep_time`` is every primary's beep in its source; under the 5 s
    pre-buffer it is a short-head stage (#1430).

    ``stage_numbers`` overrides the default contiguous ``1..stage_count``
    so callers can seed a *gapped* list (``[1, 2, 4, 5]``) -- the shape a
    match takes once the stage-list editor removes a stage (#521). The
    numbers are used verbatim for stage identity, artifact filenames and
    audit keys; nothing here derives a number from a list position.
    """
    import json as _json

    from splitsmith.match_project import MatchProject, StageEntry

    numbers = list(stage_numbers) if stage_numbers is not None else list(range(1, stage_count + 1))
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="Match Export Test")
    client = _MatchClient(app)
    project = MatchProject.load(_shooter_root)
    project.stages = []
    for n in numbers:
        project.stages.append(StageEntry(stage_number=n, stage_name=f"Stage {n}", time_seconds=10.0))
        src = _shooter_root / "raw" / f"VID{n}.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(src.name.encode())  # distinct bytes: one content is one clip
        video = project.register_video(src, project_root)
        project.assign_video(video.path, to_stage_number=n, role="primary")
        primary = project.stage(n).primary()
        assert primary is not None
        primary.beep_time = beep_time
    project.save(_shooter_root)

    audit_dir = _shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    exports_dir = _shooter_root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        (audit_dir / f"stage{n}.json").write_text(
            _json.dumps(
                {
                    "stage_number": n,
                    "stage_name": f"Stage {n}",
                    "stage_time_seconds": 10.0,
                    "beep_time": 5.0,
                    "shots": [{"shot_number": 1, "ms_after_beep": 500}],
                    "_candidates_pending_audit": {"candidates": []},
                }
            ),
            encoding="utf-8",
        )
        slug = f"stage{n}_stage-{n}"
        (exports_dir / f"{slug}_trimmed.mp4").write_bytes(b"")
    return client, project_root


def _stub_match_export_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import fcpxml_gen as fcpxml_mod
    from splitsmith.config import VideoMetadata

    def fake(_path: Path) -> VideoMetadata:
        return VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=20.0,
            frame_rate_num=30,
            frame_rate_den=1,
        )

    monkeypatch.setattr(fcpxml_mod, "probe_video", fake)


def _export_csv_only(client, stage_number: int = 1):
    return client.post(
        f"/api/shooters/me/stages/{stage_number}/export",
        json={
            "write_trim": False,
            "write_csv": True,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )


def _seed_trims_only_stage(tmp_path: Path, monkeypatch) -> tuple[object, Path]:
    """A stage with a beep, a reachable source and an audit holding zero shots.

    That is the trims-only reality: the compare grid never reads shot data,
    so the user cuts trims without ever running detection.
    """
    import json as _json

    from splitsmith import trim

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"
    (shooter_root / "audit" / "stage1.json").write_text(
        _json.dumps(
            {
                "stage_number": 1,
                "stage_name": "Stage 1",
                "stage_time_seconds": 10.0,
                "beep_time": 5.0,
                "shots": [],
                "_candidates_pending_audit": {"candidates": []},
            }
        ),
        encoding="utf-8",
    )

    def fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200
    return client, project_root


def _post_trims_only(client, stage_number: int = 1):
    return client.post(
        f"/api/shooters/me/stages/{stage_number}/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )


def _seed_cleanup_project(tmp_path: Path) -> tuple[_MatchClient, Path]:
    """A project whose seeded artefacts are genuinely rebuildable.

    Stage 1 is registered with its source blob present on disk, and
    ``audit/stage1.json`` exists. Without the stage entry nothing here is
    reconstructable -- ``_audited_stages`` iterates ``project.stages`` on
    desktop, and every per-camera artefact resolves through the stage --
    so a plain scaffold makes every category unrebuildable and the
    seed cannot tell the two cases apart (#926).
    """
    root, _shooter_root = _scaffold_match(tmp_path, name="Cleanup", subdir="match")
    project = MatchProject.load(_shooter_root)
    # Real bytes: ``reconstructable`` asks whether the artefact's own
    # input still exists, so a StageVideo pointing at nothing would flag
    # every per-camera artefact unrebuildable.
    source = _shooter_root / "raw" / "stage1.mov"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/stage1.mov"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(_shooter_root)

    audio = project.audio_path(_shooter_root)
    audio.mkdir(parents=True, exist_ok=True)
    (audio / "stage1_primary.wav").write_bytes(b"\x00" * 1024)

    exp = project.exports_path(_shooter_root)
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "stage1_one_overlay.mov").write_bytes(b"\x00" * 4096)

    audit = project.audit_path(_shooter_root)
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "stage1.json").write_text("{}", encoding="utf-8")

    app = _match_create_app(project_root=root, project_name="Cleanup")
    return _MatchClient(app), root


def _seed_unrebuildable_export(root: Path) -> tuple[Path, Path]:
    """Add one rebuildable and one unrebuildable ``exports-light`` file.

    ``EXPORTS_LIGHT`` keys on the stage's *audit doc*: the seed writes
    ``audit/stage1.json`` and nothing for stage 9, so the stage-1 CSV is
    re-derivable and the stage-9 one is not. Returns (rebuildable,
    unrebuildable).
    """
    exports = root / "shooters" / "me" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    rebuildable = exports / "stage1_one.csv"
    rebuildable.write_bytes(b"\x00" * 32)
    unrebuildable = exports / "stage9_nine.csv"
    unrebuildable.write_bytes(b"\x00" * 64)
    return rebuildable, unrebuildable


def _build_project_with_primary(
    root: Path,
    name: str,
    *,
    beep_time: float | None,
    beep_source: str | None,
    beep_confidence: float | None,
    beep_reviewed: bool,
    beep_auto_detect_failed: bool = False,
) -> MatchProject:
    """Helper for HITL queue tests -- builds a Match folder + one
    shooter ``me`` + one stage / primary.

    ``root`` is the match-folder root; the shooter project lives at
    ``<root>/shooters/me/`` post Tier 1 step 3 of doc 10.
    """
    from splitsmith import match_model

    match = match_model.Match.init(root, name=name)
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    shooter_root = match_model.Match.shooter_root(root, "me")
    project = MatchProject.init(shooter_root, name=name)
    primary = StageVideo(
        path=root / "primary.mp4",
        role="primary",
        beep_time=beep_time,
        beep_source=beep_source,  # type: ignore[arg-type]
        beep_confidence=beep_confidence,
        beep_reviewed=beep_reviewed,
        beep_auto_detect_failed=beep_auto_detect_failed,
    )
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0, videos=[primary])]
    project.save(shooter_root)
    return project


def _seed_review_state_fixtures(fixtures_root: Path) -> None:
    """Three fixtures spanning the promotion paths: batch-promoted
    unlabeled (pending), batch-promoted labeled (done), hand-dropped
    legacy (done)."""
    fixtures_root.mkdir(parents=True)

    def write(slug: str, payload: dict) -> None:
        (fixtures_root / f"{slug}.json").write_text(json.dumps(payload), encoding="utf-8")
        (fixtures_root / f"{slug}.wav").write_bytes(b"")

    write(
        "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
        {
            "promoted_at": "2026-08-14T12:38:39+00:00",
            "shots": [{"shot_number": 1, "time": 5.5}],
        },
    )
    write(
        "stage-shots-hfo-masters-2026-stage2-s0fe3d797",
        {
            "promoted_at": "2026-08-14T12:40:00+00:00",
            "shots": [{"shot_number": 1, "time": 5.5, "subclass": "steel"}],
        },
    )
    write("stage-shots-blacksmith-2026-stage6", {"shots": [{"time": 5.5}]})


def _make_match_app(tmp_path: Path):
    """Build a TestClient bound to a real Match folder with one shooter."""
    from splitsmith import match_model

    match_root = tmp_path / "scoped-match"
    match = match_model.Match.init(match_root, name="Scoped Match")
    shooter = match_model.Shooter(slug="mathias", name="Mathias")
    match.add_shooter(match_root, shooter)
    # Post Tier 1 step 3 of doc 10, every Match folder must carry a
    # ``project.json`` inside each shooter dir; the bind path
    # otherwise warns "no project.json" and the shooter is invisible
    # to the listing endpoints.
    MatchProject.init(match_model.Match.shooter_root(match_root, "mathias"), name=match.name)
    app = _match_create_app(project_root=match_root, project_name=match.name)
    return app, match, match_root


def _add_stage_video(
    root: Path,
    *,
    role: str,
    filename: str,
    beep_time: float | None,
    beep_source: str | None,
    beep_confidence: float | None,
    beep_reviewed: bool,
) -> str:
    """Append a video to stage 1 of the ``me`` shooter and return its video_id."""
    from splitsmith import match_model

    shooter_root = match_model.Match.shooter_root(root, "me")
    project = MatchProject.load(shooter_root)
    video = StageVideo(
        path=root / filename,
        role=role,  # type: ignore[arg-type]
        beep_time=beep_time,
        beep_source=beep_source,  # type: ignore[arg-type]
        beep_confidence=beep_confidence,
        beep_reviewed=beep_reviewed,
    )
    project.stages[0].videos.append(video)
    project.save(shooter_root)
    # video_id hashes "<path>#<stage_number>" and stage_number is stamped
    # on load - read the id back from a fresh load, not the local object.
    reloaded = MatchProject.load(shooter_root)
    return next(v for v in reloaded.stages[0].videos if v.path == video.path).video_id


def _combined_divisions() -> tuple[dict, dict[int, str]]:
    import json as _json

    combined = _json.loads(
        (_SCOREBOARD_FIXTURES_DIR / "match_22_27190_with_stages.json").read_text(encoding="utf-8")
    )
    return combined, {c["id"]: c["division"] for c in combined["competitors"]}


def _run_overlay_match_export(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, request_style: dict
) -> list[object]:
    """One match export with the overlay on; returns the per-stage export
    calls it made (empty when the overlay on disk was reused)."""
    from splitsmith.overlay import render as overlay_render
    from splitsmith.ui import exports as exports_mod

    calls: list[object] = []
    real = exports_mod.export_stage

    def capture(**kwargs: object) -> object:
        calls.append(kwargs["request"])
        return real(**kwargs)

    def fake_render(**kwargs: object) -> Path:
        out = kwargs["output_path"]
        assert isinstance(out, Path)
        out.write_bytes(b"drawn")
        return out

    monkeypatch.setattr(exports_mod, "export_stage", capture)
    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    resp = client.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "include_overlay": True, **request_style},
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    return calls


def _edit_shot_time(doc: dict) -> None:
    doc["shots"][0]["ms_after_beep"] = 640


def _confirm_a_region(doc: dict) -> None:
    doc["events"] = [{"id": "evt-1", "kind": "reload", "start": 0.6, "end": 1.9, "source": "manual"}]


def _edit_look_palette(manifest: dict) -> None:
    manifest["colors"]["accent"] = [10, 200, 90]


def _edit_look_mono_font(manifest: dict) -> None:
    manifest["fonts"]["mono"] = "roboto-mono"
