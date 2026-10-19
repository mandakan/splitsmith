"""Tests for the routes and wiring still in ``ui/server.py``.

Settings, fixtures, dev, lab, health, the SPA fallback and the app's own
behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.ui._helpers import (
    _confirm_a_region,
    _edit_look_mono_font,
    _edit_look_palette,
    _edit_shot_time,
    _export_csv_only,
    _make_match_app,
    _match_create_app,
    _MatchClient,
    _post_trims_only,
    _run_overlay_match_export,
    _scan_paths,
    _seed_match_export_project,
    _seed_project_with_primary,
    _seed_review_state_fixtures,
    _seed_trims_only_stage,
    _stub_match_export_probe,
    _wait_for_job,
)


def test_health_reports_unbound(tmp_path: Path) -> None:
    """Tier 1 step 4 of doc 10 retired the bound-state concept on
    the server. ``/api/health`` is now purely a "process up" check
    -- it never reports a bound project, even when one is registered
    on ``state.matches``. The SPA reads match identity from the URL,
    not from health.
    """
    app = _match_create_app(project_root=tmp_path / "match", project_name="Test Match")
    client = _MatchClient(app)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["bound"] is False
    assert body["project_name"] is None


def test_spa_serves_index_html_when_built(tmp_path: Path) -> None:
    """When ui_static/dist is built (the post-`npm run build` state), `/`
    must serve the SPA's index.html so the browser bootstraps the React app.

    Skipped automatically if the dev hasn't built the SPA yet; CI builds it
    before running tests, so the post-build path is the one we care about
    asserting against."""
    from splitsmith.ui.server import STATIC_DIR

    if not (STATIC_DIR / "index.html").exists():
        import pytest as _pytest

        _pytest.skip("SPA not built; run `pnpm build` in src/splitsmith/ui_static/")

    app = _match_create_app(project_root=tmp_path / "match", project_name="SPA Match")
    client = _MatchClient(app)

    # The /api/health route always works.
    assert client.get("/api/health").status_code == 200

    # SPA index for the root.
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'<div id="root"></div>' in resp.content

    # SPA fallback for unknown client routes (so React Router can handle them).
    resp = client.get("/some/deep/route")
    assert resp.status_code == 200
    assert b'<div id="root"></div>' in resp.content

    # API routes still return 404 from the fallback rather than the SPA.
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404


@respx.mock
def test_scoreboard_search_unbound_is_anonymous_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The create-match-from-scoreboard flow searches before a project
    exists; without a token it runs anonymously like the bound variant."""
    from splitsmith.ui.scoreboard.http import DEFAULT_BASE_URL

    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    resp = client.get("/api/scoreboard/search", params={"q": "anything"})
    assert resp.status_code == 200


@respx.mock
def test_scoreboard_search_unbound_works_with_no_bound_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint must not raise no_project (409) when the server has
    no bound project -- that's the whole point of the unbound variant."""
    from splitsmith.ui.scoreboard.http import DEFAULT_BASE_URL

    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
    app = create_app()  # no project bound
    client = _MatchClient(app)

    resp = client.get("/api/scoreboard/search", params={"q": "anything"})
    assert resp.status_code == 200  # anonymous, not no_project (409)


