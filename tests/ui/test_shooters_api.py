"""Tests for the per-shooter routes (``ui/shooters_api.py``)."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from splitsmith import raw_link
from splitsmith.automation import AutomationOverride
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.thumbnail import ThumbnailError
from splitsmith.ui.server import create_app
from splitsmith.video_probe import ProbeError, ProbeResult
from tests.conftest import scaffold_match as _scaffold_match
from tests.conftest import submit_fn
from tests.ui._helpers import (
    _EXAMPLES_DIR,
    _SCOREBOARD_FIXTURES_DIR,
    _build_project_with_primary,
    _combined_divisions,
    _edit_shot_time,
    _enable_auto_beep,
    _load_v1_match_fixture,
    _match_create_app,
    _MatchClient,
    _primary_cache_paths,
    _run_overlay_match_export,
    _scan_paths,
    _seed_cleanup_project,
    _seed_match_export_project,
    _seed_project_with_primary,
    _seed_unrebuildable_export,
    _stub_detect,
    _stub_match_export_probe,
    _wait_for_job,
    _wait_for_jobs_to_drain,
)


def test_get_project_returns_full_dump(tmp_path: Path) -> None:
    root, _shooter_root = _scaffold_match(tmp_path, name="Get Project Match", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.competitor_name = "API Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(_shooter_root)

    app = _match_create_app(project_root=root, project_name="ignored, project already exists")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/project")
    assert resp.status_code == 200
    body = resp.json()
    # The project name from disk wins, not the create_app argument.
    assert body["name"] == "Get Project Match"
    assert body["competitor_name"] == "API Tester"
    assert len(body["stages"]) == 1
    assert body["stages"][0]["videos"][0]["role"] == "primary"
    # The stage is set up (video + time) but no audit JSON exists, so
    # the SPA should see ``ready`` -- not ``audited`` like the old
    # heuristic claimed.
    assert body["stages"][0]["status"] == "ready"


def test_get_project_marks_stage_audited_after_save_event(tmp_path: Path) -> None:
    """End-to-end check that the status enrichment reflects a save event
    in the audit JSON. The Home / sidebar / shooter chips all read this
    field, so getting it right at the HTTP layer is the single
    contract we have to keep."""
    import json as _json

    root, _shooter_root = _scaffold_match(tmp_path, name="Audited Match", subdir="match-audited")
    project = MatchProject.load(_shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="S1",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(_shooter_root)
    audit_dir = project.audit_path(_shooter_root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        _json.dumps(
            {
                "stage_number": 1,
                "audit_events": [
                    {"ts": "2026-05-22T12:00:00Z", "kind": "shot_detect_run"},
                    {"ts": "2026-05-22T12:01:00Z", "kind": "save"},
                ],
            }
        ),
        encoding="utf-8",
    )

    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)
    body = client.get("/api/shooters/me/project").json()
    assert body["stages"][0]["status"] == "audited"


def test_import_scoreboard_endpoint(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    sb = {
        "match": {"id": "27046", "name": "Imported Match"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
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
    resp = client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Imported Match"
    assert body["scoreboard_match_id"] == "27046"
    assert body["competitor_name"] == "Tester"
    assert len(body["stages"]) == 1


def test_import_scoreboard_409_on_conflict(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    sb = {
        "match": {"id": "1", "name": "First"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }
    assert client.post("/api/shooters/me/scoreboard/import", json={"data": sb}).status_code == 200
    resp = client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    assert resp.status_code == 409

    # overwrite=True succeeds.
    resp = client.post("/api/shooters/me/scoreboard/import", json={"data": sb, "overwrite": True})
    assert resp.status_code == 200


def test_scoreboard_loads_token_from_project_env_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``<project>/.env.local`` is the canonical place for SPLITSMITH_SSI_TOKEN.

    Without this autoload the user has to remember ``export SPLITSMITH_SSI_TOKEN``
    in every shell that launches ``splitsmith ui`` -- avoidable friction.
    Existing process env still wins (override=False), so an explicit export
    keeps beating the file.
    """
    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    project_root = tmp_path / "match"
    project_root.mkdir(parents=True)
    (project_root / ".env.local").write_text("SPLITSMITH_SSI_TOKEN=token-from-env-local\n", encoding="utf-8")
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    src = client.get("/api/shooters/me/scoreboard/source").json()
    assert src["http_token_set"] is True


