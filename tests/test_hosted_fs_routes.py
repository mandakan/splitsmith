"""The folder-picker filesystem routes are local mode only.

``/api/shooters/{slug}/fs/list``, ``/api/fs/list-dirs``,
``/api/shooters/{slug}/fs/probe`` and ``/api/shooters/{slug}/thumbnails/...``
browse and probe the machine the server runs on. Every SPA caller is a
local-mode surface (the FolderPicker in Add footage, the footage sort, the
create-match parent picker and the relink dialog); hosted mode adds footage
through the upload modal instead. So hosted answers all four with the same
404 as an unknown route, whatever the path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login
from tests.mirror_helpers import seed_mirror, sync_docs_url

MID = "m-fs-routes"
SLUG = "alice"


@pytest.fixture
def signed_in_with_shooter(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> TestClient:
    from splitsmith import match_model
    from splitsmith.match_project import MatchProject

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_mirror(client, MID, "fs routes")
    doc = match_model.Match(match_id=MID, name="fs routes", shooters=[SLUG], stages=[]).model_dump(
        mode="json"
    )
    resp = client.put(sync_docs_url(MID, "match"), params={"expected_version": 1}, json=doc)
    assert resp.status_code == 200, resp.text
    resp = client.put(
        f"/api/sync/matches/{MID}/docs/project/{SLUG}",
        params={"expected_version": 0},
        json=MatchProject(name="Alice").model_dump(mode="json"),
    )
    assert resp.status_code == 200, resp.text
    return client


def _outside_dir(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("x\n")
    (outside / "clip.mp4").write_bytes(b"\x00" * 64)
    (outside / "sub").mkdir()
    return outside


def test_hosted_fs_list_is_not_served(signed_in_with_shooter: TestClient, tmp_path: Path) -> None:
    client = signed_in_with_shooter
    outside = _outside_dir(tmp_path)
    base = f"/api/matches/{MID}/shooters/{SLUG}"
    assert client.get(f"{base}/fs/list", params={"path": str(outside)}).status_code == 404
    assert client.get(f"{base}/fs/list").status_code == 404


def test_hosted_fs_list_dirs_is_not_served(signed_in_with_shooter: TestClient, tmp_path: Path) -> None:
    client = signed_in_with_shooter
    outside = _outside_dir(tmp_path)
    assert client.get("/api/fs/list-dirs", params={"path": str(outside)}).status_code == 404
    assert client.get("/api/fs/list-dirs").status_code == 404


def test_hosted_fs_probe_is_not_served(signed_in_with_shooter: TestClient, tmp_path: Path) -> None:
    client = signed_in_with_shooter
    outside = _outside_dir(tmp_path)
    base = f"/api/matches/{MID}/shooters/{SLUG}"
    # A missing path and a non-video file answer the same as a real clip,
    # so the route says nothing about what exists on the container.
    for path in (outside / "clip.mp4", outside / "notes.txt", outside / "missing.mp4"):
        resp = client.get(f"{base}/fs/probe", params={"path": str(path)})
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json() == {"detail": "not found"}


def test_hosted_thumbnail_route_is_not_served(signed_in_with_shooter: TestClient) -> None:
    client = signed_in_with_shooter
    # Plant a cached thumbnail exactly where the route would read it, so
    # the 404 comes from the mode gate and not from a cache miss.
    thumbs = Path(os.environ["SPLITSMITH_PROJECTS_DIR"]) / MID / "shooters" / SLUG / "thumbs"
    thumbs.mkdir(parents=True)
    key = "c72e2952421c98ff"
    (thumbs / f"{key}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/thumbnails/{key}.jpg")
    assert resp.status_code == 404