@respx.mock
def test_scoreboard_upstream_401_still_maps_to_scoreboard_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older deployment that still gates v1, or a set-but-rejected
    token, answers 401 upstream; the endpoint keeps the structured
    ``scoreboard_auth`` body the SPA routes to its offline fallback."""
    from splitsmith.ui.scoreboard.http import DEFAULT_BASE_URL

    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(return_value=httpx.Response(401))
    app = create_app()
    client = _MatchClient(app)

    resp = client.get("/api/scoreboard/matches/22/27190")
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["code"] == "scoreboard_auth"
    assert detail["env_var"] == "SPLITSMITH_SSI_TOKEN"
    assert "requires a bearer token" in detail["message"]


def test_fs_list_dirs_unbound_returns_directories(
    tmp_path: Path,
) -> None:
    """The unbound dir browser must not require a bound project (the
    create-match folder picker runs before any project exists). It
    should list child *directories* only -- files are deliberately
    excluded because this picker selects a parent for a new project."""
    parent = tmp_path / "fs-root"
    parent.mkdir()
    (parent / "alpha").mkdir()
    (parent / "beta").mkdir()
    (parent / "ignore-me.txt").write_text("not a dir", encoding="utf-8")
    (parent / ".hidden").mkdir()

    app = create_app()
    client = _MatchClient(app)

    resp = client.get("/api/fs/list-dirs", params={"path": str(parent)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [e["name"] for e in body["entries"]]
    assert names == ["alpha", "beta"]
    assert all(e["kind"] == "dir" for e in body["entries"])
    assert body["path"] == str(parent.resolve())


def test_fs_list_dirs_404_for_missing_path(tmp_path: Path) -> None:
    app = create_app()
    client = _MatchClient(app)
    resp = client.get("/api/fs/list-dirs", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


def test_scan_reports_a_rescanned_file_as_skipped(tmp_path: Path) -> None:
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    vid = tmp_path / "card" / "VID.mp4"
    vid.parent.mkdir()
    vid.write_bytes(b"the recording")
    _scan_paths(client, [vid])

    body = _scan_paths(client, [vid])

    assert body["registered"] == []
    assert body["skipped"] == ["VID.mp4: already imported for this shooter as raw/VID.mp4"]


def test_scan_counts_two_copies_in_one_import_once(tmp_path: Path) -> None:
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    first = tmp_path / "a" / "VID.mp4"
    second = tmp_path / "b" / "VID-copy.mp4"
    for f in (first, second):
        f.parent.mkdir()
        f.write_bytes(b"the recording")

    body = _scan_paths(client, [first, second])

    assert body["registered"] == ["raw/VID.mp4"]
    assert body["skipped"] == ["VID-copy.mp4: same recording as VID.mp4 in this import"]


def test_manual_stage_time_survives_scoreboard_sync(tmp_path: Path) -> None:
    """``merge_stage_times`` (the per-competitor sync path) must
    leave manual values alone -- otherwise pinning yourself in the
    Scoreboard would silently overwrite a duration the user typed in."""
    from splitsmith.match_project import MatchProject
    from splitsmith.ui.scoreboard.models import (
        CompetitorStageResult,
        CompetitorStageResults,
    )

    proj = MatchProject(name="t", schema_version=1)
    proj.init_placeholder_stages(count=2)
    proj.stages[0].time_seconds = 9.5
    proj.stages[0].time_seconds_manual = True

    results = CompetitorStageResults(
        competitorId=1,
        stages=[
            CompetitorStageResult(
                stage_number=1,
                time_seconds=42.0,
                scorecard_updated_at="2026-01-01T00:00:00+00:00",
            ),
            CompetitorStageResult(
                stage_number=2,
                time_seconds=11.0,
                scorecard_updated_at="2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    proj.merge_stage_times(results)

    assert proj.stages[0].time_seconds == 9.5
    assert proj.stages[0].time_seconds_manual is True
    assert proj.stages[1].time_seconds == 11.0
    assert proj.stages[1].time_seconds_manual is False


def test_scoreboard_sync_persists_full_scorecard() -> None:
    from splitsmith.match_project import MatchProject
    from splitsmith.ui.scoreboard.models import (
        CompetitorStageResult,
        CompetitorStageResults,
    )

    proj = MatchProject(name="t", schema_version=1)
    proj.init_placeholder_stages(count=1)
    results = CompetitorStageResults(
        competitorId=1,
        stages=[
            CompetitorStageResult(
                stage_number=1,
                time_seconds=12.34,
                scorecard_updated_at="2026-07-08T10:00:00+00:00",
                hit_factor=6.5,
                stage_points=80.0,
                stage_pct=95.5,
                alphas=10,
                charlies=2,
                deltas=0,
                misses=0,
                no_shoots=0,
                procedurals=1,
                dq=False,
            )
        ],
    )
    updated = proj.merge_stage_times(results)
    assert updated == 1
    stage = proj.stages[0]
    assert stage.scorecard is not None
    assert stage.scorecard.hit_factor == 6.5
    assert stage.scorecard.alphas == 10
    assert stage.scorecard.procedurals == 1
    assert stage.scorecard.dq is False


def test_reset_deletion_events_names_only_identified_shots() -> None:
    """Unit: an event keys on the shot id, so a shot without a usable one
    cannot be named. That is not a gap -- an unstamped shot makes the sync
    merge refuse the whole shot section out loud, so it cannot produce the
    silent re-adoption these events close."""
    from splitsmith.ui.server import _reset_deletion_events

    events = _reset_deletion_events(
        [
            {"id": "cand-4", "time": 5.5},
            {"time": 6.0},  # never stamped
            {"id": "", "time": 6.5},  # empty string is not an id
            {"id": 7, "time": 7.0},  # nor is an int from a hand-edited doc
            "not-a-shot",
        ]
    )
    assert [e["payload"]["id"] for e in events] == ["cand-4"]
    assert events[0]["kind"] == "marker_deleted"
    assert events[0]["payload"]["reason"] == "shot_detect_reset"
    assert _reset_deletion_events(None) == []
    assert _reset_deletion_events([]) == []


def test_candidate_high_water_reads_all_three_places_a_number_survives() -> None:
    """#842: the aliasing half. A number counts wherever it survives, and
    the event log is the one that matters -- it is append-only and never
    pruned, so it outlives both the candidate block (overwritten every
    run) and ``shots[]``."""
    from splitsmith.ui.server import _candidate_high_water

    assert _candidate_high_water({}) == 0
    assert _candidate_high_water(None) == 0
    block = {"_candidates_pending_audit": {"candidates": [{"candidate_number": 6}]}}
    assert _candidate_high_water(block) == 6
    assert _candidate_high_water({"shots": [{"candidate_number": 11}]}) == 11
    # The second surviving case on #842: only the log remembers cand-7.
    assert (
        _candidate_high_water({"audit_events": [{"kind": "marker_rejected", "payload": {"id": "cand-7"}}]})
        == 7
    )
    # Highest across all three, not the first one found.
    assert (
        _candidate_high_water(
            {
                "_candidates_pending_audit": {"candidates": [{"candidate_number": 3}]},
                "shots": [{"candidate_number": 5}],
                "audit_events": [{"kind": "marker_rejected", "payload": {"id": "cand-9"}}],
            }
        )
        == 9
    )
    # A manual shot's id is not a candidate number, and True is not 1.
    assert (
        _candidate_high_water(
            {
                "shots": [{"candidate_number": True}],
                "audit_events": [{"kind": "marker_added_manual", "payload": {"id": "manual-t6500"}}],
            }
        )
        == 0
    )


def test_calibrated_camera_models_endpoint_lists_shipped_models(tmp_path: Path) -> None:
    """GET /api/calibrated-camera-models reflects the shipped calibration.

    The shipped artifact today calibrates Insta360 GO 3S and Meta Vanguard
    (see PR #310 + #311). Future calibrations will grow the list; this
    test only asserts the contract -- a list of objects with key, make,
    model, amp_floor -- without locking in the exact entries.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get("/api/calibrated-camera-models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict) and "models" in body
    assert isinstance(body["models"], list)
    for row in body["models"]:
        assert set(row.keys()) >= {"key", "make", "model", "amp_floor"}
        assert isinstance(row["key"], str) and row["key"]
        assert isinstance(row["make"], str) and row["make"]
        assert isinstance(row["model"], str) and row["model"]
        assert isinstance(row["amp_floor"], float)


def test_calibrated_camera_models_never_loads_the_ensemble_runtime(tmp_path: Path, monkeypatch) -> None:
    """The camera list is a calibration read, not a model load (issue #667).

    Two failure modes this pins down at once: the endpoint used to warm
    several hundred MB of ONNX sessions on the first Ingest-screen fetch,
    and a missing model artifact turned the camera list into a 500 even
    though no model feeds the response. Stubbing the runtime loader to
    blow up reproduces the missing-artifact case exactly.
    """
    from splitsmith.ui import server as server_module

    client, _ = _seed_project_with_primary(tmp_path)

    def _boom() -> None:
        raise RuntimeError("ensemble runtime must not be built for a metadata read")

    monkeypatch.setattr(server_module, "_get_ensemble_runtime", _boom)

    resp = client.get("/api/calibrated-camera-models")
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"], "shipped calibration should list at least one model"


def test_put_fixture_audit_rejects_non_finite_float(tmp_path: Path) -> None:
    """#843, lab surface: the same trap, and worse -- an unguarded
    ``json.dumps`` writes the bare token, so the fixture on disk stops
    being standard JSON for every other reader of it."""
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "fixture-nonfinite.json"
    fixture.write_text(
        _json.dumps({"stage_number": 3, "shots": [{"shot_number": 1, "time": 0.6}]}),
        encoding="utf-8",
    )
    resp = client.put(
        f"/api/fixture/audit?path={fixture}",
        content='{"stage_number": 3, "shots": [{"shot_number": 1, "time": Infinity}]}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert _json.loads(fixture.read_text(encoding="utf-8"))["shots"][0]["time"] == 0.6


def test_fixture_audit_round_trip(tmp_path: Path) -> None:
    """The fixture endpoints read + write a JSON file in place. Closes #19's
    standalone review SPA -- localhost-only, no project context."""
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "blacksmith-h1.json"
    fixture.write_text(
        _json.dumps(
            {
                "stage_number": 3,
                "stage_name": "H1",
                "beep_time": 0.5,
                "shots": [{"shot_number": 1, "time": 0.6}],
            }
        ),
        encoding="utf-8",
    )

    resp = client.get(f"/api/fixture/audit?path={fixture}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["beep_time"] == 0.5
    assert body["shots"][0]["time"] == 0.6

    resp = client.put(
        f"/api/fixture/audit?path={fixture}",
        json={"stage_number": 3, "shots": [{"shot_number": 1, "time": 0.7}]},
    )
    assert resp.status_code == 200
    on_disk = _json.loads(fixture.read_text(encoding="utf-8"))
    assert on_disk["shots"][0]["time"] == 0.7
    backup = fixture.with_suffix(fixture.suffix + ".bak")
    assert backup.exists(), "previous version should be retained as .bak"
    backup_data = _json.loads(backup.read_text(encoding="utf-8"))
    assert backup_data["shots"][0]["time"] == 0.6


def test_fixture_audit_404_on_missing_path(tmp_path: Path) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    resp = client.get(f"/api/fixture/audit?path={tmp_path}/does-not-exist.json")
    assert resp.status_code == 404


def test_fixture_peaks_serves_sibling_wav(tmp_path: Path) -> None:
    """Peaks endpoint reads <path>.with_suffix('.wav') and returns the
    same shape the project peaks endpoint does."""
    import json as _json

    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "review.json"
    fixture.write_text(
        _json.dumps({"beep_time": 5.0, "shots": []}),
        encoding="utf-8",
    )
    audio = np.zeros(48_000, dtype="float32")
    audio[10_000:11_000] = 0.5
    sf.write(fixture.with_suffix(".wav"), audio, 48_000)

    resp = client.get(f"/api/fixture/peaks?path={fixture}&bins=64")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bins"] == 64
    assert body["trimmed"] is True
    assert body["beep_time"] == 5.0
    assert len(body["peaks"]) == 64


def test_fixture_review_signals_read_the_fixture_its_report_and_the_inventory(tmp_path: Path) -> None:
    """The Audit page's fixture review asks where to stop (spec 2026-10-14):
    the route reads the fixture, its sibling promotion report and its entry
    in the review inventory, and answers the signals."""
    import json as _json

    client, _ = _seed_project_with_primary(tmp_path)
    root = tmp_path / "repo"
    fixtures = root / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    fixture = fixtures / "snap.json"
    fixture.write_text(
        _json.dumps(
            {
                "beep_time": 5.0,
                "fixture_window_in_source": [1.0, 40.0],
                "anchor": {"fixture_slug": "a", "snap_window_ms": 60},
                "shots": [
                    {"shot_number": 1, "time": 6.0, "source": "promoted", "snap_displacement_ms": 59.0}
                ],
                "stage_rounds": {"expected": 1},
            }
        ),
        encoding="utf-8",
    )
    (fixtures / "snap-promotion-report.json").write_text(
        _json.dumps({"per_shot": [{"snapped_time": 7.0}]}), encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "fixture_review_inventory.json").write_text(
        _json.dumps(
            {"fixtures": [{"slug": "snap", "moves": [{"time": 6.0, "suggested": 5.99, "move_ms": -10.0}]}]}
        ),
        encoding="utf-8",
    )

    resp = client.get(f"/api/fixture/review-signals?path={fixture}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # A snap is seated on the rise foot: the inventory's stricter onset is not shown.
    assert body["moves"] == []
    assert body["echoes"] == []
    assert body["edges"] == [{"time": 6.0, "displacement_ms": 59.0}]
    assert body["reference"] == {"kind": "snap", "times": [6.0]}
    assert body["rounds"] == 1


def test_fixture_peaks_reach_one_millisecond_on_a_long_stage(tmp_path: Path) -> None:
    """The lab review zooms until one bin is 1 ms, so a 60 s fixture
    needs 60 000 bins (the old 8192 cap stopped at ~7 ms)."""
    import json as _json

    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "review.json"
    fixture.write_text(_json.dumps({"beep_time": 5.0, "shots": []}), encoding="utf-8")
    sf.write(fixture.with_suffix(".wav"), np.zeros(60 * 48_000, dtype="float32"), 48_000)

    resp = client.get(f"/api/fixture/peaks?path={fixture}&bins=60000")
    assert resp.status_code == 200
    assert len(resp.json()["peaks"]) == 60000


def test_fixture_audio_serves_sibling_wav(tmp_path: Path) -> None:
    import soundfile as sf

    client, _ = _seed_project_with_primary(tmp_path)
    fixture = tmp_path / "review.json"
    fixture.write_text("{}", encoding="utf-8")
    sf.write(fixture.with_suffix(".wav"), np.zeros(100, dtype="float32"), 48_000)

    resp = client.get(f"/api/fixture/audio?path={fixture}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content[:4] == b"RIFF"


def test_fixture_video_serves_arbitrary_path(tmp_path: Path) -> None:
    """Localhost convention: any path the user passes for --video is served
    through. The standalone review SPA had the same trust model."""
    client, _ = _seed_project_with_primary(tmp_path)
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"FAKE_VIDEO_BYTES")
    resp = client.get(f"/api/fixture/video?path={video}")
    assert resp.status_code == 200
    assert resp.content == b"FAKE_VIDEO_BYTES"
    assert resp.headers["content-type"].startswith("video/")


def test_create_app_records_recent_project(tmp_path: Path, _user_config_home: Path) -> None:
    from splitsmith import user_config

    _match_create_app(project_root=tmp_path / "match", project_name="Recent Match")
    recent = user_config.get_recent_projects()
    assert len(recent) == 1
    assert recent[0].name == "Recent Match"
    assert Path(recent[0].path) == (tmp_path / "match").resolve()


def test_unbound_create_app_health_reports_unbound() -> None:
    """`splitsmith ui` with no --project boots unbound; /api/health
    reports it so the SPA can route to the picker."""
    app = create_app()
    client = _MatchClient(app)

    body = client.get("/api/health").json()
    assert body["bound"] is False
    assert body["project_name"] is None
    assert body["project_root"] is None


def test_load_env_files_picks_up_user_config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSI token (and any other ``SPLITSMITH_*`` var) can live in
    ``~/.splitsmith/.env`` -- it's per-user, not per-project. Project /
    cwd files still win because they're loaded earlier with
    ``override=False``.
    """
    pytest.importorskip("dotenv")
    from splitsmith import user_config
    from splitsmith.ui import server as server_mod

    home = tmp_path / "user-home"
    home.mkdir()
    (home / ".env").write_text("SPLITSMITH_TEST_TOKEN=from-user-config\n")
    monkeypatch.setenv(user_config.ENV_HOME, str(home))
    monkeypatch.delenv(user_config.ENV_DISABLE, raising=False)
    monkeypatch.delenv("SPLITSMITH_TEST_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    project_root = tmp_path / "no-env-project"
    project_root.mkdir()
    loaded = server_mod._load_env_files(project_root)
    assert any(p.parent.resolve() == home.resolve() for p in loaded)

    import os

    assert os.environ.get("SPLITSMITH_TEST_TOKEN") == "from-user-config"


def test_server_features_reports_local_mode_by_default(tmp_path: Path) -> None:
    """Without ``SPLITSMITH_MODE=hosted`` the SPA should see local mode
    so it keeps showing the host filesystem pickers + project-folder
    input (issue #425)."""
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.get("/api/server/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local"
    assert body["lab"] is False


def test_server_features_reports_hosted_mode_when_env_set(tmp_path: Path, monkeypatch) -> None:
    """``SPLITSMITH_MODE=hosted`` flips the discriminator; SPA reads
    this on boot to suppress filesystem-picker UX that's meaningless
    against an ephemeral hosted container (issue #425)."""
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    # We test *only* the feature flag projection; the full hosted
    # wiring (Postgres-backed stores) is exercised in the hosted
    # integration smoke (``pytest -m docker``). create_app already
    # checks SPLITSMITH_MODE up front, so we have to monkeypatch the
    # wiring helper to a no-op for this isolated test.
    from splitsmith.ui import server as server_mod

    monkeypatch.setattr(server_mod, "_apply_hosted_mode_wiring", lambda _state: None)

    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.get("/api/server/features")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "hosted"


def test_promote_against_fixture_endpoint_lab_gated(tmp_path: Path) -> None:
    """Without ``lab_enabled``, the new endpoint isn't registered (404 / 405)."""
    app = _match_create_app(project_root=tmp_path / "match-noflag", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/lab/projects/1/videos/abc/promote-against-fixture",
        json={"anchor_slug": "stage-shots-foo-2026-stage1", "mount": "hand", "position": "shooter"},
    )
    # 404 (no such route) or 405 (no such method on a partial match) --
    # both prove the endpoint is unreachable without --lab.
    assert resp.status_code in (404, 405)


def test_promote_against_fixture_endpoint_validates_anchor_exists(
    tmp_path: Path,
) -> None:
    """Lab-enabled, but the anchor slug doesn't exist on disk -> 404."""
    from splitsmith.match_project import MatchProject

    project_root = tmp_path / "match-anchor"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(
        project_root=project_root,
        project_name="x",
        lab_enabled=True,
    )
    client = _MatchClient(app)
    # Seed a stage + a video with a beep_time so we get past the
    # earlier validation gates.

    project = MatchProject.load(_shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="A", time_seconds=10.0)]
    src = _shooter_root / "raw" / "VID.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\x00")
    video = project.register_video(src, _shooter_root)
    project.assign_video(video.path, to_stage_number=1, role="primary")
    primary = project.stages[0].primary()
    assert primary is not None
    primary.beep_time = 5.0
    project.save(_shooter_root)

    resp = client.post(
        f"/api/lab/projects/me/1/videos/{primary.video_id}/promote-against-fixture",
        json={
            "anchor_slug": "stage-shots-does-not-exist-2026-stage1",
            "mount": "hand",
            "position": "shooter",
        },
    )
    assert resp.status_code == 404
    assert "anchor fixture not found" in resp.json()["detail"]


def test_export_stage_accepts_a_scoreboard_time_without_a_timestamp(tmp_path: Path) -> None:
    """A stage time with no ``scorecard_updated_at`` and no manual flag is
    still exportable (#613).

    ``apply_stage_time_results`` sets ``time_seconds`` even when the row's
    ``scorecard_updated_at`` fails to parse, so that combination is reachable
    from a plain scoreboard import. The CLI planner has always been willing
    to cut it; the server used to reject it as a placeholder, which is the
    divergence the shared ``ready_to_trim`` rule closes.
    """
    from splitsmith.match_project import MatchProject

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    stage = MatchProject.load(project_root / "shooters" / "me").stages[0]
    assert stage.time_seconds > 0
    assert stage.scorecard_updated_at is None
    assert stage.time_seconds_manual is False

    resp = _export_csv_only(client)
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"


def test_export_stage_400_on_untouched_placeholder(tmp_path: Path) -> None:
    """The gate that survives is the one that means something: a stage with
    no duration cannot be trimmed, because the trim is sized by it.

    Replaces an older test of the same name whose fixture carried
    ``time_seconds=10.0`` -- it asserted the stricter rule (#613) rather
    than the placeholder case its name described.
    """
    from splitsmith.match_project import MatchProject

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.stages[0].time_seconds = 0.0
    project.save(shooter_root)

    resp = _export_csv_only(client)
    assert resp.status_code == 400
    assert "placeholder" in resp.json()["detail"]


def test_export_stage_400_on_a_skipped_stage(tmp_path: Path) -> None:
    """New with the shared rule (#613): a stage marked skipped is refused.

    The endpoint never checked ``skipped`` before -- only the SPA's stage
    list did, by filtering it out. Now that all three surfaces read the same
    ``trim_blocker``, a direct API call gets the same answer the UI implies,
    with a message that names the actual problem.
    """
    from splitsmith.match_project import MatchProject

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.stages[0].skipped = True
    project.save(shooter_root)

    resp = _export_csv_only(client)
    assert resp.status_code == 400
    assert "skipped" in resp.json()["detail"]


def test_trims_only_export_reports_a_clean_run(tmp_path: Path, monkeypatch) -> None:
    """A successful trims-only export must not announce an anomaly (#616).

    The compare grid needs a beep and a stage time, never shots, so an
    audit with an empty ``shots[]`` is the *designed* state here. Reporting
    ``report.detect_anomalies``' "No shots detected in the stage window"
    turned every clean trims-only run into a warning -- and pointed the
    user at a report.txt this mode never writes.
    """
    client, _ = _seed_trims_only_stage(tmp_path, monkeypatch)

    final = _wait_for_job(client, _post_trims_only(client).json()["id"])

    assert final["status"] == "succeeded", final
    assert final["result"]["anomalies"] == []
    assert final["message"] == "Done: trim"
    assert "report.txt" not in final["message"]


def test_trims_only_export_still_reports_a_failed_secondary(tmp_path: Path, monkeypatch) -> None:
    """Filtering shot findings must not swallow an artefact that failed."""
    from splitsmith import trim
    from splitsmith.match_project import MatchProject

    client, project_root = _seed_trims_only_stage(tmp_path, monkeypatch)
    shooter_root = project_root / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    secondary_src = shooter_root / "raw" / "VID1_cam2.mp4"
    secondary_src.write_bytes(b"\x00")
    registered = project.register_video(secondary_src, project_root)
    project.assign_video(registered.path, to_stage_number=1, role="secondary")
    secondary = next(v for v in project.stage(1).videos if v.role == "secondary")
    secondary.beep_time = 5.0
    secondary_id = secondary.video_id
    project.save(shooter_root)

    real_trim = trim.trim_video

    def flaky_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        if "_cam_" in Path(output_path).name:
            raise trim.FFmpegError("secondary codec unsupported")
        return real_trim(source, output_path, **kwargs)

    monkeypatch.setattr(trim, "trim_video", flaky_trim_video)

    final = _wait_for_job(client, _post_trims_only(client).json()["id"])

    assert final["status"] == "succeeded", final
    assert final["result"]["anomalies"] == [
        f"secondary cam {secondary_id} trim not written: secondary codec unsupported"
    ]
    # The shot finding stays filtered; only the real failure is reported.
    assert final["message"] == "Done: trim (1 anomaly)"


def test_export_that_writes_no_trim_at_all_fails_the_job(tmp_path: Path, monkeypatch) -> None:
    """Zero deliverables from a trim request is a failure, not a quiet "Done".

    The endpoint 424s an unreachable source before queueing, so reaching
    this branch means ffmpeg itself blew up. Reporting it as a successful
    job that wrote "nothing" hides a total failure behind a green row.
    """
    from splitsmith import trim

    client, project_root = _seed_trims_only_stage(tmp_path, monkeypatch)
    (project_root / "shooters" / "me" / "exports" / "stage1_stage-1_trimmed.mp4").unlink()

    def failing_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        raise trim.FFmpegError("moov atom not found")

    monkeypatch.setattr(trim, "trim_video", failing_trim_video)

    final = _wait_for_job(client, _post_trims_only(client).json()["id"])

    assert final["status"] == "failed", final
    assert "moov atom not found" in final["error"]


def test_dev_model_endpoint_returns_calibration_metadata(tmp_path: Path) -> None:
    """/api/dev/model reads the shipped ensemble_calibration.json and
    formats it for the dev shell's model chip + workflow stepper. The
    endpoint is always available (no --lab gate) because the shell
    needs the model version on every page load."""
    root = tmp_path / "match"
    MatchProject.init(root, name="Dev Match").save(root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/dev/model")
    assert resp.status_code == 200
    body = resp.json()
    # active_version derives from built_at as vYYYY.MM.DD.
    assert body["active_version"].startswith("v")
    # Recall is whatever voter_c_target_recall is in the shipped json.
    assert 0.0 < body["recall"] <= 1.0
    # Step counts always present, even when zero.
    assert {"corpus", "review", "validate_runs", "retrain"} <= set(body["step_counts"])
    # Per-class CV metrics recorded by the build ride along so the
    # Retrain/Validate pages can show real numbers without an eval run.
    assert body["metrics_by_class"], "shipped calibration carries voter_c_metrics_by_camera_class"
    a_class = next(iter(body["metrics_by_class"].values()))
    assert {"voter_c_precision_cv", "voter_c_recall_cv", "voter_c_f1_cv"} <= set(a_class)


def test_dev_models_endpoint_maps_the_corpus_onto_the_shipped_heads(tmp_path: Path) -> None:
    """/api/dev/models feeds the lab's Models page: every fixture with the
    head it calibrated, and both shipped heads with their parameters."""
    root = tmp_path / "match"
    MatchProject.init(root, name="Dev Match").save(root)
    client = _MatchClient(_match_create_app(project_root=root, project_name="ignored"))

    resp = client.get("/api/dev/models")
    assert resp.status_code == 200
    body = resp.json()
    heads = {h["camera_class"]: h for h in body["ensemble"]["heads"]}
    assert set(heads) == {"headcam", "handheld"}
    calibrated = {f["stem"] for f in body["fixtures"] if f["voter_c"]}
    assert len(calibrated) == heads["headcam"]["n_fixtures"] + heads["handheld"]["n_fixtures"]
    assert body["ensemble"]["hyperparameters"]["max_depth"] >= 1


def test_dev_review_queue_buckets_fixtures(tmp_path: Path) -> None:
    """/api/dev/review-queue groups fixtures into pending/flagged/done
    so the dev review page can render its three queue sections."""
    root = tmp_path / "match"
    MatchProject.init(root, name="Dev Match").save(root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/dev/review-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["pending"], list)
    assert isinstance(body["flagged"], list)
    assert isinstance(body["done"], list)
    # Every item must have a stable shape.
    for bucket in (body["pending"], body["flagged"], body["done"]):
        for item in bucket:
            assert {"slug", "audit_path", "source", "status", "n_shots"} <= set(item)


def test_dev_review_queue_includes_batch_promoted_unlabeled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch-promoted fixtures (promoted_at, no anchor block) enter the
    pending bucket until a human label pass lands. Before this rule the
    queue keyed on anchor_slug alone, so all 102 batch-promoted corpus
    fixtures silently skipped review."""
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    body = client.get("/api/dev/review-queue").json()
    pending = {i["slug"]: i for i in body["pending"]}
    done = {i["slug"]: i for i in body["done"]}
    assert set(pending) == {"stage-shots-hfo-masters-2026-stage1-s0fe3d797"}
    item = pending["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]
    assert item["promoted_at"] == "2026-08-14T12:38:39+00:00"
    assert item["source_label"] == "Promoted from match"
    # Labeled batch-promote -> done; hand-dropped legacy -> done.
    assert set(done) == {
        "stage-shots-hfo-masters-2026-stage2-s0fe3d797",
        "stage-shots-blacksmith-2026-stage6",
    }
    # The model chip's review counter follows the same rule.
    model = client.get("/api/dev/model").json()
    assert model["step_counts"]["review"] == 1


def test_dev_review_queue_counts_walk_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Continue resumes a walk already started: the queue says how far each got."""
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    path = tmp_path / "fixtures" / "stage-shots-hfo-masters-2026-stage1-s0fe3d797.json"
    doc = json.loads(path.read_text())
    doc["audit_events"] = [
        {"kind": "walk_decided", "payload": {"stop": "a"}},
        {"kind": "walk_decided", "payload": {"stop": "b"}},
        {"kind": "marker_moved", "payload": {}},
    ]
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    pending = {i["slug"]: i for i in client.get("/api/dev/review-queue").json()["pending"]}
    assert pending["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]["walk_decisions"] == 2


def test_dev_review_queue_puts_doubtful_shot_times_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixtures whose shot times need checking (#1363) lead the pending
    list in the inventory's priority order, with its reasons; label-pass
    items follow."""
    import splitsmith.lab.core as lab_core

    fixtures = tmp_path / "tests" / "fixtures"
    _seed_review_state_fixtures(fixtures)
    for slug in (
        "stage-shots-hfo-masters-2026-stage1-s97dcec94-go3s",
        "stage-shots-hfo-masters-2026-stage2-s97dcec94-go3s",
    ):
        (fixtures / f"{slug}.json").write_text(
            json.dumps({"anchor": {"fixture_slug": "a"}, "shots": [{"time": 5.5}]})
        )
        (fixtures / f"{slug}.wav").write_bytes(b"")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "fixture_review_inventory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fixtures": [
                    {
                        "slug": "stage-shots-hfo-masters-2026-stage1-s97dcec94-go3s",
                        "priority": 120.0,
                        "reasons": ["a"],
                    },
                    {
                        "slug": "stage-shots-hfo-masters-2026-stage2-s97dcec94-go3s",
                        "priority": 170.0,
                        "reasons": ["b"],
                    },
                ],
            }
        )
    )
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", fixtures)
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))

    pending = client.get("/api/dev/review-queue").json()["pending"]
    assert [p["slug"] for p in pending] == [
        "stage-shots-hfo-masters-2026-stage2-s97dcec94-go3s",
        "stage-shots-hfo-masters-2026-stage1-s97dcec94-go3s",
        "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
    ]
    assert pending[0]["review_status"] == "needs_review"
    assert pending[0]["priority"] == 170.0 and pending[0]["reasons"] == ["b"]
    assert pending[2]["review_status"] == "reviewed"


def test_dev_review_confirm_clears_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/dev/review-queue/{slug}/confirm stamps review.confirmed_at
    on the fixture JSON, moving it from the pending bucket to done and
    dropping the model chip's review counter -- the "Approve to corpus"
    button's backend."""
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    slug = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    assert slug in {i["slug"] for i in client.get("/api/dev/review-queue").json()["pending"]}

    resp = client.post(f"/api/dev/review-queue/{slug}/confirm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == slug
    assert body["confirmed_at"]

    queue = client.get("/api/dev/review-queue").json()
    assert slug not in {i["slug"] for i in queue["pending"]}
    assert slug in {i["slug"] for i in queue["done"]}
    assert client.get("/api/dev/model").json()["step_counts"]["review"] == 0

    # Unknown slug -> 404, nothing written.
    assert client.post("/api/dev/review-queue/nope/confirm").status_code == 404


def test_dev_review_confirm_records_the_walk_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review walk signs off with its version, so a flawed walk can
    re-flag exactly the fixtures it signed off; a plain sign-off (the editor's
    Mark reviewed) leaves no method behind."""
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    slug = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    path = tmp_path / "fixtures" / f"{slug}.json"

    assert client.post(f"/api/dev/review-queue/{slug}/confirm", json={"method": "walk-1"}).status_code == 200
    assert json.loads(path.read_text())["review"]["method"] == "walk-1"

    assert client.post(f"/api/dev/review-queue/{slug}/confirm").status_code == 200
    assert "method" not in json.loads(path.read_text())["review"]


def test_dev_review_queue_names_the_source_video_only_when_it_is_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk shows the source video beside the waveform; a fixture whose
    video sits on an unmounted volume gets none rather than a broken player."""
    import splitsmith.lab.core as lab_core

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    video = tmp_path / "IMG_0001.MOV"
    video.write_bytes(b"x")
    for slug, source in (
        ("stage-shots-hfo-masters-2026-stage1-s0fe3d797", str(video)),
        ("stage-shots-hfo-masters-2026-stage2-s0fe3d797", "/Volumes/Gone/IMG_0002.MOV"),
    ):
        (fixtures / f"{slug}.json").write_text(
            json.dumps({"promoted_at": "2026-08-14T12:38:39+00:00", "source_video": source, "shots": []})
        )
        (fixtures / f"{slug}.wav").write_bytes(b"")
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", fixtures)
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))

    body = client.get("/api/dev/review-queue").json()
    items = {i["slug"]: i for i in body["pending"] + body["done"]}
    assert items["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]["video_path"] == str(video)
    assert items["stage-shots-hfo-masters-2026-stage2-s0fe3d797"]["video_path"] is None


def test_lab_fixtures_reports_calibration_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/lab/fixtures augments each record with ``in_calibration`` by
    comparing slugs against the active artifact's calibration_fixtures."""
    from types import SimpleNamespace

    import splitsmith.ensemble as ensemble_pkg
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    monkeypatch.setattr(
        ensemble_pkg,
        "load_calibration",
        lambda: SimpleNamespace(calibration_fixtures=["stage-shots-blacksmith-2026-stage6"]),
    )
    app = _match_create_app(project_root=tmp_path / "match", project_name="x", lab_enabled=True)
    client = _MatchClient(app)

    body = client.get("/api/lab/fixtures").json()
    by_slug = {r["slug"]: r for r in body}
    assert by_slug["stage-shots-blacksmith-2026-stage6"]["in_calibration"] is True
    assert by_slug["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]["in_calibration"] is False
    # The review-state fields ride along for the Corpus page's tags.
    assert by_slug["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]["promoted_at"] is not None
    assert by_slug["stage-shots-hfo-masters-2026-stage2-s0fe3d797"]["n_labeled_shots"] == 1


def test_shooter_root_no_match_singleton_fallback(tmp_path: Path, _user_config_home: Path) -> None:
    """Tier 1 of doc 10 (step 2): with a Match folder bound on the
    singleton, ``state.shooter_root(slug)`` still refuses to resolve
    without a per-request ContextVar. The Match-folder singleton
    fallback is gone; only the legacy single-shooter fallback
    survives (covered separately by the legacy test pattern).
    """
    from splitsmith.ui.state import _no_project_error

    app, _bound_match, _ = _make_match_app(tmp_path)
    state = app.state.splitsmith_state

    # The match IS registered (`_make_match_app` calls
    # `_register_match_at`).
    assert state.matches.known_ids(), "expected the test match to be registered"

    # Yet shooter_root refuses to resolve a known slug without the
    # ContextVar set by the alias middleware.
    with pytest.raises(HTTPException) as exc:
        state.shooter_root("mathias")
    assert exc.value.status_code == 409
    expected_code = _no_project_error().detail["code"]
    assert exc.value.detail["code"] == expected_code


def test_match_root_no_singleton_fallback(tmp_path: Path, _user_config_home: Path) -> None:
    """Tier 1 of doc 10: ``state.match_root`` reads only from the
    per-request ContextVar set by the alias middleware. Even when a
    project is bound on the singleton, a bare-path request must 409
    rather than silently resolve via ``_bound_root``.
    """
    from splitsmith.ui.state import _no_project_error

    app, _bound_match, _ = _make_match_app(tmp_path)
    state = app.state.splitsmith_state

    # The match IS registered (`_make_match_app` calls
    # `_register_match_at`).
    assert state.matches.known_ids(), "expected the test match to be registered"

    # Yet match_root refuses to resolve without a ContextVar.
    with pytest.raises(HTTPException) as exc:
        _ = state.match_root
    assert exc.value.status_code == 409
    # Mirror the error code _no_project_error emits so a future
    # rename of the code keeps this test honest.
    expected_code = _no_project_error().detail["code"]
    assert exc.value.detail["code"] == expected_code


def test_match_id_alias_contextvar_isolates_per_request(tmp_path: Path, _user_config_home: Path) -> None:
    """``state.shooter_root(slug)`` resolves Match-folder requests
    only via the per-request ``current_match_root`` ContextVar.

    Tier 1 of doc 10 (step 2): a bound Match folder no longer
    silently resolves shooter slugs via the singleton when the
    request didn't carry an ``/api/matches/{match_id}/`` prefix.
    The ContextVar is the only path; bare access 409s.
    """
    from splitsmith import match_model, user_config
    from splitsmith.ui.server import current_match_root

    app, _bound_match, _bound_root = _make_match_app(tmp_path)
    other_root = tmp_path / "other-match"
    other = match_model.Match.init(other_root, name="Other Match")
    other_shooter = match_model.Shooter(slug="alice", name="Alice")
    other.add_shooter(other_root, other_shooter)
    user_config.record_project_open(other_root, other.name, kind="match")

    state = app.state.splitsmith_state

    # No contextvar set -> Match-folder singleton no longer
    # answers; 409 ``no_project`` regardless of which slug is
    # requested.
    with pytest.raises(HTTPException) as exc:
        state.shooter_root("alice")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "no_project"

    # Set the contextvar to point at the OTHER match (simulating
    # what the middleware does on /api/matches/{other.match_id}/).
    # ``alice`` resolves under the other match's shooter dir.
    token = current_match_root.set(other_root)
    try:
        resolved = state.shooter_root("alice")
        assert resolved == match_model.Match.shooter_root(other_root, "alice")
    finally:
        current_match_root.reset(token)

    # After reset, bare access 409s again -- the contextvar didn't
    # leak across "requests" and the singleton doesn't paper over.
    with pytest.raises(HTTPException) as exc:
        state.shooter_root("mathias")
    assert exc.value.status_code == 409


def test_hosted_boot_lifespan_runs_on_app_startup(monkeypatch) -> None:
    """create_app registers boot_retrigger so it actually runs at startup.

    Regression: app.add_event_handler was removed in starlette 1.3; the
    hosted serve boot crashed with AttributeError the first time the
    launcher env was configured. Exercise the registration mechanism
    against a real FastAPI app instead of trusting the method name.
    """
    from fastapi import FastAPI

    from splitsmith.ui.server import _hosted_boot_lifespan

    # Railway env config must not leak into this test - without it the
    # lifespan's seeding leg stays off and only the retrigger leg matters.
    for var in ("SPLITSMITH_WORKER_TRIGGER_TOKEN", "SPLITSMITH_WORKER_SERVICE_ID"):
        monkeypatch.delenv(var, raising=False)

    calls: list[str] = []

    async def fake_retrigger() -> None:
        calls.append("ran")

    class _FakeState:
        boot_retrigger = staticmethod(fake_retrigger)
        workers_store = None
        db_engines = None

    class _FakeStateNone:
        boot_retrigger = None
        workers_store = None
        db_engines = None

    # Helper must return None when no hook is wired.
    assert _hosted_boot_lifespan(_FakeStateNone()) is None

    # Helper must return a lifespan that calls the hook on startup.
    lifespan = _hosted_boot_lifespan(_FakeState())
    assert lifespan is not None

    app = FastAPI(lifespan=lifespan)
    with TestClient(app):
        pass

    assert calls == ["ran"]


def test_hosted_boot_lifespan_returned_for_seeding_alone(monkeypatch) -> None:
    """The lifespan must exist when only the railway-row seeding applies
    (boot_retrigger None), and it must run ensure_railway_row on startup -
    a fresh deploy otherwise has an empty workers table and the wake path
    is silently dead."""
    from fastapi import FastAPI

    from splitsmith.ui.server import _hosted_boot_lifespan

    monkeypatch.setenv("SPLITSMITH_WORKER_TRIGGER_TOKEN", "t")
    monkeypatch.setenv("SPLITSMITH_WORKER_SERVICE_ID", "s")
    monkeypatch.setenv("SPLITSMITH_WORKER_ENVIRONMENT_ID", "e")

    calls: list[str] = []
    seeded_versions: list[str | None] = []

    class _FakeWorkersStore:
        async def ensure_railway_row(self, *, version: str | None = None) -> None:
            calls.append("seeded")
            seeded_versions.append(version)

    class _FakeState:
        boot_retrigger = None
        workers_store = _FakeWorkersStore()
        db_engines = None

    lifespan = _hosted_boot_lifespan(_FakeState())
    assert lifespan is not None

    with TestClient(FastAPI(lifespan=lifespan)):
        pass

    assert calls == ["seeded"]
    # The running deploy's version is stamped onto the railway row at boot.
    from splitsmith import __version__

    assert seeded_versions == [__version__]


@pytest.mark.parametrize("request_style", [{}, {"overlay_variant": "plate"}], ids=["classic", "plate"])
@pytest.mark.parametrize("edit", [_edit_shot_time, _confirm_a_region], ids=["shot-time", "confirmed-region"])
def test_match_export_redraws_an_overlay_when_its_audit_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_style: dict, edit: Callable[[dict], None]
) -> None:
    """The settings record carries the audit revision the overlay was drawn
    from: an unchanged audit reuses the MOV, an edited one draws it again,
    whatever the style. Before, a shot edit or a confirmed region shipped the
    old overlay because the record held only the style."""
    import json as _json

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    audit_path = root / "shooters" / "me" / "audit" / "stage1.json"

    assert _run_overlay_match_export(client, monkeypatch, request_style), "first export draws"
    assert _run_overlay_match_export(client, monkeypatch, request_style) == [], "unchanged audit reuses"

    doc = _json.loads(audit_path.read_text(encoding="utf-8"))
    edit(doc)
    audit_path.write_text(_json.dumps(doc), encoding="utf-8")
    assert _run_overlay_match_export(client, monkeypatch, request_style), "edited audit redraws"
    assert _run_overlay_match_export(client, monkeypatch, request_style) == [], "and then reuses again"


@pytest.mark.parametrize("edited", ["_shared/hud.js", "splitsmith/hud-plate.html"])
def test_match_export_redraws_a_template_overlay_when_its_template_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited: str
) -> None:
    """A HUD style's MOV is what its template and the shared scripts drew:
    the record carries their identity, so an edit to either draws it again
    (#1337 review). Edits a copy of the shipped Looks, never the files."""
    import shutil

    from splitsmith import looks

    copy = tmp_path / "shipped-looks"
    shutil.copytree(looks.shipped_looks_dir(), copy)
    monkeypatch.setattr(looks, "shipped_looks_dir", lambda: copy)
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    style = {"overlay_variant": "plate"}

    assert _run_overlay_match_export(client, monkeypatch, style), "first export draws"
    assert _run_overlay_match_export(client, monkeypatch, style) == [], "unchanged template reuses"
    path = copy / edited
    path.write_bytes(path.read_bytes() + b"\n<!-- edited -->\n")
    assert _run_overlay_match_export(client, monkeypatch, style), "edited template redraws"
    assert _run_overlay_match_export(client, monkeypatch, style) == [], "and then reuses again"


@pytest.mark.parametrize("request_style", [{}, {"overlay_variant": "plate"}], ids=["classic", "plate"])
@pytest.mark.parametrize("edit", [_edit_look_palette, _edit_look_mono_font], ids=["palette", "font"])
def test_match_export_redraws_an_overlay_when_its_look_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_style: dict, edit: Callable[[dict], None]
) -> None:
    """Every style draws in the Look's palette and faces (Classic's clock
    too), so the record carries their identity: an unchanged Look reuses the
    MOV, a palette or font edit draws it again (#1403). Before, the record
    held only the Look's name and the old colours shipped. Edits a copy of
    the shipped Looks, never the files."""
    import json as _json
    import shutil

    from splitsmith import looks

    copy = tmp_path / "shipped-looks"
    shutil.copytree(looks.shipped_looks_dir(), copy)
    monkeypatch.setattr(looks, "shipped_looks_dir", lambda: copy)
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)

    assert _run_overlay_match_export(client, monkeypatch, request_style), "first export draws"
    assert _run_overlay_match_export(client, monkeypatch, request_style) == [], "unchanged Look reuses"
    manifest_path = copy / "splitsmith" / "look.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    edit(manifest)
    manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")
    assert _run_overlay_match_export(client, monkeypatch, request_style), "edited Look redraws"
    assert _run_overlay_match_export(client, monkeypatch, request_style) == [], "and then reuses again"


@pytest.mark.parametrize("request_style", [{}, {"overlay_variant": "plate"}], ids=["classic", "plate"])
@pytest.mark.parametrize("recorded", ["short-head", "pre-fix"])
def test_match_export_redraws_a_short_head_overlay_drawn_before_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request_style: dict, recorded: str
) -> None:
    """A stage whose source has 3 s before the beep (5 s pre-buffer): an
    overlay whose record says it drew the beep at 3 s is reused; one drawn
    before #1430 (no ``short_head``, so drawn at 5 s, late) is drawn again,
    and the redraw's own record is then reused."""
    import json as _json

    from splitsmith.overlay.hud import HudOptions, overlay_settings
    from splitsmith.ui import exports as exports_mod

    client, root = _seed_match_export_project(tmp_path, stage_count=1, beep_time=3.0)
    _stub_match_export_probe(monkeypatch)
    exports_dir = root / "shooters" / "me" / "exports"
    (exports_dir / "stage1_stage-1_overlay.mov").write_bytes(b"old")
    settings = overlay_settings(
        look="splitsmith",
        variant=request_style.get("overlay_variant", "default"),
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision=exports_mod.overlay_audit_revision(root / "shooters" / "me" / "audit" / "stage1.json"),
        short_head=3.0 if recorded == "short-head" else None,
    )
    (exports_dir / "stage1_stage-1_overlay.json").write_text(_json.dumps(settings))

    calls = _run_overlay_match_export(client, monkeypatch, request_style)
    assert bool(calls) is (recorded == "pre-fix")
    assert _run_overlay_match_export(client, monkeypatch, request_style) == [], "then reuses"
