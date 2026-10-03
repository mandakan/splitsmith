"""Path fields in request bodies that hosted mode serves but never resolves.

These routes stay reachable hosted (the SPA uses them there), so the
route table cannot simply 404 them; instead the path-like field is a
lookup key or is refused:

- ``POST /api/me/recent-projects/bind``: hosted resolves ``path`` against
  the caller's own picker rows and never touches the disk (no scaffold
  with ``create``, no loading a folder's ``match.json`` or ``.env``).
- ``POST .../export/match``: ``intro_path`` / ``outro_path`` name files on
  the server, so hosted refuses them.
- ``POST .../videos/suggest-coverage``: ``path`` is only read for a file
  the project can reach; a hosted project never reaches one by path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login


@pytest.fixture
def native_match(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> tuple[TestClient, str, str]:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": "Request paths",
            "stages": [{"stage_number": 1, "stage_name": "S1"}],
            "primary_shooter": {"name": "Me"},
        },
    )
    assert resp.status_code == 200, resp.text
    return client, resp.json()["match_id"], resp.json()["default_shooter_slug"]


def test_hosted_bind_never_scaffolds_a_folder(
    native_match: tuple[TestClient, str, str], tmp_path: Path
) -> None:
    client, _mid, _slug = native_match
    target = tmp_path / "scaffold-me"
    resp = client.post("/api/me/recent-projects/bind", json={"path": str(target), "create": True})
    assert resp.status_code == 404, resp.text
    assert not target.exists()


def test_hosted_bind_never_loads_a_folder_by_path(
    native_match: tuple[TestClient, str, str], tmp_path: Path
) -> None:
    from splitsmith import match_model

    client, _mid, _slug = native_match
    folder = tmp_path / "foreign-match"
    match_model.Match.init(folder, name="Foreign")
    (folder / ".env").write_text("SPLITSMITH_BIND_PROBE=loaded\n")
    os.environ.pop("SPLITSMITH_BIND_PROBE", None)
    try:
        resp = client.post("/api/me/recent-projects/bind", json={"path": str(folder)})
        assert resp.status_code == 404, resp.text
        assert "SPLITSMITH_BIND_PROBE" not in os.environ
    finally:
        os.environ.pop("SPLITSMITH_BIND_PROBE", None)


def test_hosted_bind_still_reopens_the_callers_own_match(native_match: tuple[TestClient, str, str]) -> None:
    client, mid, _slug = native_match
    rows = client.get("/api/me/recent-projects").json()["projects"]
    row = next(r for r in rows if r.get("match_id") == mid)
    resp = client.post("/api/me/recent-projects/bind", json={"path": row["path"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["match_id"] == mid


@pytest.mark.parametrize("field", ["intro_path", "outro_path"])
def test_hosted_match_export_refuses_server_side_clip_paths(
    native_match: tuple[TestClient, str, str], tmp_path: Path, field: str
) -> None:
    client, mid, slug = native_match
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    resp = client.post(
        f"/api/matches/{mid}/shooters/{slug}/export/match",
        json={"stage_numbers": [1], field: str(clip)},
    )
    assert resp.status_code == 400, resp.text
    assert field in resp.text


@pytest.mark.integration
def test_hosted_suggest_coverage_does_not_read_a_file_by_path(
    native_match: tuple[TestClient, str, str], tmp_path: Path
) -> None:
    from tests.synthetic_media import build_synthetic_video

    client, mid, slug = native_match
    clip = build_synthetic_video(tmp_path / "clip.mp4")
    resp = client.post(
        f"/api/matches/{mid}/shooters/{slug}/videos/suggest-coverage", json={"path": str(clip)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"covers_stages": [], "span": None}
