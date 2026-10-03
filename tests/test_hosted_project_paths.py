"""Hosted mode resolves a project's paths only inside the match's own tree.

A project doc can reach the hosted API process from the desktop sync
(``PUT /api/sync/.../docs/project/{slug}``), and a desktop mirror's docs
legitimately carry the desktop's absolute paths (``/Users/x/footage/a.mp4``,
a custom ``exports_dir``). The docs are accepted as they are -- refusing
them would break desktop sync -- but a project loaded from the state
store never *resolves* an absolute or ``..`` path to a file on the
container: a video path like that reads as an unreachable source, and a
cache-dir override like that falls back to the default folder under the
shooter's root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.hosted_helpers import _CapturingSender, login
from tests.mirror_helpers import seed_mirror, sync_docs_url

MID = "m-project-paths"
SLUG = "alice"
SECRET = b"not-a-video-but-private\n" * 8


def _push_project(client: TestClient, project: MatchProject) -> None:
    seed_mirror(client, MID, "paths")
    roster = match_model.Match(match_id=MID, name="paths", shooters=[SLUG], stages=[]).model_dump(mode="json")
    resp = client.put(sync_docs_url(MID, "match"), params={"expected_version": 1}, json=roster)
    assert resp.status_code == 200, resp.text
    resp = client.put(
        sync_docs_url(MID, f"project/{SLUG}"),
        params={"expected_version": 0},
        json=project.model_dump(mode="json"),
    )
    # The desktop's own absolute paths are accepted at sync time.
    assert resp.status_code == 200, resp.text


@pytest.fixture
def signed_in(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> TestClient:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    return client


@pytest.mark.parametrize("kind", ["source", "auto"])
def test_an_absolute_video_path_in_a_synced_doc_is_not_served(
    signed_in: TestClient, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "clip.mp4"
    secret.write_bytes(SECRET)
    stage = StageEntry(
        stage_number=1,
        stage_name="S1",
        time_seconds=10.0,
        videos=[StageVideo(path=secret, role="primary")],
    )
    _push_project(signed_in, MatchProject(name="paths", stages=[stage]))

    for route in (
        f"/api/matches/{MID}/shooters/{SLUG}/videos/stream",
        f"/api/matches/{MID}/match/shooters/{SLUG}/videos/stream",
    ):
        resp = signed_in.get(route, params={"path": str(secret), "kind": kind})
        assert resp.status_code != 200, (route, resp.status_code)
        assert SECRET not in resp.content


def test_a_parent_relative_video_path_is_not_served(signed_in: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "clip.mp4"
    secret.write_bytes(SECRET)
    # Enough parents to reach / from any shooter root, then down to the file.
    escaping = Path("../" * 40 + str(secret).lstrip("/"))
    stage = StageEntry(
        stage_number=1,
        stage_name="S1",
        time_seconds=10.0,
        videos=[StageVideo(path=escaping, role="primary")],
    )
    _push_project(signed_in, MatchProject(name="paths", stages=[stage]))

    resp = signed_in.get(
        f"/api/matches/{MID}/shooters/{SLUG}/videos/stream", params={"path": str(escaping), "kind": "source"}
    )
    assert resp.status_code != 200
    assert SECRET not in resp.content


def test_an_absolute_exports_dir_in_a_synced_doc_is_not_read(signed_in: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "outside-exports"
    outside.mkdir()
    (outside / "stage1.csv").write_bytes(SECRET)
    _push_project(signed_in, MatchProject(name="paths", exports_dir=str(outside)))

    resp = signed_in.get(f"/api/matches/{MID}/shooters/{SLUG}/exports/file/stage1.csv")
    assert resp.status_code == 404, resp.text
    assert SECRET not in resp.content


def test_confined_project_keeps_relative_paths_and_local_mode_keeps_absolute(tmp_path: Path) -> None:
    root = tmp_path / "shooter"
    outside = tmp_path / "elsewhere"
    project = MatchProject(name="p", exports_dir=str(outside), raw_dir="raw-custom")

    # Local mode: the operator's own overrides and absolute sources stand.
    assert project.exports_path(root) == outside
    assert project.resolve_video_path(root, outside / "a.mp4") == outside / "a.mp4"

    project.confine_paths()
    assert project.exports_path(root) == root / "exports"
    assert project.raw_path(root) == root / "raw-custom"
    assert project.resolve_video_path(root, Path("raw/a.mp4")) == root / "raw/a.mp4"
    unreachable = project.resolve_video_path(root, outside / "a.mp4")
    assert unreachable.resolve().is_relative_to(root.resolve())
    assert not unreachable.exists()
    escaping = project.resolve_video_path(root, Path("../elsewhere/a.mp4"))
    assert escaping.resolve().is_relative_to(root.resolve())