def test_scoreboard_loads_token_from_cwd_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``.env`` next to where the user *launched* the server (typically
    the repo root) is the most common spot. It must work even when the
    project lives somewhere else entirely."""
    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    launch_dir = tmp_path / "repo"
    project_root = tmp_path / "matches" / "spsk"
    launch_dir.mkdir(parents=True)
    project_root.mkdir(parents=True)
    (launch_dir / ".env").write_text("SPLITSMITH_SSI_TOKEN=token-from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(launch_dir)
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    src = client.get("/api/shooters/me/scoreboard/source").json()
    assert src["http_token_set"] is True


def test_scoreboard_source_reports_online_when_no_local_file(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.get("/api/shooters/me/scoreboard/source")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "online"
    assert resp.json()["local_match_json_path"] is None


def test_scoreboard_upload_writes_local_json_and_populates_project(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    fixture = _load_v1_match_fixture()
    resp = client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "SPSK Open 2026"
    assert body["scoreboard_match_id"] == "27190"
    assert body["scoreboard_content_type"] == 22
    assert len(body["stages"]) == 10
    # Per #50: stages from MatchData lack per-competitor times -> placeholder.
    assert all(s["placeholder"] for s in body["stages"])

    # File was written to <project>/scoreboard/match.json.
    local_path = _shooter_root / "scoreboard" / "match.json"
    assert local_path.exists()

    # Source endpoint now reports local mode (the offline path is active).
    src = client.get("/api/shooters/me/scoreboard/source").json()
    assert src["mode"] == "local"
    assert src["local_match_json_path"] == str(local_path)


def test_scoreboard_upload_rejects_non_v1_payload(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    # Legacy ``examples/`` shape is *not* MatchData -- the upload endpoint
    # should bounce it with a 400 rather than silently misparsing.
    legacy = {"match": {"id": "1", "name": "Legacy"}, "competitors": []}
    resp = client.post("/api/shooters/me/scoreboard/upload", json={"data": legacy})
    assert resp.status_code == 400


def test_scoreboard_search_uses_local_when_match_json_present(tmp_path: Path) -> None:
    """DI: with a local match.json on disk, the search endpoint must serve
    from LocalJsonScoreboard (no SPLITSMITH_SSI_TOKEN required, no network)."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    fixture = _load_v1_match_fixture()
    upload = client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    assert upload.status_code == 200

    # No SPLITSMITH_SSI_TOKEN env var; must still work because the local
    # JSON path doesn't go through SsiHttpClient.
    resp = client.get("/api/shooters/me/scoreboard/search", params={"q": "SPSK"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@respx.mock
def test_scoreboard_search_is_anonymous_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No local file + no token -> the request goes out anonymously
    (ssi-scoreboard#554, #1016): no Authorization header, the app named
    in User-Agent, and the endpoint answers 200."""
    from splitsmith import __version__
    from splitsmith.ui.scoreboard.http import DEFAULT_BASE_URL

    monkeypatch.delenv("SPLITSMITH_SSI_TOKEN", raising=False)
    # chdir to a clean dir so the splitsmith repo's own ``.env.local``
    # (which carries a real token) doesn't leak into the test process.
    monkeypatch.chdir(tmp_path)
    route = respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/scoreboard/search", params={"q": "anything"})
    assert resp.status_code == 200
    assert resp.json() == []
    sent = route.calls.last.request
    assert "authorization" not in sent.headers
    assert sent.headers["user-agent"] == f"splitsmith/{__version__}"


def test_scoreboard_upload_legacy_examples_auto_merges_stage_times(tmp_path: Path) -> None:
    """Dropping the legacy ``examples/*.json`` shape (single competitor with
    per-stage times) auto-pins that competitor and merges the times so the
    user lands on a fully-populated stage list in one drop. This is the
    bridge that lets pre-#50 users keep working without re-exporting."""
    import json as _json

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    legacy = _json.loads((_EXAMPLES_DIR / "blacksmith-handgun-open-2026.json").read_text(encoding="utf-8"))
    resp = client.post("/api/shooters/me/scoreboard/upload", json={"data": legacy})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_times_merged"] == 8
    assert body["selected_competitor_id"] == 1
    assert all(not s["placeholder"] for s in body["stages"])
    assert all(s["time_seconds"] > 0 for s in body["stages"])
    assert all(s["scorecard_updated_at"] is not None for s in body["stages"])


def test_scoreboard_upload_pure_v1_does_not_auto_merge(tmp_path: Path) -> None:
    """Pure ``MatchData`` carries no per-competitor stages -- the upload
    populates placeholders only, leaving the user to pin themselves."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    resp = client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_times_merged"] == 0
    assert body["selected_competitor_id"] is None
    assert all(s["placeholder"] for s in body["stages"])


def test_scoreboard_upload_combined_v1_auto_merges_when_single_competitor(
    tmp_path: Path,
) -> None:
    """Combined v1 fixture (MatchData + ``competitor_stages`` for one cid)
    auto-merges the same way the legacy file does."""
    import json as _json

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    combined = _json.loads(
        (_SCOREBOARD_FIXTURES_DIR / "match_22_27190_with_stages.json").read_text(encoding="utf-8")
    )
    resp = client.post("/api/shooters/me/scoreboard/upload", json={"data": combined})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_times_merged"] == 10
    assert body["selected_competitor_id"] is not None
    assert body["selected_shooter_id"] is not None
    assert all(not s["placeholder"] for s in body["stages"])


def test_scoreboard_select_shooter_blocked_on_pure_v1(tmp_path: Path) -> None:
    """When the offline source is pure ``MatchData``, select-shooter still
    persists the pin (so refresh-times can retry once upstream lands) but
    returns a 400 with the offline-pure-matchdata code so the UI can hint
    "drop a richer JSON" instead of a generic upstream banner."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})

    resp = client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": 40821, "competitor_id": 727562},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "stage_times_offline_pure_matchdata"
    proj = client.get("/api/shooters/me/project").json()
    assert proj["selected_shooter_id"] == 40821
    assert proj["selected_competitor_id"] == 727562


def test_scoreboard_refresh_times_re_merges(tmp_path: Path) -> None:
    """refresh-times invalidates the stage-times cache and re-runs the
    fetch+merge."""
    import json as _json

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    legacy = _json.loads((_EXAMPLES_DIR / "blacksmith-handgun-open-2026.json").read_text(encoding="utf-8"))
    client.post("/api/shooters/me/scoreboard/upload", json={"data": legacy})
    resp = client.post("/api/shooters/me/scoreboard/refresh-times")
    assert resp.status_code == 200
    assert resp.json()["stage_times_merged"] == 8


def test_scoreboard_match_data_endpoint_exposes_competitors(tmp_path: Path) -> None:
    """The SPA needs ``competitors[]`` to map a picked shooter id to a
    competitor id before calling /select-shooter."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})

    resp = client.get("/api/shooters/me/scoreboard/match-data")
    assert resp.status_code == 200
    md = resp.json()
    assert md["name"] == "SPSK Open 2026"
    assert isinstance(md["competitors"], list)
    assert len(md["competitors"]) > 0
    assert {"id", "shooterId", "name"}.issubset(md["competitors"][0])


def test_scoreboard_match_data_404_when_no_match_loaded(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.get("/api/shooters/me/scoreboard/match-data")
    assert resp.status_code == 404


def test_scoreboard_select_shooter_409_when_no_match(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": 1, "competitor_id": 1},
    )
    assert resp.status_code == 409


def test_scoreboard_refresh_times_409_when_no_pin(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    resp = client.post("/api/shooters/me/scoreboard/refresh-times")
    assert resp.status_code == 409


def test_scoreboard_select_shooter_rejects_competitor_not_in_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Server-side validation: a ``competitor_id`` that isn't in the
    loaded ``MatchData.competitors[]`` is rejected with 404
    ``competitor_not_in_match`` *before* any pin is persisted, so the
    project never ends up with an invalid pin that would break every
    subsequent refresh-times call."""
    import httpx

    monkeypatch.setenv("SPLITSMITH_SSI_TOKEN", "fake")
    monkeypatch.chdir(tmp_path)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    (_shooter_root / "scoreboard" / "match.json").unlink()

    # Mock the live API: get_match returns the fixture so the validate
    # step has a competitor list to check against. The stages path
    # should never be hit because validation fails first; assert that
    # by counting calls.
    from splitsmith.ui.scoreboard import http as http_mod

    original_init = http_mod.SsiHttpClient.__init__
    stage_calls = {"count": 0}

    def mocked_init(self, *args, **kwargs):  # noqa: ANN001
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/stages") and "/competitor/" in path:
                stage_calls["count"] += 1
                return httpx.Response(404)
            if "/match/22/27190" in path:
                return httpx.Response(200, json=fixture)
            return httpx.Response(500)

        kwargs["client"] = httpx.Client(
            base_url=http_mod.DEFAULT_BASE_URL,
            transport=httpx.MockTransport(handler),
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(http_mod.SsiHttpClient, "__init__", mocked_init)

    # 999999 is not in the fixture's competitors -- pre-validation should reject.
    resp = client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": 1, "competitor_id": 999999},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "competitor_not_in_match"
    assert stage_calls["count"] == 0  # never reached the stages endpoint
    proj = client.get("/api/shooters/me/project").json()
    assert proj["selected_shooter_id"] is None
    assert proj["selected_competitor_id"] is None


def test_scoreboard_select_shooter_uses_live_stage_times_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the upstream ships, the live API returns ``CompetitorStageResults``
    and the project ends up with real ``time_seconds`` / ``scorecard_updated_at``
    on every stage -- no offline JSON drop needed."""
    import httpx

    monkeypatch.setenv("SPLITSMITH_SSI_TOKEN", "fake")
    monkeypatch.chdir(tmp_path)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})
    (_shooter_root / "scoreboard" / "match.json").unlink()

    stage_results = {
        "ct": 22,
        "matchId": 27190,
        "competitorId": 727562,
        "shooterId": 40821,
        "division": "Optics Minor",
        "stages": [
            {
                "stage_number": s,
                "time_seconds": 18.0 + s,
                "scorecard_updated_at": f"2026-05-02T{8 + (s - 1) // 2:02d}:"
                f"{((s - 1) * 23) % 60:02d}:00+00:00",
            }
            for s in range(1, 11)
        ],
    }

    from splitsmith.ui.scoreboard import http as http_mod

    original_init = http_mod.SsiHttpClient.__init__

    def mocked_init(self, *args, **kwargs):  # noqa: ANN001
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/stages") and "/competitor/" in path:
                return httpx.Response(200, json=stage_results)
            if "/match/22/27190" in path:
                return httpx.Response(200, json=fixture)
            return httpx.Response(500)

        kwargs["client"] = httpx.Client(
            base_url=http_mod.DEFAULT_BASE_URL,
            transport=httpx.MockTransport(handler),
        )
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(http_mod.SsiHttpClient, "__init__", mocked_init)

    resp = client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": 40821, "competitor_id": 727562},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_times_merged"] == 10
    assert all(not s["placeholder"] for s in body["stages"])
    assert body["selected_shooter_id"] == 40821
    assert body["selected_competitor_id"] == 727562


def test_scoreboard_fetch_uses_local_match_when_present(tmp_path: Path) -> None:
    """When a local match.json is dropped, /api/scoreboard/fetch must also
    use the offline path -- no network call. We verify by clearing the
    token (LocalJsonScoreboard doesn't need it) and asking for the same
    (ct, id) that the dropped file claims to be."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    fixture = _load_v1_match_fixture()
    client.post("/api/shooters/me/scoreboard/upload", json={"data": fixture})

    resp = client.post(
        "/api/shooters/me/scoreboard/fetch",
        json={"content_type": 22, "match_id": 27190, "overwrite": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scoreboard_match_id"] == "27190"


def test_placeholder_stages_endpoint_creates_stages(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/project/placeholder-stages",
        json={"stage_count": 5, "match_name": "Test Match", "match_date": "2026-04-12"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Match"
    assert body["match_date"] == "2026-04-12"
    assert len(body["stages"]) == 5
    assert all(s["placeholder"] for s in body["stages"])


def test_placeholder_stages_endpoint_409_when_real_stages_exist(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    # Real scoreboard import first.
    sb = {
        "match": {"id": "1", "name": "Real"},
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    resp = client.post(
        "/api/shooters/me/project/placeholder-stages",
        json={"stage_count": 5},
    )
    assert resp.status_code == 409


def test_scan_videos_registers_and_auto_assigns(tmp_path: Path) -> None:
    """End-to-end test for the scan endpoint: create a stage, drop a video into a
    source folder with a matching mtime, scan, expect auto-assignment as primary."""
    import os as _os
    from datetime import datetime

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="Scan Match")
    client = _MatchClient(app)

    scorecard_iso = "2026-04-12T11:30:00+00:00"
    scorecard_dt = datetime.fromisoformat(scorecard_iso)
    sb = {
        "match": {"id": "1", "name": "Scan Match Loaded"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "First",
                        "time_seconds": 12.0,
                        "scorecard_updated_at": scorecard_iso,
                    }
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    vid = src_dir / "VID.mp4"
    vid.write_bytes(b"")
    target_ts = scorecard_dt.timestamp() - 60  # 1 minute before scorecard
    _os.utime(vid, (target_ts, target_ts))

    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["registered"] == ["raw/VID.mp4"]
    assert body["auto_assigned"] == {"1": "raw/VID.mp4"}

    # Verify project state.
    project = client.get("/api/shooters/me/project").json()
    assert project["stages"][0]["videos"][0]["role"] == "primary"
    assert project["unassigned_videos"] == []


def test_scan_videos_400_on_missing_dir(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(tmp_path / "does-not-exist")},
    )
    assert resp.status_code == 400


def test_scan_reports_a_copy_of_an_imported_clip_as_skipped(tmp_path: Path) -> None:
    """A byte copy of a clip this shooter has is not "added" (#1233).

    ``register_video`` returns the existing video for a fingerprint match;
    the scan listed it under ``registered`` and the page said "1 video
    added" while the shooter's count stayed put.
    """
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    original = tmp_path / "card" / "VID.mp4"
    original.parent.mkdir()
    original.write_bytes(b"the recording")
    assert _scan_paths(client, [original])["registered"] == ["raw/VID.mp4"]

    copy = tmp_path / "elsewhere" / "VID-copy.mp4"
    copy.parent.mkdir()
    copy.write_bytes(b"the recording")
    body = _scan_paths(client, [copy])

    assert body["registered"] == []
    assert body["skipped"] == ["VID-copy.mp4: already imported for this shooter as raw/VID.mp4"]
    assert len(client.get("/api/shooters/me/project").json()["unassigned_videos"]) == 1


def test_move_assignment_endpoint(tmp_path: Path) -> None:
    """Set role to ignored, verify, then move back to a stage."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="Move Match")
    client = _MatchClient(app)

    # Set up: 1 stage, 1 video assigned as primary.
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "Tester",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "First",
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
    vid = src_dir / "VID.mp4"
    vid.write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Should have one unassigned video.
    project = client.get("/api/shooters/me/project").json()
    assert len(project["unassigned_videos"]) == 1

    # Move to stage 1 as primary.
    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={
            "video_path": "raw/VID.mp4",
            "to_stage_number": 1,
            "role": "primary",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["unassigned_videos"] == []
    assert body["stages"][0]["videos"][0]["role"] == "primary"

    # Mark as ignored (still on stage 1).
    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={
            "video_path": "raw/VID.mp4",
            "to_stage_number": 1,
            "role": "ignored",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["stages"][0]["videos"][0]["role"] == "ignored"

    # Back to unassigned.
    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stages"][0]["videos"] == []
    assert len(body["unassigned_videos"]) == 1


def test_move_assignment_404_on_unknown_video(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/nope.mp4", "to_stage_number": None},
    )
    assert resp.status_code == 404


def test_fs_list_returns_directory_entries(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    listing_root = tmp_path / "browseable"
    listing_root.mkdir()
    (listing_root / "subdir-a").mkdir()
    (listing_root / "subdir-b").mkdir()
    (listing_root / "VID_1.mp4").write_bytes(b"")
    (listing_root / "VID_2.mov").write_bytes(b"")
    (listing_root / "notes.txt").write_text("hi", encoding="utf-8")
    (listing_root / ".hidden").mkdir()  # filtered out

    resp = client.get("/api/shooters/me/fs/list", params={"path": str(listing_root)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(listing_root.resolve())
    assert body["parent"] == str(listing_root.parent.resolve())
    names = {e["name"]: e for e in body["entries"]}
    assert ".hidden" not in names
    assert names["subdir-a"]["kind"] == "dir"
    assert names["VID_1.mp4"]["kind"] == "video"
    assert names["notes.txt"]["kind"] == "file"
    # Directories sort first.
    kinds = [e["kind"] for e in body["entries"]]
    assert kinds.index("dir") < kinds.index("video")


def test_fs_list_video_count_for_directories(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)

    parent = tmp_path / "p"
    inner = parent / "match-day"
    inner.mkdir(parents=True)
    (inner / "v1.mp4").write_bytes(b"")
    (inner / "v2.mov").write_bytes(b"")
    (inner / "notes.txt").write_text("hi", encoding="utf-8")

    resp = client.get("/api/shooters/me/fs/list", params={"path": str(parent)})
    assert resp.status_code == 200
    entries = {e["name"]: e for e in resp.json()["entries"]}
    assert entries["match-day"]["kind"] == "dir"
    assert entries["match-day"]["video_count"] == 2


def test_fs_list_default_path_uses_last_scanned_dir(tmp_path: Path) -> None:
    """Saving last_scanned_dir on the project should change the default path."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    seeded = tmp_path / "videos"
    seeded.mkdir()
    project = MatchProject.load(_shooter_root)
    project.last_scanned_dir = str(seeded.resolve())
    project.save(_shooter_root)

    resp = client.get("/api/shooters/me/fs/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(seeded.resolve())
    # The seeded dir is suggested first in bookmarks (the "Recent" group).
    first = body["suggested_starts"][0]
    assert first["path"] == str(seeded.resolve())
    assert first["kind"] == "recent"


def test_fs_list_404_on_missing_path(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.get("/api/shooters/me/fs/list", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


def test_scan_persists_last_scanned_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "VID.mp4").write_bytes(b"")

    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    assert resp.status_code == 200

    project = client.get("/api/shooters/me/project").json()
    assert project["last_scanned_dir"] == str(src_dir.resolve())


def test_swap_primary_with_a_confirmed_beep_chains_the_trim(tmp_path: Path, monkeypatch) -> None:
    """The new primary's beep was confirmed on its own audio: the swap keeps
    it and goes straight to the trim (which chains detection), with no new
    beep detection to race the confirmed beep (2026-10-16)."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, src = _seed_project_with_primary(tmp_path)
    (src.parent / "HEAD.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src.parent), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "secondary"},
    )
    project_json = tmp_path / "match" / "shooters" / "me" / "project.json"
    doc = json.loads(project_json.read_text(encoding="utf-8"))
    head = next(v for v in doc["stages"][0]["videos"] if v["path"] == "raw/HEAD.mp4")
    head.update(beep_time=3.5, beep_source="manual", beep_reviewed=True)
    head["processed"] = {"beep": True, "shot_detect": False, "trim": False}
    project_json.write_text(json.dumps(doc), encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 200, resp.text
    new = next(v for v in resp.json()["stages"][0]["videos"] if v["path"] == "raw/HEAD.mp4")
    assert (new["role"], new["beep_time"], new["beep_reviewed"]) == ("primary", 3.5, True)
    kinds = [(j["kind"], j.get("video_id")) for j in client.get("/api/me/jobs").json()]
    assert ("trim", new["video_id"]) in kinds
    assert not any(k == "detect_beep" for k, _ in kinds)


def test_stream_video_serves_trimmed_for_primary(tmp_path: Path) -> None:
    """When the trimmed MP4 exists for a stage, /stream returns its bytes
    rather than the source. This is what gives the audit screen its
    short-GOP scrub-friendly playback."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(_shooter_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"SOURCE_MP4")

    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    _full, _audit, trimmed_mp4 = _primary_cache_paths(project_root)
    trimmed_mp4.write_bytes(b"TRIMMED_MP4")

    resp = client.get(f"/api/shooters/me/videos/stream?path={primary.path}")
    assert resp.status_code == 200
    assert resp.content == b"TRIMMED_MP4"


def test_stream_video_kind_pins_source_even_when_trim_exists(tmp_path: Path) -> None:
    """``kind=source`` ignores an existing trim so the SPA can pin a live
    <video> element to the source bytes. Without this, a background trim
    completing mid-Range-request would switch the file under the element
    and the next request would fall past the trim's (shorter) EOF."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(_shooter_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"SOURCE_MP4")

    trimmed_dir = _shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    _full, _audit, trimmed_mp4 = _primary_cache_paths(project_root)
    trimmed_mp4.write_bytes(b"TRIMMED_MP4")

    resp = client.get(f"/api/shooters/me/videos/stream?path={primary.path}&kind=source")
    assert resp.status_code == 200
    assert resp.content == b"SOURCE_MP4"


def test_stream_video_kind_trim_404_when_not_built(tmp_path: Path) -> None:
    """``kind=trim`` returns 404 when no trimmed clip exists yet, so the
    SPA never falls back to source bytes when it's explicitly asking
    for the (frame-accurate) trim."""
    client, _ = _seed_project_with_primary(tmp_path)
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    project = MatchProject.load(_shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(_shooter_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"SOURCE_MP4")

    resp = client.get(f"/api/shooters/me/videos/stream?path={primary.path}&kind=trim")
    assert resp.status_code == 404
    assert "trimmed clip not built" in resp.json()["detail"]


def test_set_compare_camera_rejects_unknown_selector(tmp_path: Path) -> None:
    """Typing 'chset' must fail here, not silently export every tile from
    the primary."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"

    resp = client.patch("/api/shooters/me/compare-camera", json={"camera": "backpack"})

    assert resp.status_code == 400, resp.text
    assert "backpack" in resp.json()["detail"]
    assert MatchProject.load(shooter_root).compare_camera is None


def test_set_compare_camera_ignores_mounts_only_on_skipped_stages(tmp_path: Path) -> None:
    """A skipped stage contributes no footage to a grid, so a mount that
    exists only there is not a usable selection (#620).

    Untested until now: without the ``if not stage.skipped`` filter the
    endpoint accepts the mount, and every unskipped stage then silently
    exports from the primary instead -- the exact silent-wrong-camera
    failure this validation exists to prevent.
    """
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"

    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    primary.camera_mount = "chest"
    project.stages[0].skipped = True
    project.save(shooter_root)

    resp = client.patch("/api/shooters/me/compare-camera", json={"camera": "chest"})

    assert resp.status_code == 400, resp.text
    assert "chest" in resp.json()["detail"]
    assert MatchProject.load(shooter_root).compare_camera is None


def test_clear_compare_camera(tmp_path: Path) -> None:
    """``null`` clears the selection back to "whatever the primary is"."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    # A role selector resolves without any mount tagging.
    assert client.patch("/api/shooters/me/compare-camera", json={"camera": "primary"}).status_code == 200
    assert MatchProject.load(shooter_root).compare_camera == "primary"

    resp = client.patch("/api/shooters/me/compare-camera", json={"camera": None})

    assert resp.status_code == 200, resp.text
    assert MatchProject.load(shooter_root).compare_camera is None


def test_stream_video_serves_registered_file(tmp_path: Path) -> None:
    """Stream endpoint serves bytes for a path that's registered with the project."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(shooter_root, primary.path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"FAKE_MP4_BYTES")

    resp = client.get(f"/api/shooters/me/videos/stream?path={primary.path}")
    assert resp.status_code == 200
    assert resp.content == b"FAKE_MP4_BYTES"
    assert resp.headers["content-type"].startswith("video/")


def test_stream_video_serves_a_mov_as_quicktime_inline(tmp_path: Path) -> None:
    """An iPhone .MOV must reach a <video> element as a playable video, not
    as a download: Safari refuses application/octet-stream with an
    attachment disposition (the beep picker's "Preview unavailable", no
    sound, 2026-10-02). kind=proxy falls back to the source locally."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    source = tmp_path / "from-albin" / "IMG_5277.MOV"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"FAKE_MOV_BYTES")
    project = MatchProject.load(shooter_root)
    video = project.register_video(source, shooter_root)
    project.save(shooter_root)

    resp = client.get(f"/api/shooters/me/videos/stream?path={video.path}&kind=proxy")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/quicktime"
    assert resp.headers["content-disposition"].startswith("inline")


def test_stream_video_404_on_unregistered_path(tmp_path: Path) -> None:
    """Stream endpoint refuses to serve arbitrary filesystem paths."""
    client, _ = _seed_project_with_primary(tmp_path)
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"SECRET")
    resp = client.get(f"/api/shooters/me/videos/stream?path={secret}")
    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


def test_stream_video_424_when_target_missing(tmp_path: Path) -> None:
    """Registered path that no longer exists on disk surfaces as 424
    (Failed Dependency) with a structured detail. The SPA reads
    ``detail.code == "source_unreachable"`` to render a consistent
    "reconnect the USB / SD card" message across detect-beep, trim,
    beep preview, video stream, and export."""
    client, _ = _seed_project_with_primary(tmp_path)
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    primary = project.stages[0].primary()
    assert primary is not None
    resolved = project.resolve_video_path(shooter_root, primary.path).resolve()
    if resolved.exists() or resolved.is_symlink():
        resolved.unlink()
    resp = client.get(f"/api/shooters/me/videos/stream?path={primary.path}")
    assert resp.status_code == 424
    body = resp.json()
    assert body["detail"]["code"] == "source_unreachable"
    assert "not reachable" in body["detail"]["message"]


def test_scan_videos_with_explicit_source_paths(tmp_path: Path) -> None:
    """source_paths picks specific files (USB-cam workflow). Only the listed
    files are registered, even if other videos sit in the same directory."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
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
    keep1 = src_dir / "keep1.mp4"
    keep2 = src_dir / "keep2.mp4"
    skip = src_dir / "skip.mp4"
    for f in (keep1, keep2, skip):
        f.write_bytes(b"")

    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={
            "source_paths": [str(keep1), str(keep2)],
            "auto_assign_primary": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["registered"]) == ["raw/keep1.mp4", "raw/keep2.mp4"]
    project = client.get("/api/shooters/me/project").json()
    assert len(project["unassigned_videos"]) == 2
    # last_scanned_dir set to the parent of the first picked file.
    assert project["last_scanned_dir"] == str(src_dir.resolve())


def test_scan_videos_walks_subdirectories(tmp_path: Path) -> None:
    """source_dir recurses into subfolders so users can point at a parent
    folder whose videos live inside dated/per-card subdirectories
    (mirrors the relink scanner's behaviour)."""
    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)

    src_dir = tmp_path / "videos"
    (src_dir / "card-a").mkdir(parents=True)
    (src_dir / "card-b" / "nested").mkdir(parents=True)
    nested_a = src_dir / "card-a" / "A.mp4"
    nested_b = src_dir / "card-b" / "nested" / "B.mp4"
    nested_a.write_bytes(b"")
    nested_b.write_bytes(b"")
    # Non-video sibling at root should be ignored.
    (src_dir / "notes.txt").write_text("hi", encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["registered"]) == ["raw/A.mp4", "raw/B.mp4"]


def test_scan_400_when_neither_source_dir_nor_paths(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post("/api/shooters/me/videos/scan", json={"auto_assign_primary": False})
    assert resp.status_code == 400


def test_scan_400_when_both_source_dir_and_paths(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(tmp_path), "source_paths": [str(tmp_path / "a.mp4")]},
    )
    assert resp.status_code == 400


def test_settings_endpoint_persists_overrides(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/project/settings",
        json={
            "audio_dir": str(tmp_path / "scratch" / "audio"),
            "trimmed_dir": str(tmp_path / "scratch" / "trimmed"),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["audio_dir"] == str(tmp_path / "scratch" / "audio")
    assert body["trimmed_dir"] == str(tmp_path / "scratch" / "trimmed")
    # raw_dir / exports_dir untouched.
    assert body["raw_dir"] is None
    assert body["exports_dir"] is None
    # Directories were created.
    assert (tmp_path / "scratch" / "audio").is_dir()
    assert (tmp_path / "scratch" / "trimmed").is_dir()


def test_settings_empty_string_clears_override(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    client.post(
        "/api/shooters/me/project/settings",
        json={"audio_dir": str(tmp_path / "audio-config")},
    )
    body = client.post("/api/shooters/me/project/settings", json={"audio_dir": ""}).json()
    assert body["audio_dir"] is None


def test_settings_409_when_old_dir_non_empty(tmp_path: Path) -> None:
    """Changing a path field must surface a 409 ``non_empty_old_dirs`` warning
    when the old directory still has files -- splitsmith does not migrate."""
    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    # Default audio_dir is <shooter_root>/audio. Drop a stray cache file there.
    audio_default = root / "shooters" / "me" / "audio"
    audio_default.mkdir(parents=True, exist_ok=True)
    (audio_default / "stage_1.wav").write_bytes(b"fake")

    new_audio = tmp_path / "scratch-audio"
    resp = client.post(
        "/api/shooters/me/project/settings",
        json={"audio_dir": str(new_audio)},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "non_empty_old_dirs"
    assert len(detail["dirs"]) == 1
    assert detail["dirs"][0]["field"] == "audio_dir"
    assert detail["dirs"][0]["path"] == str(audio_default)
    assert detail["dirs"][0]["file_count"] == 1
    # Project unchanged on disk.
    assert MatchProject.load(root / "shooters" / "me").audio_dir is None

    # With confirm=true the change goes through, leaving old files behind.
    resp = client.post(
        "/api/shooters/me/project/settings",
        json={"audio_dir": str(new_audio), "confirm": True},
    )
    assert resp.status_code == 200
    assert resp.json()["audio_dir"] == str(new_audio)
    assert (audio_default / "stage_1.wav").exists()  # not migrated


def test_settings_no_warning_when_old_dir_empty(tmp_path: Path) -> None:
    """An empty old directory should not trigger the warning."""
    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)
    # Default <project>/audio doesn't even exist yet.
    resp = client.post(
        "/api/shooters/me/project/settings",
        json={"audio_dir": str(tmp_path / "elsewhere")},
    )
    assert resp.status_code == 200


def test_remove_video_unassigned(tmp_path: Path) -> None:
    """Removing an unassigned video drops it from the project and unlinks
    its symlink under raw_dir. The original source on USB / external storage
    is never touched."""
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    assert resp.status_code == 200
    registered_path = resp.json()["registered"][0]
    raw_entry = root / "shooters" / "me" / "raw" / "clip.mp4"
    assert raw_link.is_link(raw_entry)

    resp = client.post("/api/shooters/me/videos/remove", json={"video_path": registered_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["was_primary"] is False
    assert body["project"]["unassigned_videos"] == []
    assert not raw_link.present(raw_entry)
    assert src.exists(), "original source must never be touched"


def test_remove_primary_clears_caches_and_keeps_audit(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)
    sb = {
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.assign_video(Path("raw/clip.mp4"), to_stage_number=1, role="primary")
    project.save(shooter_root)
    # Simulate processed state: write fake cached files audit/audio/trimmed.
    audio_cache, _audit_wav, trimmed_cache = _primary_cache_paths(root)
    audio_cache.parent.mkdir(parents=True, exist_ok=True)
    audio_cache.write_bytes(b"wav")
    trimmed_cache.parent.mkdir(parents=True, exist_ok=True)
    trimmed_cache.write_bytes(b"mp4")
    audit = shooter_root / "audit" / "stage1.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}", encoding="utf-8")
    project.stages[0].videos[0].processed = {
        "beep": True,
        "shot_detect": True,
        "trim": True,
    }
    project.save(shooter_root)

    resp = client.post(
        "/api/shooters/me/videos/remove",
        json={"video_path": "raw/clip.mp4"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["was_primary"] is True
    assert body["plan"]["audit_reset"] is False
    assert not audio_cache.exists()
    assert not trimmed_cache.exists()
    assert audit.exists(), "audit JSON must be preserved by default"


def test_remove_primary_with_reset_audit_clears_audit(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src_dir = tmp_path / "ext"
    src_dir.mkdir()
    src = src_dir / "clip.mp4"
    src.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)
    sb = {
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
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_paths": [str(src)], "auto_assign_primary": False},
    )
    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.assign_video(Path("raw/clip.mp4"), to_stage_number=1, role="primary")
    project.save(shooter_root)
    audit = shooter_root / "audit" / "stage1.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text("{}", encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/videos/remove",
        json={"video_path": "raw/clip.mp4", "reset_audit": True},
    )
    assert resp.status_code == 200
    assert resp.json()["plan"]["audit_reset"] is True
    assert not audit.exists()


def test_remove_video_404_when_unknown(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    resp = client.post(
        "/api/shooters/me/videos/remove",
        json={"video_path": "raw/no-such.mp4"},
    )
    assert resp.status_code == 404


def test_fs_list_probe_populates_duration_and_thumbnail(tmp_path: Path) -> None:
    """When ``probe=true`` is passed, video entries get duration + thumbnail."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    clip = folder / "clip.mp4"
    clip.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    def _fake_probe(path: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        return ProbeResult(duration=12.5, width=1920, height=1080, codec="h264")

    def _fake_thumb(source: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        from splitsmith.video_probe import source_cache_key

        dest = cache_dir / f"{source_cache_key(source)}.jpg"
        dest.write_bytes(b"\xff\xd8\xff")
        return dest

    with (
        patch("splitsmith.ui.server.video_probe.probe", side_effect=_fake_probe),
        patch("splitsmith.ui.server.thumbnail_helpers.ensure", side_effect=_fake_thumb),
    ):
        resp = client.get(f"/api/shooters/me/fs/list?path={folder}&probe=true")

    assert resp.status_code == 200
    body = resp.json()
    [entry] = body["entries"]
    assert entry["kind"] == "video"
    assert entry["duration"] == 12.5
    assert entry["thumbnail_url"].startswith("/api/shooters/me/thumbnails/")


def test_fs_probe_endpoint_runs_on_demand(tmp_path: Path) -> None:
    root = tmp_path / "match"
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    def _fake_probe(path: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        return ProbeResult(duration=8.0)

    def _fake_thumb(source: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        from splitsmith.video_probe import source_cache_key

        dest = cache_dir / f"{source_cache_key(source)}.jpg"
        dest.write_bytes(b"\xff")
        return dest

    with (
        patch("splitsmith.ui.server.video_probe.probe", side_effect=_fake_probe),
        patch("splitsmith.ui.server.thumbnail_helpers.ensure", side_effect=_fake_thumb),
    ):
        resp = client.get(f"/api/shooters/me/fs/probe?path={clip}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["duration"] == 8.0
    assert body["thumbnail_url"].startswith("/api/shooters/me/thumbnails/")


def test_videos_reveal_runs_on_registered_path(tmp_path: Path) -> None:
    # Source the user picked lives outside the project root (typical USB
    # / Downloads case); the symlink under raw/ resolves to it. The reveal
    # endpoint must accept the registered project-relative path and shell
    # out against the resolved target.
    root = tmp_path / "match"
    src_dir = tmp_path / "external"
    src_dir.mkdir()
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    scan = client.post("/api/shooters/me/videos/scan", json={"source_paths": [str(src)]})
    assert scan.status_code == 200, scan.text

    calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):  # noqa: ANN001
        calls.append(list(cmd))

        class _R:
            returncode = 0

        return _R()

    with patch("splitsmith.ui.server.subprocess.run", side_effect=_fake_run):
        resp = client.post("/api/shooters/me/videos/reveal", json={"path": "raw/VID.mp4"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["revealed"].endswith("VID.mp4")
    assert calls, "subprocess.run was not invoked"
    # Resolved target should be the source on the symlink target side, not
    # the symlink under raw/.
    invoked_path = calls[0][-1]
    assert "external" in invoked_path or invoked_path.endswith("VID.mp4")


def test_videos_reveal_404_on_unregistered_path(tmp_path: Path) -> None:
    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    resp = client.post("/api/shooters/me/videos/reveal", json={"path": "raw/missing.mp4"})
    assert resp.status_code == 404


def test_videos_reveal_surfaces_nonzero_subprocess_exit(tmp_path: Path) -> None:
    """Regression: a Linux box without ``xdg-open`` (or a Wayland session
    without DBUS) used to silently succeed -- the helper exited nonzero
    and we returned 200. The SPA could not toast the failure. Now we
    propagate as a 500 so the click visibly fails instead of vanishing.
    """
    root = tmp_path / "match"
    src_dir = tmp_path / "external"
    src_dir.mkdir()
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    scan = client.post("/api/shooters/me/videos/scan", json={"source_paths": [str(src)]})
    assert scan.status_code == 200, scan.text

    def _failing_run(cmd, **_kwargs):  # noqa: ANN001
        class _R:
            returncode = 3  # xdg-open: "required tool not found"
            stderr = "xdg-open: no method available for opening"

        return _R()

    with patch("splitsmith.ui.server.subprocess.run", side_effect=_failing_run):
        resp = client.post("/api/shooters/me/videos/reveal", json={"path": "raw/VID.mp4"})

    if sys.platform.startswith("win"):
        # explorer.exe returns 1 on success; we deliberately don't gate on
        # exit code there. Skip the assertion rather than rewriting the
        # stub to mimic a non-explorer Windows error path.
        pytest.skip("Windows opts out of subprocess returncode checking for explorer.exe")
    assert resp.status_code == 500, resp.text
    assert "refused to open" in resp.text


def test_videos_reveal_surfaces_missing_helper_binary(tmp_path: Path) -> None:
    """``xdg-open`` / ``open`` / ``explorer`` not on PATH must surface a 500."""
    root = tmp_path / "match"
    src_dir = tmp_path / "external"
    src_dir.mkdir()
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    scan = client.post("/api/shooters/me/videos/scan", json={"source_paths": [str(src)]})
    assert scan.status_code == 200, scan.text

    def _raise_fnf(cmd, **_kwargs):  # noqa: ANN001
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    with patch("splitsmith.ui.server.subprocess.run", side_effect=_raise_fnf):
        resp = client.post("/api/shooters/me/videos/reveal", json={"path": "raw/VID.mp4"})

    assert resp.status_code == 500, resp.text
    assert "failed to launch file manager" in resp.text


def test_fs_probe_resolves_project_relative_paths(tmp_path: Path) -> None:
    # StageVideo.path is stored project-relative (e.g. "raw/foo.mp4"); the
    # probe endpoint must resolve it via the shooter project root, not
    # process CWD.
    root = tmp_path / "match"
    raw_dir = root / "shooters" / "me" / "raw"
    raw_dir.mkdir(parents=True)
    clip = raw_dir / "clip.mp4"
    clip.write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    def _fake_probe(path: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        return ProbeResult(duration=4.0)

    def _fake_thumb(source: Path, *, cache_dir: Path, **kwargs):  # noqa: ANN001
        cache_dir.mkdir(parents=True, exist_ok=True)
        from splitsmith.video_probe import source_cache_key

        dest = cache_dir / f"{source_cache_key(source)}.jpg"
        dest.write_bytes(b"\xff")
        return dest

    with (
        patch("splitsmith.ui.server.video_probe.probe", side_effect=_fake_probe),
        patch("splitsmith.ui.server.thumbnail_helpers.ensure", side_effect=_fake_thumb),
    ):
        resp = client.get("/api/shooters/me/fs/probe?path=raw/clip.mp4")

    assert resp.status_code == 200
    assert resp.json()["duration"] == 4.0


def test_thumbnail_endpoint_serves_cached(tmp_path: Path) -> None:
    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    thumbs_dir = root / "shooters" / "me" / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / "abc123.jpg").write_bytes(b"\xff\xd8\xff jpeg")

    resp = client.get("/api/shooters/me/thumbnails/abc123.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")

    resp_404 = client.get("/api/shooters/me/thumbnails/missing.jpg")
    assert resp_404.status_code == 404


def test_thumbnail_endpoint_rejects_path_traversal(tmp_path: Path) -> None:
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    client = _MatchClient(app)
    # Slashes in the path component aren't possible thanks to FastAPI's
    # path matcher, but '..' as a key should be rejected by our shape check.
    resp = client.get("/api/shooters/me/thumbnails/..jpg")
    assert resp.status_code == 400


def test_fs_list_probe_skipped_when_falsy(tmp_path: Path) -> None:
    """Without ?probe=true and without cached results, video entries should
    have null duration / thumbnail and probe / ensure must not run."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    with (
        patch(
            "splitsmith.ui.server.video_probe.probe",
            side_effect=AssertionError("probe must not run"),
        ),
        patch(
            "splitsmith.ui.server.thumbnail_helpers.ensure",
            side_effect=AssertionError("thumb must not run"),
        ),
    ):
        resp = client.get(f"/api/shooters/me/fs/list?path={folder}")

    assert resp.status_code == 200
    [entry] = resp.json()["entries"]
    assert entry["duration"] is None
    assert entry["thumbnail_url"] is None


def test_fs_list_probe_failure_swallowed(tmp_path: Path) -> None:
    """A probe failure must not break the listing; the entry just gets nulls."""
    root = tmp_path / "match"
    folder = tmp_path / "videos"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"fake")

    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    with (
        patch(
            "splitsmith.ui.server.video_probe.probe",
            side_effect=ProbeError("boom"),
        ),
        patch(
            "splitsmith.ui.server.thumbnail_helpers.ensure",
            side_effect=ThumbnailError("thumb fails too"),
        ),
    ):
        resp = client.get(f"/api/shooters/me/fs/list?path={folder}&probe=true")

    assert resp.status_code == 200
    [entry] = resp.json()["entries"]
    assert entry["duration"] is None


def test_external_edit_visible_without_restart(tmp_path: Path) -> None:
    """External edits to project.json must appear on the next request -- the
    server doesn't cache the model in memory."""
    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="External Edit Match")
    client = _MatchClient(app)

    assert client.get("/api/shooters/me/project").json()["competitor_name"] is None

    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.competitor_name = "Edited Externally"
    project.save(shooter_root)

    assert client.get("/api/shooters/me/project").json()["competitor_name"] == "Edited Externally"


def test_video_id_is_stable_and_exposed(tmp_path: Path) -> None:
    """The computed ``video_id`` is on the wire and identical across reloads."""
    client, _ = _seed_project_with_primary(tmp_path)
    proj1 = client.get("/api/shooters/me/project").json()
    proj2 = client.get("/api/shooters/me/project").json()
    vid1 = proj1["stages"][0]["videos"][0]["video_id"]
    vid2 = proj2["stages"][0]["videos"][0]["video_id"]
    assert vid1 == vid2
    assert len(vid1) == 12  # 6-byte blake2s -> 12 hex chars


def test_auto_beep_queued_on_move_to_stage(tmp_path: Path, monkeypatch) -> None:
    """Dragging a video onto a stage queues detect_beep automatically --
    the user shouldn't have to click 'detect beep' on every camera."""
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=8.42)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
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
    (src_dir / "VID.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Move triggers auto-fire.
    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    assert resp.status_code == 200
    jobs = _wait_for_jobs_to_drain(client)
    auto_beep_jobs = [j for j in jobs if j["kind"] == "detect_beep"]
    assert len(auto_beep_jobs) == 1
    assert auto_beep_jobs[0]["status"] == "succeeded"
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["beep_time"] == 8.42
    assert primary["beep_source"] == "auto"
    assert primary["processed"]["beep"] is True


def test_auto_beep_fires_for_secondaries_too(tmp_path: Path, monkeypatch) -> None:
    """The hook isn't primary-only -- secondaries assigned to a stage also
    get beeped, since they need their own beep timestamp to align with
    the primary's audit timeline."""
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=8.42)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
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
    (src_dir / "PRIMARY.mp4").write_bytes(b"")
    (src_dir / "SECONDARY.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Sequence the moves so each worker's project save doesn't race the
    # other -- the per-job runner loads project state on entry and
    # writes it back on exit; concurrent saves would clobber each
    # other's ``processed.beep`` flag.
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/PRIMARY.mp4", "to_stage_number": 1, "role": "primary"},
    )
    _wait_for_jobs_to_drain(client)
    client.post(
        "/api/shooters/me/assignments/move",
        json={
            "video_path": "raw/SECONDARY.mp4",
            "to_stage_number": 1,
            "role": "secondary",
        },
    )
    _wait_for_jobs_to_drain(client)
    stage = client.get("/api/shooters/me/project").json()["stages"][0]
    assert all(v["processed"]["beep"] for v in stage["videos"])


def test_auto_beep_skipped_for_unassigned_destination(tmp_path: Path, monkeypatch) -> None:
    """Moving a video back to the unassigned tray (to_stage_number=None)
    must not queue a beep job -- there's no stage context for the
    audio-cache layout or the trim chain to use."""
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=8.42)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
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
    (src_dir / "VID.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # Move to stage -> auto-beep
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    _wait_for_jobs_to_drain(client)
    job_count_after_assign = len(client.get("/api/me/jobs").json())

    # Move back to tray -> no new job (idempotent on already-beeped is
    # also covered: the helper would skip even if we re-assigned).
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": None, "role": "secondary"},
    )
    assert len(client.get("/api/me/jobs").json()) == job_count_after_assign


def test_auto_beep_skipped_when_already_processed(tmp_path: Path, monkeypatch) -> None:
    """A video that already has ``processed.beep == True`` (because the
    user clicked detect-beep manually, or we already auto-fired) doesn't
    re-fire on a subsequent move between stages."""
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=8.42)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S1",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "stage_number": 2,
                        "stage_name": "S2",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T00:00:00+00:00",
                    },
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "VID.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    # First move to stage 1 -> auto-fires.
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    _wait_for_jobs_to_drain(client)
    jobs_before = len(client.get("/api/me/jobs").json())

    # Move to stage 2 -> processed.beep is already True, no new job.
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 2, "role": "primary"},
    )
    assert len(client.get("/api/me/jobs").json()) == jobs_before


def test_auto_beep_disabled_via_env_var(tmp_path: Path, monkeypatch) -> None:
    """The autouse fixture disables auto-beep for the rest of this module
    via the env var; this test makes the contract explicit."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    _stub_detect(monkeypatch, beep_time=8.42)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
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
    (src_dir / "VID.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/VID.mp4", "to_stage_number": 1, "role": "primary"},
    )
    # No auto job; primary still un-beeped until the user clicks detect.
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["processed"]["beep"] is False


def test_auto_beep_queued_on_scan_auto_assign(tmp_path: Path, monkeypatch) -> None:
    """Scanning a folder with ``auto_assign_primary=true`` and matching
    timestamps lands a primary on the stage; the auto-fire hook queues
    its beep without the user clicking anything."""
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=4.5)

    project_root = tmp_path / "match"
    _shooter_root = project_root / "shooters" / "me"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T12:00:00+00:00",
                    }
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})

    # Drop a video whose mtime matches the stage's scorecard window so
    # auto_match picks it up.
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    src = src_dir / "VID.mp4"
    src.write_bytes(b"")
    import os as _os

    target_mtime = 1767268500  # within +/- tolerance of 2026-01-01T12:00:00Z
    _os.utime(src, (target_mtime, target_mtime))

    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": True},
    )
    _wait_for_jobs_to_drain(client)
    primary = client.get("/api/shooters/me/project").json()["stages"][0]["videos"][0]
    assert primary["role"] == "primary"
    assert primary["processed"]["beep"] is True
    assert primary["beep_time"] == 4.5


def test_scan_attaches_a_club_mates_clip_of_the_same_run_as_secondary(tmp_path: Path, monkeypatch) -> None:
    """Head cam folder first, then a club mate's phone folder: the phone
    clip of the same run joins the stage as a secondary and gets its beep
    queued; the head cam stays primary."""
    import os as _os
    from datetime import UTC, datetime

    from splitsmith import video_match

    # The clips' own camera clocks, as on real footage: creation_time = mtime.
    monkeypatch.setattr(
        video_match,
        "read_format_tags",
        lambda path, **_: {"creation_time": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()},
    )
    _enable_auto_beep(monkeypatch)
    _stub_detect(monkeypatch, beep_time=4.5)
    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="x")
    client = _MatchClient(app)
    sb = {
        "match": {"id": "1", "name": "x"},
        "competitors": [
            {
                "competitor_id": 1,
                "name": "T",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "S",
                        "time_seconds": 10.0,
                        "scorecard_updated_at": "2026-01-01T12:00:00+00:00",
                    }
                ],
            }
        ],
    }
    client.post("/api/shooters/me/scoreboard/import", json={"data": sb})

    def _folder(name: str, clip: str, epoch: int) -> Path:
        folder = tmp_path / name
        folder.mkdir()
        (folder / clip).write_bytes(b"")
        _os.utime(folder / clip, (epoch, epoch))
        return folder

    head_dir = _folder("head", "VID.mp4", 1767268500)  # 11:55:00Z
    phone_dir = _folder("from-magnus", "IMG.MOV", 1767268530)  # 30 s later, same run

    first = client.post(
        "/api/shooters/me/videos/scan", json={"source_dir": str(head_dir), "auto_assign_primary": True}
    ).json()
    _wait_for_jobs_to_drain(client)
    second = client.post(
        "/api/shooters/me/videos/scan", json={"source_dir": str(phone_dir), "auto_assign_primary": True}
    ).json()
    _wait_for_jobs_to_drain(client)

    assert first["auto_assigned"] == {"1": "raw/VID.mp4"}
    assert second["auto_assigned"] == {}
    assert second["auto_secondary"] == {"1": ["raw/IMG.MOV"]}
    videos = client.get("/api/shooters/me/project").json()["stages"][0]["videos"]
    roles = {v["path"]: v["role"] for v in videos}
    assert roles == {"raw/VID.mp4": "primary", "raw/IMG.MOV": "secondary"}
    assert all(v["processed"]["beep"] for v in videos)


def test_unbound_project_endpoints_return_409_no_project(tmp_path: Path) -> None:
    """Every project-bound route returns the structured 409 the SPA
    listens for to redirect to the picker."""
    app = create_app()
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/project")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "no_project"


def test_bind_recent_project_switches_in_memory(tmp_path: Path, _user_config_home: Path) -> None:
    """The picker POSTs to /bind to switch the active project without a
    server restart. Subsequent /api/health reflects the new binding and
    the recent-projects list bumps the entry to the top.
    """
    from splitsmith import user_config

    alpha = tmp_path / "alpha"
    _match_create_app(project_root=alpha, project_name="Alpha")
    # Now boot unbound and bind via the endpoint.
    app = create_app()
    client = _MatchClient(app)

    assert client.get("/api/health").json()["bound"] is False

    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(alpha.resolve())},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bound"] is True
    assert body["project_name"] == "Alpha"

    # Subsequent project endpoints now work.
    assert client.get("/api/shooters/me/project").status_code == 200

    # Recent-projects list bumped Alpha to the top.
    recent = user_config.get_recent_projects()
    assert recent[0].name == "Alpha"


def test_unbind_endpoint_is_gone(tmp_path: Path, _user_config_home: Path) -> None:
    """Tier 1 step 4 of doc 10: ``/api/me/recent-projects/unbind``
    was deleted because there's no server-side bound state to clear
    anymore -- match identity is per-request via the URL prefix.
    The SPA picker navigates between matches by URL.
    """
    app = _match_create_app(project_root=tmp_path / "match", project_name="x")
    raw = TestClient(app)

    assert raw.get("/api/health").json()["bound"] is False
    resp = raw.post("/api/me/recent-projects/unbind")
    # FastAPI returns 405 for an unknown method on a known prefix
    # or 404 when the path itself is unrouted; either is acceptable
    # confirmation that the endpoint is gone.
    assert resp.status_code in (404, 405)
    # Bare-path shooter access 409s because the singleton fallback
    # has been gone since step 2.
    assert raw.get("/api/shooters/me/project").status_code == 409


def test_match_export_endpoint_writes_fcpxml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            # Trims are pre-staged but no overlay, so disable to keep the
            # worker on the "skip per-stage exporter" branch (otherwise it
            # would try to render an overlay via ffmpeg).
            "include_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["kind"] == "match_export"
    final = _wait_for_job(client, job["id"])
    assert final["status"] == "succeeded", final
    result = final["result"]
    assert result is not None
    assert result["stage_count"] == 2
    assert Path(result["fcpxml_path"]).exists()
    assert Path(result["fcpxml_path"]).name.endswith("-match.fcpxml")
    # Per stage: 20s clip, beep@5s, last shot at 0.5s past beep -> head_trim
    # = 5.0 - 0.5 = 4.5s; tail_trim = (20 - 5.5) - 1.0 = 13.5s; eff = 2.0s.
    # Two stages -> 4.0s total.
    assert result["duration_seconds"] == pytest.approx(4.0, abs=0.1)


def test_export_over_a_gapped_stage_list_covers_every_remaining_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match export over 1, 2, 4, 5 -- the shape the stage-list editor
    leaves behind once stage 3 is removed (#521).

    Until that editor shipped, ``Match.stages`` was always 1..N, so any
    consumer that rebuilt stage numbers from a count instead of reading
    the list was indistinguishable from a correct one. The export path is
    the highest-risk consumer: it derives artifact filenames
    (``stage<N>_<slug>_trimmed.mp4``), audit keys (``audit/stage<N>.json``)
    and timeline clip names per stage. This asserts on the rendered
    FCPXML, not just the job's ``stage_count``: a count of 4 would also be
    satisfied by exporting stages 1, 2, 3, 4.
    """
    client, _ = _seed_match_export_project(tmp_path, stage_numbers=[1, 2, 4, 5])
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2, 4, 5],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            "include_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    result = final["result"]
    assert result is not None
    assert result["stage_count"] == 4
    # No "stage N missing its trim / audit" anomalies: every gapped number
    # resolved to the artifacts seeded under its own name.
    assert result["anomalies"] == [], result["anomalies"]

    xml = Path(result["fcpxml_path"]).read_text(encoding="utf-8")
    # Each stage contributes an asset whose src is its own trim. Stage 3
    # was never seeded, so a consumer that walked positions 0..3 as stages
    # 1..4 would either name stage 3 here or fail to find its trim.
    for n in (1, 2, 4, 5):
        assert f"stage{n}_stage-{n}_trimmed.mp4" in xml, f"stage {n} missing from the timeline"
    assert "stage3_stage-3_trimmed.mp4" not in xml
    assert xml.count("_trimmed.mp4") >= 4
    # Same per-stage arithmetic as the contiguous test: 2.0s each.
    assert result["duration_seconds"] == pytest.approx(8.0, abs=0.1)


def test_match_export_endpoint_400_on_empty_stage_numbers(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": []})
    assert resp.status_code == 400
    assert "stage_numbers cannot be empty" in resp.json()["detail"]


def test_match_export_endpoint_409_while_a_render_upload_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1100: a phone's render-and-upload writes the same MP4 and sidecar
    and then uploads them; a desk export for that shooter waits, and gets a
    plain 409 rather than the other job's snapshot."""
    import asyncio
    import json as _json
    import threading

    from splitsmith.ui import server as server_mod
    from splitsmith.ui.exports_api import MatchExportRequest

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    release = threading.Event()
    monkeypatch.setitem(
        state.jobs.bodies._bodies, "render_upload", lambda handle, **_a: release.wait(timeout=10)
    )
    match_id = _json.loads((root / "match.json").read_text(encoding="utf-8"))["match_id"]
    id_token = server_mod.current_match_id.set(match_id)
    root_token = server_mod.current_match_root.set(root)
    try:
        job = asyncio.run(
            state.jobs.submit(
                kind="render_upload",
                shooter_slug="me",
                args={"slug": "me", "req": MatchExportRequest(stage_numbers=[1]), "command_id": "c1"},
            )
        )
    finally:
        server_mod.current_match_root.reset(root_token)
        server_mod.current_match_id.reset(id_token)
    try:
        resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1]})
        assert resp.status_code == 409, resp.text
        assert "phone" in resp.json()["detail"]
        assert [j["kind"] for j in client.get("/api/me/jobs").json()] == ["render_upload"]
    finally:
        release.set()
    _wait_for_job(client, job.id)


def test_match_export_endpoint_400_on_padding_above_buffer(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    resp = client.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "head_pad_seconds": 99.0},
    )
    assert resp.status_code == 400
    assert "head_pad_seconds" in resp.json()["detail"]


def test_match_export_endpoint_400_on_unknown_stage(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [99]})
    assert resp.status_code == 400
    assert "stage 99 not found" in resp.json()["detail"]


def test_match_export_endpoint_job_fails_when_trim_unrecoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source video missing AND no pre-staged trim -> the job fails (not
    the endpoint). Pre-flight catches reachability up-front: the source is
    ``project_root/raw/VID1.mp4`` which the seed helper writes, so to
    simulate a missing source we delete it after seeding and remove the
    pre-staged trim. The endpoint's source-reachability check then 424s."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    shooter_root = project_root / "shooters" / "me"
    (shooter_root / "exports" / "stage1_stage-1_trimmed.mp4").unlink()
    # Make the source unreachable by removing the underlying file (the
    # symlink in raw/ stays). The endpoint pre-flight surfaces this as a
    # structured 424.
    (shooter_root / "raw" / "VID1.mp4").unlink()
    resp = client.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "include_overlay": False},
    )
    assert resp.status_code == 424
    detail = resp.json()["detail"]
    # _ensure_source_reachable returns structured detail with a code.
    assert detail.get("code") == "source_unreachable" if isinstance(detail, dict) else False, detail


def test_cleanup_plan_returns_per_category_totals(tmp_path: Path) -> None:
    client, _ = _seed_cleanup_project(tmp_path)
    resp = client.get(
        "/api/shooters/me/project/cleanup/plan",
        params={"categories": "audio,exports-overlays"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_file_count"] == 2
    assert body["total_bytes"] == 1024 + 4096
    by_cat = body["totals_by_category"]
    assert by_cat["audio"]["file_count"] == 1
    assert by_cat["exports-overlays"]["bytes"] == 4096


def test_cleanup_plan_empty_categories_yields_empty_plan(tmp_path: Path) -> None:
    client, _ = _seed_cleanup_project(tmp_path)
    resp = client.get("/api/shooters/me/project/cleanup/plan", params={"categories": ""})
    assert resp.status_code == 200
    assert resp.json()["total_file_count"] == 0


def test_cleanup_plan_unknown_category_silently_dropped(tmp_path: Path) -> None:
    client, _ = _seed_cleanup_project(tmp_path)
    resp = client.get(
        "/api/shooters/me/project/cleanup/plan",
        params={"categories": "audio,bogus-category"},
    )
    assert resp.status_code == 200
    assert resp.json()["total_file_count"] == 1


def test_cleanup_apply_deletes_and_returns_result(tmp_path: Path) -> None:
    client, root = _seed_cleanup_project(tmp_path)
    shooter_root = root / "shooters" / "me"
    resp = client.post(
        "/api/shooters/me/project/cleanup",
        json={"categories": ["audio", "exports-overlays"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["bytes_freed"] == 1024 + 4096
    assert len(body["result"]["deleted"]) == 2
    assert not (shooter_root / "audio" / "stage1_primary.wav").exists()
    assert not (shooter_root / "exports" / "stage1_one_overlay.mov").exists()
    # Audit JSON survives unless explicitly requested
    assert (shooter_root / "audit" / "stage1.json").exists()


def test_cleanup_apply_audit_data_destructive(tmp_path: Path) -> None:
    """Selecting the category IS the opt-in for audit data, so the
    item-level gate must not re-gate it.

    Every audit doc is unreconstructable by definition -- that is why the
    category sits outside ``SAFE_CATEGORIES``. If ``cleanup_apply``
    re-derived the item gate as "drop everything unreconstructable"
    instead of reusing ``needs_item_opt_in``, this POST would select the
    category and then silently empty it, and the checkbox would do
    nothing. That trap already cost one round on #924; this is the pin
    for the API surface (#926).
    """
    client, root = _seed_cleanup_project(tmp_path)
    shooter_root = root / "shooters" / "me"
    resp = client.post("/api/shooters/me/project/cleanup", json={"categories": ["audit-data"]})
    assert resp.status_code == 200
    assert not (shooter_root / "audit" / "stage1.json").exists()


def test_cleanup_apply_holds_back_unrebuildable_files_by_default(tmp_path: Path) -> None:
    """A direct POST that says nothing must not destroy data (#926).

    The SPA's per-item ticks gate its Confirm button, and the CLI has
    ``--include-unrebuildable``; the endpoint they both sit on used to
    enforce neither, so anything POSTing a category list -- a script, a
    replayed request, the dialog with its gate bypassed -- deleted
    irreplaceable files with no consent step anywhere in the path.

    Asserted against the filesystem, not the response body: the plan in
    the response lists the held-back item either way, by design.
    """
    client, root = _seed_cleanup_project(tmp_path)
    rebuildable, unrebuildable = _seed_unrebuildable_export(root)

    resp = client.post(
        "/api/shooters/me/project/cleanup",
        json={"categories": ["exports-light"]},
    )

    assert resp.status_code == 200
    assert unrebuildable.exists(), "an unrebuildable file was deleted without an opt-in"
    assert not rebuildable.exists(), "the gate swallowed a rebuildable neighbour too"
    body = resp.json()
    assert body["result"]["bytes_freed"] == 32
    # The plan still names the file it declined to delete -- omitting it
    # from a list that promises what can be reclaimed makes it a liar.
    assert any(i["path"].endswith("stage9_nine.csv") for i in body["plan"]["items"])


def test_cleanup_apply_include_unrebuildable_deletes_it(tmp_path: Path) -> None:
    """The same POST with the opt-in set goes through (#926)."""
    client, root = _seed_cleanup_project(tmp_path)
    rebuildable, unrebuildable = _seed_unrebuildable_export(root)

    resp = client.post(
        "/api/shooters/me/project/cleanup",
        json={"categories": ["exports-light"], "include_unrebuildable": True},
    )

    assert resp.status_code == 200
    assert not unrebuildable.exists()
    assert not rebuildable.exists()
    assert resp.json()["result"]["bytes_freed"] == 32 + 64


def test_cleanup_apply_refuses_while_jobs_active(tmp_path: Path) -> None:
    """When a job is still PENDING/RUNNING the endpoint returns 409."""
    from splitsmith.ui.jobs import JobStatus

    client, _ = _seed_cleanup_project(tmp_path)
    state = client.app.state.splitsmith_state

    # Submit a job that blocks until we release it; the endpoint sees
    # status RUNNING during that window.
    started = threading.Event()
    release = threading.Event()

    def worker(_handle):  # type: ignore[no-untyped-def]
        started.set()
        release.wait(timeout=5.0)

    asyncio.run(submit_fn(state.jobs, kind="test_block", fn=worker))
    assert started.wait(timeout=2.0)

    try:
        resp = client.post("/api/shooters/me/project/cleanup", json={"categories": ["audio"]})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "jobs_active"
    finally:
        release.set()

    # Wait until the registry flips it to terminal state.
    deadline_attempts = 0
    while deadline_attempts < 100:
        snap = asyncio.run(state.jobs.list())
        if all(j.status not in (JobStatus.PENDING, JobStatus.RUNNING) for j in snap):
            break
        deadline_attempts += 1
        import time as _time

        _time.sleep(0.02)

    # And once the job finishes, the endpoint goes through.
    resp = client.post("/api/shooters/me/project/cleanup", json={"categories": ["audio"]})
    assert resp.status_code == 200


def test_cleanup_endpoints_409_when_no_project_bound(tmp_path: Path) -> None:
    """Picker-mode (no bound project) -> both endpoints return 409 no_project."""
    app = create_app()  # unbound
    client = _MatchClient(app)
    assert client.get("/api/shooters/me/project/cleanup/plan?categories=audio").status_code == 409
    cleanup_resp = client.post(
        "/api/shooters/me/project/cleanup",
        json={"categories": ["audio"]},
    )
    assert cleanup_resp.status_code == 409


def test_get_automation_returns_resolved_settings_and_provenance(tmp_path: Path) -> None:
    """No overrides -> source is 'global', current value is the field
    default. Smoke-test that the response shape matches what the SPA's
    SettingProvenance component expects."""
    root = tmp_path / "match"
    MatchProject.init(root, name="Auto Match").save(root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/automation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"] == {
        "shot_detect_on_beep_verified": True,
        "beep_low_confidence_threshold": 0.97,
    }
    prov = body["provenance"]["shot_detect_on_beep_verified"]
    assert prov["source"] == "global"
    assert prov["global_value"] is True
    assert prov["project_value"] is None
    assert prov["cli_value"] is None
    threshold_prov = body["provenance"]["beep_low_confidence_threshold"]
    assert threshold_prov["source"] == "global"
    assert threshold_prov["global_value"] == 0.97


def test_get_automation_reports_project_provenance_when_overridden(
    tmp_path: Path,
) -> None:
    root, _shooter_root = _scaffold_match(tmp_path, name="Override Match", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.automation = AutomationOverride(shot_detect_on_beep_verified=False)
    project.save(_shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/automation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["shot_detect_on_beep_verified"] is False
    prov = body["provenance"]["shot_detect_on_beep_verified"]
    assert prov["source"] == "project"
    assert prov["project_value"] is False
    assert prov["global_value"] is True


def test_dismiss_nudge_persists_stage_number(tmp_path: Path) -> None:
    """#218 phase 4 -- POST adds the stage to nudges_dismissed_stages
    and the response carries the updated project."""
    root, _shooter_root = _scaffold_match(tmp_path, name="Nudge Match", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(_shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/shooters/me/project/nudges/dismiss",
        json={"stage_number": 1, "dismissed": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["nudges_dismissed_stages"] == [1]


def test_dismiss_nudge_idempotent(tmp_path: Path) -> None:
    root, _shooter_root = _scaffold_match(tmp_path, name="Idempotent", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.stages = [
        StageEntry(
            stage_number=2,
            stage_name="Two",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.nudges_dismissed_stages = [2]
    project.save(_shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/shooters/me/project/nudges/dismiss",
        json={"stage_number": 2, "dismissed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["nudges_dismissed_stages"] == [2]


def test_dismiss_nudge_clears_when_dismissed_false(tmp_path: Path) -> None:
    root, _shooter_root = _scaffold_match(tmp_path, name="Reset", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.stages = [
        StageEntry(
            stage_number=3,
            stage_name="Three",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.nudges_dismissed_stages = [3]
    project.save(_shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/shooters/me/project/nudges/dismiss",
        json={"stage_number": 3, "dismissed": False},
    )
    assert resp.status_code == 200
    assert resp.json()["nudges_dismissed_stages"] == []


def test_dismiss_nudge_404_for_unknown_stage(tmp_path: Path) -> None:
    root = tmp_path / "match"
    MatchProject.init(root, name="NoStage").save(root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/shooters/me/project/nudges/dismiss",
        json={"stage_number": 99, "dismissed": True},
    )
    assert resp.status_code == 404


def test_hitl_queue_lists_low_confidence_auto_beep(tmp_path: Path) -> None:
    """Auto-detected beep below the threshold lands in the queue."""
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "HITL Low",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.4,
        beep_reviewed=False,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/hitl-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold"] == 0.97
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == "beep_low_confidence"
    assert item["stage_number"] == 1
    assert item["confidence"] == 0.4
    assert "candidate" in item["suggested_action"].lower()


def test_hitl_queue_lists_missing_beep(tmp_path: Path) -> None:
    """Auto-detect failure shows up as ``beep_missing``."""
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "HITL Missing",
        beep_time=None,
        beep_source="auto",
        beep_confidence=None,
        beep_reviewed=False,
        beep_auto_detect_failed=True,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/hitl-queue")
    item = resp.json()["items"][0]
    assert item["kind"] == "beep_missing"
    assert item["confidence"] is None
    assert "manual" in item["suggested_action"].lower()


def test_hitl_queue_omits_high_confidence_reviewed_beep(tmp_path: Path) -> None:
    """High-confidence auto-trusted beeps don't need attention."""
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "HITL Trusted",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.85,
        beep_reviewed=True,  # auto-trusted via threshold gate
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/hitl-queue")
    assert resp.json()["items"] == []


def test_hitl_queue_omits_manual_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project_with_primary(
        root,
        "HITL Manual",
        beep_time=22.5,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/hitl-queue")
    assert resp.json()["items"] == []


def test_hitl_queue_orders_by_stage_number(tmp_path: Path) -> None:
    """Stages 3, 1, 2 with all-needs-review beeps come back in 1, 2, 3 order."""
    root, _shooter_root = _scaffold_match(tmp_path, name="HITL Order", subdir="match")
    project = MatchProject.load(_shooter_root)
    project.stages = []
    for n in (3, 1, 2):
        v = StageVideo(
            path=root / f"p{n}.mp4",
            role="primary",
            beep_time=10.0,
            beep_source="auto",  # type: ignore[arg-type]
            beep_confidence=0.4,
            beep_reviewed=False,
        )
        project.stages.append(StageEntry(stage_number=n, stage_name=f"S{n}", time_seconds=12.0, videos=[v]))
    project.save(_shooter_root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.get("/api/shooters/me/hitl-queue")
    stages = [item["stage_number"] for item in resp.json()["items"]]
    assert stages == [1, 2, 3]


def test_hitl_queue_threshold_respects_project_override(tmp_path: Path) -> None:
    """Project-level threshold override flows through to the response."""
    root = tmp_path / "match"
    project = _build_project_with_primary(
        root,
        "HITL Threshold",
        beep_time=22.5,
        beep_source="auto",
        beep_confidence=0.65,
        beep_reviewed=False,
    )
    project.automation = AutomationOverride(beep_low_confidence_threshold=0.9)
    project.save(root / "shooters" / "me")
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    body = client.get("/api/shooters/me/hitl-queue").json()
    assert body["threshold"] == 0.9
    assert body["items"][0]["confidence"] == 0.65


def test_post_settings_patches_automation_override(tmp_path: Path) -> None:
    """Round-trip: post an automation override, then GET /api/automation
    sees it as the project layer."""
    root = tmp_path / "match"
    MatchProject.init(root, name="Patch Match").save(root)
    app = _match_create_app(project_root=root, project_name="ignored")
    client = _MatchClient(app)

    resp = client.post(
        "/api/shooters/me/project/settings",
        json={"automation": {"shot_detect_on_beep_verified": False}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["automation"]["shot_detect_on_beep_verified"] is False

    auto = client.get("/api/shooters/me/automation").json()
    assert auto["settings"]["shot_detect_on_beep_verified"] is False
    assert auto["provenance"]["shot_detect_on_beep_verified"]["source"] == "project"


def test_download_export_file_serves_local_and_guards_traversal(tmp_path: Path) -> None:
    """The export download endpoint serves a deliverable off the local
    exports dir, 404s an absent file, and refuses a path that escapes the
    exports folder (encoded ``../``)."""
    from splitsmith import match_model

    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    exports = match_model.Match.shooter_root(root, "me") / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "stage1_one.fcpxml").write_bytes(b"<fcpxml/>")

    resp = client.get("/api/shooters/me/exports/file/stage1_one.fcpxml")
    assert resp.status_code == 200
    assert resp.content == b"<fcpxml/>"
    assert resp.headers["content-type"].startswith("application/xml")

    assert client.get("/api/shooters/me/exports/file/missing.csv").status_code == 404

    resp = client.get("/api/shooters/me/exports/file/%2e%2e%2f%2e%2e%2fmatch.json")
    assert resp.status_code == 400


def test_export_overview_lists_match_deliverables_for_download(tmp_path: Path) -> None:
    """#629: the overview is what survives a reload, so the match-level
    output has to be reachable through it.

    The download route already served these files -- it pulls from object
    storage on hosted -- but nothing told the client their names outside
    the export job's own in-session ``result``. Per-stage artefacts must
    not leak into this list: they have their own rows.
    """
    from splitsmith import match_model

    root = tmp_path / "match"
    app = _match_create_app(project_root=root, project_name="x")
    client = _MatchClient(app)

    exports = match_model.Match.shooter_root(root, "me") / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "bromma-2026-match.fcpxml").write_bytes(b"<fcpxml/>")
    (exports / "bromma-2026-match.srt").write_bytes(b"1\n")
    (exports / "stage1_one_trimmed.mp4").write_bytes(b"\x00")

    body = client.get("/api/shooters/me/exports/overview").json()
    assert sorted(m["filename"] for m in body["match_exports"]) == [
        "bromma-2026-match.fcpxml",
        "bromma-2026-match.srt",
    ]
    # Every name it hands back is one the download route actually serves.
    for m in body["match_exports"]:
        assert client.get(f"/api/shooters/me/exports/file/{m['filename']}").status_code == 200


def test_match_export_default_main_camera_is_the_shooters_saved_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main_camera: "default"`` (the Export page's default) reaches the
    export as the shooter's saved ``compare_camera``; the inset fields pass
    through."""
    from splitsmith.ui import match_exports as match_exports_mod

    client, root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    project_root = next((root / "shooters").iterdir())
    project = MatchProject.load(project_root)
    project.compare_camera = "hand"
    project.save(project_root)
    seen: dict[str, object] = {}
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen["request"] = kwargs["request"]
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1],
            "include_overlay": False,
            "inset_camera": "head",
            "inset_corner": "top-left",
            "inset_size": "large",
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    request = seen["request"]
    assert isinstance(request, match_exports_mod.MatchExportRequestData)
    assert request.main_camera == "hand"
    assert (request.inset_camera, request.inset_corner, request.inset_size) == ("head", "top-left", "large")


def test_scoreboard_auto_pin_stores_the_competitors_division(tmp_path: Path) -> None:
    """The division as SSI spells it is what the title page prints; the
    auto-pin of a combined upload stores it with the pin."""
    combined, divisions = _combined_divisions()
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    body = client.post("/api/shooters/me/scoreboard/upload", json={"data": combined}).json()
    assert body["competitor_division"] == divisions[body["selected_competitor_id"]]


def test_scoreboard_select_shooter_stores_the_competitors_division(tmp_path: Path) -> None:
    """A manual pin stores the division too, even when stage times are
    unavailable offline: the pin persists, and its division with it."""
    from splitsmith.match_project import MatchProject

    _combined, divisions = _combined_divisions()
    project_root = tmp_path / "match"
    client = _MatchClient(_match_create_app(project_root=project_root, project_name="x"))
    client.post("/api/shooters/me/scoreboard/upload", json={"data": _load_v1_match_fixture()})
    client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": 40821, "competitor_id": 727562},
    )
    assert MatchProject.load(project_root / "shooters" / "me").competitor_division == divisions[727562]


def test_match_export_title_page_carries_the_division_unless_turned_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On by default, the shooter's division is its own line on the title
    page, right under the name; ``title_division: false`` drops it."""
    from splitsmith.ui import match_exports as match_exports_mod

    client, root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    project_root = next((root / "shooters").iterdir())
    project = MatchProject.load(project_root)
    project.competitor_name = "Magnus Åström"
    project.competitor_division = "Classic Major"
    from splitsmith.identity import ShooterIdentity

    project.identity = ShooterIdentity(accent="#123456", club="PK")
    project.save(project_root)
    seen: list[match_exports_mod.MatchExportRequestData] = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])  # type: ignore[arg-type]
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    for extra in ({}, {"title_division": False}, {"card_variant": "rise"}):
        resp = client.post(
            "/api/shooters/me/export/match",
            json={"stage_numbers": [1], "include_overlay": False, "title_page": True, **extra},
        )
        assert resp.status_code == 200, resp.text
        assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    on, off, _ = (r.title_page_info for r in seen)
    # The club line (#1243) sits between the shooter's name and the division.
    assert on[on.index("Magnus Åström") + 1 :][:2] == ("PK", "Classic Major")
    assert "Classic Major" not in off
    # #1242: the Look variant is a request field, not a CLI-only knob.
    assert [r.card_variant for r in seen] == ["default", "default", "rise"]
    # #1243: the job resolves the shooter's identity for the cards.
    assert seen[0].shooter_identity is not None
    assert seen[0].shooter_identity.accent == "#123456" and seen[0].shooter_identity.club == "PK"
    assert seen[0].shooter_identity.label == "Magnus Åström"


@pytest.mark.parametrize(
    ("record", "request_style", "redraws"),
    [
        # No record: an overlay from before records existed cannot vouch
        # for the audit it was drawn from, so it is always drawn again.
        (None, {}, True),
        (None, {"overlay_variant": "plate"}, True),
        (None, {"overlay_speed_colors": False}, True),
        ("default", {}, False),
        ("default", {"overlay_speed_colors": False}, False),
        ("plate", {}, True),
        ("plate", {"overlay_variant": "plate"}, False),
        ("plate", {"overlay_variant": "plate", "overlay_landing": False}, True),
        (None, {"overlay_speed_colors": True}, True),
    ],
)
def test_match_export_redraws_an_overlay_only_when_its_record_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: str | None,
    request_style: dict,
    redraws: bool,
) -> None:
    """An overlay on disk is reused only when it was drawn the way this
    export asks, from the audit as it stands; one without a record is drawn
    again, and Classic ignores the style toggles."""
    import json as _json

    from splitsmith.overlay import render as overlay_render
    from splitsmith.overlay.hud import HudOptions, overlay_settings
    from splitsmith.ui import exports as exports_mod

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    exports_dir = root / "shooters" / "me" / "exports"
    (exports_dir / "stage1_stage-1_overlay.mov").write_bytes(b"old")
    if record is not None:
        settings = overlay_settings(
            look="splitsmith",
            variant=record,
            options=HudOptions(),
            codec="auto",
            max_height=None,
            max_fps=None,
            audit_revision=exports_mod.overlay_audit_revision(
                root / "shooters" / "me" / "audit" / "stage1.json"
            ),
        )
        (exports_dir / "stage1_stage-1_overlay.json").write_text(_json.dumps(settings), encoding="utf-8")
    calls: list[exports_mod.StageExportRequest] = []
    real = exports_mod.export_stage

    def capture(**kwargs: object) -> object:
        request = kwargs["request"]
        assert isinstance(request, exports_mod.StageExportRequest)
        calls.append(request)
        return real(**kwargs)

    def fake_render(**kwargs: object) -> Path:
        out = kwargs["output_path"]
        assert isinstance(out, Path)
        out.write_bytes(b"new")
        return out

    monkeypatch.setattr(exports_mod, "export_stage", capture)
    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    resp = client.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "include_overlay": True, **request_style},
    )
    assert resp.status_code == 200, resp.text
    _wait_for_job(client, resp.json()["id"])
    assert bool(calls) is redraws
    if redraws:
        assert calls[0].overlay_variant == request_style.get("overlay_variant", "default")


def test_match_export_never_reuses_a_legacy_overlay_over_an_unreadable_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record-less overlay reads as no audit revision, and so does an audit
    that will not parse: the two must not agree into a reuse. The per-stage
    export runs and reports the broken audit instead of stitching an
    overlay nothing vouches for."""
    from splitsmith.ui import exports as exports_mod

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    (root / "shooters" / "me" / "exports" / "stage1_stage-1_overlay.mov").write_bytes(b"old")
    (root / "shooters" / "me" / "audit" / "stage1.json").write_text("{not json", encoding="utf-8")
    calls: list[object] = []
    real = exports_mod.export_stage

    def capture(**kwargs: object) -> object:
        calls.append(kwargs["request"])
        return real(**kwargs)

    monkeypatch.setattr(exports_mod, "export_stage", capture)
    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1], "include_overlay": True})
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "failed"
    assert calls, "the per-stage export ran rather than reusing the overlay"


def test_a_failed_overlay_redraw_never_ships_the_stale_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit moved, so the overlay is drawn again; when that draw fails
    the old MOV stays on disk (never deleted on a transient failure) but is
    not stitched, and the result says why. Before, it shipped silently."""
    import json as _json

    from splitsmith.overlay import render as overlay_render
    from splitsmith.ui import match_exports as match_mod

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    _stub_match_export_probe(monkeypatch)
    assert _run_overlay_match_export(client, monkeypatch, {}), "first export draws"
    stale = root / "shooters" / "me" / "exports" / "stage1_stage-1_overlay.mov"
    assert stale.exists()

    audit_path = root / "shooters" / "me" / "audit" / "stage1.json"
    doc = _json.loads(audit_path.read_text(encoding="utf-8"))
    _edit_shot_time(doc)
    audit_path.write_text(_json.dumps(doc), encoding="utf-8")

    def failing_render(**_kwargs: object) -> Path:
        raise overlay_render.OverlayRenderError("ffmpeg went away")

    stitched: list[object] = []
    real_export_match = match_mod.export_match

    def capture_match(**kwargs: object) -> object:
        stitched.extend(kwargs["stages"])  # type: ignore[arg-type]
        return real_export_match(**kwargs)

    monkeypatch.setattr(overlay_render, "render_overlay", failing_render)
    monkeypatch.setattr(match_mod, "export_match", capture_match)
    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1], "include_overlay": True})
    assert resp.status_code == 200, resp.text
    job = _wait_for_job(client, resp.json()["id"])
    assert job["status"] == "succeeded", job
    assert [s.overlay_path for s in stitched] == [None]  # type: ignore[attr-defined]
    assert stale.exists(), "a rendered file is never deleted on a failed redraw"
    anomalies = job["result"]["anomalies"]
    assert any("stage 1: overlay left out" in a and "ffmpeg went away" in a for a in anomalies), anomalies
    assert not any("stage 1: overlay not available" in a for a in anomalies), anomalies


def test_a_move_never_makes_a_camera_without_a_beep_the_primary_over_audit_work(
    tmp_path: Path, monkeypatch
) -> None:
    """#1496: a move that made a beep-less camera the primary of an audited
    stage left a primary with no trim, so Audit could not load it. The move
    is refused with the way out; a camera with a confirmed beep (the one the
    audit was made on, put back) still moves in."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, src = _seed_project_with_primary(tmp_path)
    (src.parent / "HEAD.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src.parent), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "secondary"},
    )
    shooter_root = tmp_path / "match" / "shooters" / "me"
    (shooter_root / "audit").mkdir(parents=True, exist_ok=True)
    (shooter_root / "audit" / "stage1.json").write_text(
        json.dumps({"beep_time": 5.0, "shots": [{"time": 6.0, "ms_after_beep": 1000}]}), encoding="utf-8"
    )

    refused = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "primary"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "primary_without_beep"
    project = MatchProject.load(shooter_root)
    assert project.stages[0].primary().path.as_posix() != "raw/HEAD.mp4", "nothing moved"

    project_json = shooter_root / "project.json"
    doc = json.loads(project_json.read_text(encoding="utf-8"))
    head = next(v for v in doc["stages"][0]["videos"] if v["path"] == "raw/HEAD.mp4")
    head.update(beep_time=3.5, beep_source="manual", beep_reviewed=True)
    project_json.write_text(json.dumps(doc), encoding="utf-8")
    allowed = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "primary"},
    )
    assert allowed.status_code == 200, allowed.text
    assert (shooter_root / "audit" / "stage1.json").exists(), "a move never touches the audit"


def test_a_mirrors_primary_without_a_beep_says_so_instead_of_queueing_audio(
    tmp_path: Path, monkeypatch
) -> None:
    """#1496: on a desktop mirror a primary with no beep was never trimmed
    and its source never leaves the desktop, so a derive_audio job could
    only fail and the page waited on it. The audio route says why."""
    from splitsmith.ui import server as server_mod

    client, _ = _seed_project_with_primary(tmp_path)
    monkeypatch.setattr(server_mod, "_is_mirror", lambda: True)
    resp = client.get("/api/shooters/me/stages/1/audio")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "primary_not_ready"
    assert not [j for j in client.get("/api/me/jobs").json() if j["kind"] == "derive_audio"]
    monkeypatch.setattr(server_mod, "_is_mirror", lambda: False)
    assert client.get("/api/shooters/me/stages/1/audio").status_code != 409, "locally the source is here"


def _two_cameras_with_audit(
    tmp_path: Path,
    *,
    head_confirmed: bool,
    stamp: str | None = "primary",
    head_trimmed: bool = True,
    shots: tuple[float, ...] = (6.0, 6.5),
):
    """Stage 1 with VID primary (beep 20.0, confirmed) and HEAD secondary
    (beep 3.5), both trimmed with the 5 s pre-buffer, so VID's clip beep is
    5.0 and HEAD's 3.5; an audit of ``shots`` made on VID's clip. ``stamp``
    names the camera the audit says it is on: ``"primary"`` (VID),
    ``"head"``, or ``None``."""
    client, src = _seed_project_with_primary(tmp_path)
    (src.parent / "HEAD.mp4").write_bytes(b"")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src.parent), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "secondary"},
    )
    shooter_root = tmp_path / "match" / "shooters" / "me"
    project_json = shooter_root / "project.json"
    doc = json.loads(project_json.read_text(encoding="utf-8"))
    videos = {v["path"]: v for v in doc["stages"][0]["videos"]}
    videos["raw/VID.mp4"].update(beep_time=20.0, beep_source="manual", beep_reviewed=True)
    videos["raw/HEAD.mp4"].update(beep_time=3.5, beep_source="manual", beep_reviewed=head_confirmed)
    project_json.write_text(json.dumps(doc), encoding="utf-8")
    ids = {"primary": videos["raw/VID.mp4"]["video_id"], "head": videos["raw/HEAD.mp4"]["video_id"]}
    from splitsmith.ui import audio as audio_helpers

    project = MatchProject.load(shooter_root)
    for v in project.stages[0].videos:
        if v.path.name == "VID.mp4" or head_trimmed:
            trim = audio_helpers.trimmed_video_path(shooter_root, 1, v, project=project)
            trim.parent.mkdir(parents=True, exist_ok=True)
            trim.write_bytes(b"trim")  # a trim counts when non-empty
    audit = {
        "beep_time": 5.0,
        "shots": [
            {"shot_number": i, "time": t, "ms_after_beep": round((t - 5.0) * 1000)}
            for i, t in enumerate(shots, start=1)
        ],
        "events": [{"id": "r1", "kind": "reload", "start": 0.6, "end": 1.2, "source": "manual"}],
    }
    if stamp is not None:
        audit["clip_video_id"] = ids[stamp]
    (shooter_root / "audit").mkdir(parents=True, exist_ok=True)
    (shooter_root / "audit" / "stage1.json").write_text(json.dumps(audit), encoding="utf-8")
    return client, shooter_root, ids


def test_make_primary_carries_the_audit_onto_the_new_cameras_clip(tmp_path: Path, monkeypatch) -> None:
    """#1496: the audit was backed up and the stage detected again, so the
    work was gone from the stage; now the shots move by the difference of
    the two clips' beeps and keep their times from the beep."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, ids = _two_cameras_with_audit(tmp_path, head_confirmed=True)

    asked = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": False},
    )
    assert asked.status_code == 409 and asked.json()["detail"]["code"] == "audit_exists"
    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 200, resp.text
    audit = json.loads((shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert audit["beep_time"] == 3.5
    assert [s["time"] for s in audit["shots"]] == [4.5, 5.0]
    assert [s["ms_after_beep"] for s in audit["shots"]] == [1000, 1500]
    assert audit["events"][0]["start"] == 0.6, "regions are beep-relative"
    assert audit["clip_video_id"] == ids["head"]
    moved = [e for e in audit["audit_events"] if e["kind"] == "clip_camera_changed"]
    assert [e["payload"] for e in moved] == [
        {"from_video_id": ids["primary"], "to_video_id": ids["head"], "shift_ms": -1500.0}
    ]
    backups = list((shooter_root / "audit").glob("stage1.pre-swap-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8"))["beep_time"] == 5.0
    # Audit reads it on the new primary's clip as stored: nothing to rebase.
    served = client.get("/api/shooters/me/stages/1/audit").json()
    assert [s["time"] for s in served["shots"]] == [4.5, 5.0]


def test_make_primary_needs_the_new_cameras_beep_over_audit_work(tmp_path: Path, monkeypatch) -> None:
    """Without its beep the shots cannot be placed on the new clip, and the
    old swap left a primary with no trim. Refused even when confirmed."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, _ids = _two_cameras_with_audit(tmp_path, head_confirmed=False)
    before = (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "primary_without_beep"
    assert MatchProject.load(shooter_root).stages[0].primary().path.as_posix() == "raw/VID.mp4"
    assert (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8") == before
    assert not list((shooter_root / "audit").glob("stage1.pre-swap-*.json"))


def test_make_primary_on_an_unstamped_audit_treats_it_as_the_outgoing_primarys(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, ids = _two_cameras_with_audit(tmp_path, head_confirmed=True, stamp=None)

    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 200, resp.text
    audit = json.loads((shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert [s["time"] for s in audit["shots"]] == [4.5, 5.0]
    moved = [e["payload"] for e in audit["audit_events"] if e["kind"] == "clip_camera_changed"]
    assert moved[0]["from_video_id"] == ids["primary"]


def test_a_move_carries_an_audit_stamped_with_the_camera_moved_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, ids = _two_cameras_with_audit(tmp_path, head_confirmed=True)

    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "primary"},
    )

    assert resp.status_code == 200, resp.text
    audit = json.loads((shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))
    assert [s["time"] for s in audit["shots"]] == [4.5, 5.0]
    assert audit["clip_video_id"] == ids["head"]


@pytest.mark.parametrize("stamp", ["head", None])
def test_a_move_leaves_an_audit_on_the_incoming_camera_or_unstamped(
    tmp_path: Path, monkeypatch, stamp: str | None
) -> None:
    """Putting a stage back on the camera it was audited on is how #1496's
    stages were repaired; an audit from before the stamp cannot say which
    camera it is on, so a plain move leaves it as it always did."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, _ids = _two_cameras_with_audit(tmp_path, head_confirmed=True, stamp=stamp)
    before = (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/HEAD.mp4", "to_stage_number": 1, "role": "primary"},
    )

    assert resp.status_code == 200, resp.text
    assert (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8") == before


def test_an_audit_save_records_the_camera_its_clip_is(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, ids = _two_cameras_with_audit(tmp_path, head_confirmed=True, stamp=None)
    doc = client.get("/api/shooters/me/stages/1/audit").json()

    resp = client.put("/api/shooters/me/stages/1/audit", json=doc)

    assert resp.status_code == 200, resp.text
    assert (
        json.loads((shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8"))["clip_video_id"]
        == ids["primary"]
    )


def test_make_primary_waits_for_the_new_cameras_trim(tmp_path: Path, monkeypatch) -> None:
    """Carried onto an uncut camera's source clock, the audit went wrong when
    the re-trim landed: a shot before the beep made the doc read as mixed
    and every split moved. Refused, with the trim queued for the retry."""
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, ids = _two_cameras_with_audit(tmp_path, head_confirmed=True, head_trimmed=False)
    before = (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8")

    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "primary_not_trimmed"
    assert MatchProject.load(shooter_root).stages[0].primary().path.as_posix() == "raw/VID.mp4"
    assert (shooter_root / "audit" / "stage1.json").read_text(encoding="utf-8") == before
    kinds = [(j["kind"], j.get("video_id")) for j in client.get("/api/me/jobs").json()]
    assert ("trim", ids["head"]) in kinds


def test_a_shot_before_the_beep_carries_with_the_rest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")
    client, shooter_root, _ids = _two_cameras_with_audit(tmp_path, head_confirmed=True, shots=(4.9, 6.0))

    resp = client.post(
        "/api/shooters/me/assignments/swap-primary",
        json={"video_path": "raw/HEAD.mp4", "stage_number": 1, "confirm": True},
    )

    assert resp.status_code == 200, resp.text
    served = client.get("/api/shooters/me/stages/1/audit").json()
    assert served["beep_time"] == 3.5
    assert [s["time"] for s in served["shots"]] == [3.4, 4.5]
    assert [s["ms_after_beep"] for s in served["shots"]] == [-100, 1000]
