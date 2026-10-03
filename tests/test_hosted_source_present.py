"""A confined project answers "is the source here?" the way it resolves it.

``MatchProject.source_present`` is the read-only existence check the
queueing routes use before submitting a job (detect-beep, trim, export).
Once a project is confined (loaded from the hosted state store), an
absolute or ``..`` path is never resolved to a file on the container, so
it is never *present* either: the routes answer the structured 424
``source_unreachable`` instead of queueing work against a path that
happens to exist on the server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject
from tests.hosted_helpers import _CapturingSender, login

EMAIL = "owner@example.com"


def test_confined_source_present_refuses_out_of_tree_paths(tmp_path: Path) -> None:
    root = tmp_path / "shooter"
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "a.mp4").write_bytes(b"x")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    escaping = Path("../outside.mp4")
    assert (root / escaping).exists()

    project = MatchProject(name="p")
    # Local mode: the operator's own absolute and relative paths stand.
    assert project.source_present(root, outside)
    assert project.source_present(root, escaping)

    project.confine_paths()
    assert not project.source_present(root, outside)
    assert not project.source_present(root, escaping)
    assert not project.source_present(root, escaping, durable=True)
    assert project.source_present(root, Path("raw/a.mp4"))


def _point_primary_at(db_url: str, match_id: str, slug: str, source: Path) -> None:
    from splitsmith.match_project import StageVideo

    sf = sessionmaker(create_engine(db_url))

    async def _edit() -> None:
        async with sf() as s:
            uid = (await s.execute(_select(User).where(User.email == EMAIL))).scalar_one().id
        store = ProjectStateStore(sf, user_id=uid)
        doc, version = await store.load_project(match_id, slug)
        project = MatchProject.model_validate(doc)
        project.stages[0].videos = [StageVideo(path=source, role="primary")]
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)

    asyncio.run(_edit())


@pytest.mark.parametrize("route", ["stages/1/detect-beep", "stages/1/videos/{video_id}/detect-beep"])
def test_hosted_detect_beep_reports_an_out_of_tree_source_unreachable(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], tmp_path: Path, route: str
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": "Source present",
            "stages": [{"stage_number": 1, "stage_name": "S1"}],
            "primary_shooter": {"name": "Me"},
        },
    )
    assert resp.status_code == 200, resp.text
    mid, slug = resp.json()["match_id"], resp.json()["default_shooter_slug"]
    outside = tmp_path / "server-file.mp4"
    outside.write_bytes(b"\x00" * 64)
    _point_primary_at(hosted_env, mid, slug, outside)

    project = client.get(f"/api/matches/{mid}/shooters/{slug}/project").json()
    video_id = project["stages"][0]["videos"][0]["video_id"]
    resp = client.post(f"/api/matches/{mid}/shooters/{slug}/" + route.format(video_id=video_id))
    assert resp.status_code == 424, resp.text
    assert resp.json()["detail"]["code"] == "source_unreachable"
