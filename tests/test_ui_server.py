"""Tests for the production UI's FastAPI backend."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


def test_health_returns_project_info(tmp_path: Path) -> None:
    app = create_app(project_root=tmp_path / "match", project_name="Test Match")
    client = TestClient(app)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["project_name"] == "Test Match"
    assert body["schema_version"] == 1


def test_get_project_returns_full_dump(tmp_path: Path) -> None:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Get Project Match")
    project.competitor_name = "API Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(root)

    app = create_app(project_root=root, project_name="ignored, project already exists")
    client = TestClient(app)

    resp = client.get("/api/project")
    assert resp.status_code == 200
    body = resp.json()
    # The project name from disk wins, not the create_app argument.
    assert body["name"] == "Get Project Match"
    assert body["competitor_name"] == "API Tester"
    assert len(body["stages"]) == 1
    assert body["stages"][0]["videos"][0]["role"] == "primary"


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

    app = create_app(project_root=tmp_path / "match", project_name="SPA Match")
    client = TestClient(app)

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


def test_external_edit_visible_without_restart(tmp_path: Path) -> None:
    """External edits to project.json must appear on the next request -- the
    server doesn't cache the model in memory."""
    root = tmp_path / "match"
    app = create_app(project_root=root, project_name="External Edit Match")
    client = TestClient(app)

    assert client.get("/api/project").json()["competitor_name"] is None

    project = MatchProject.load(root)
    project.competitor_name = "Edited Externally"
    project.save(root)

    assert client.get("/api/project").json()["competitor_name"] == "Edited Externally"
