"""API tests for the footage sort (``ui/footage_sort_api.py``, spec 2026-10-01).

A two-shooter match and a shared folder of real (synthetic) MP4s whose
``creation_time`` places them on each shooter's run. ffmpeg builds the
media, so these are integration tests.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 26, 11, 0, tzinfo=UTC)
# alice scores stage 1 100 s after T0, bob 400 s; stage 2 twenty minutes on.
SCORECARDS = {"alice": {1: 100, 2: 1300}, "bob": {1: 400, 2: 1600}}


@pytest.fixture(autouse=True)
def _no_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return build_synthetic_video(tmp_path_factory.mktemp("media") / "source.mp4")


def _tagged(source: Path, dest: Path, recorded: datetime) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-metadata",
            f"creation_time={recorded.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
            str(dest),
        ],
        check=True,
    )
    return dest


def _match_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Sort Test")
    match.stages = [match_model.MatchStageDefinition(stage_number=n, stage_name=f"S{n}") for n in (1, 2)]
    match.save(root)
    for slug, by_stage in SCORECARDS.items():
        match.add_shooter(root, match_model.Shooter(slug=slug, name=slug.title()))
        sroot = match_model.Match.shooter_root(root, slug)
        project = MatchProject.init(sroot, name="Sort Test")
        project.stages = [
            StageEntry(
                stage_number=n,
                stage_name=f"S{n}",
                time_seconds=20.0,
                scorecard_updated_at=T0 + timedelta(seconds=s),
            )
            for n, s in by_stage.items()
        ]
        project.save(sroot)
    app = create_app()
    client = TestClient(app)
    assert client.post("/api/me/recent-projects/bind", json={"path": str(root.resolve())}).status_code == 200
    (mid,) = app.state.splitsmith_state.matches.known_ids()
    return app, client, root, f"/api/matches/{mid}"


def _scan(client, base: str, folder: Path) -> dict:  # type: ignore[no-untyped-def]
    resp = client.post(f"{base}/match/footage-sort/scan", json={"source_dir": str(folder)})
    assert resp.status_code == 200, resp.text
    scan_id = resp.json()["scan_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        view = client.get(f"{base}/match/footage-sort/{scan_id}").json()
        if view["status"] != "scanning":
            return view
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def _shared_folder(tmp_path: Path, source: Path) -> Path:
    """Carol's phone filmed alice then bob on stage 1, plus a photo."""
    shared = tmp_path / "shared"
    _tagged(source, shared / "from-carol" / "IMG_0001.MOV", T0)
    _tagged(source, shared / "from-carol" / "IMG_0002.MOV", T0 + timedelta(seconds=300))
    (shared / "from-carol" / "IMG_0003.HEIC").write_bytes(b"photo")
    return shared


def test_scan_proposes_each_clip_and_import_assigns_it(tmp_path: Path, source_clip: Path) -> None:
    _, client, root, base = _match_app(tmp_path)
    shared = _shared_folder(tmp_path, source_clip)

    view = _scan(client, base, shared)

    assert view["status"] == "ready"
    assert view["skipped_files"] == 1
    proposed = {
        c["filename"]: (c["proposal"]["shooter"], c["proposal"]["stage"], c["checked"]) for c in view["clips"]
    }
    assert proposed == {"IMG_0001.MOV": ("alice", 1, True), "IMG_0002.MOV": ("bob", 1, True)}
    assert all(c["thumbnail"] for c in view["clips"])
    thumb = client.get(f"{base}/match/footage-sort/{view['scan_id']}/thumbs/0.jpg")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

    played = client.get(
        f"{base}/match/footage-sort/{view['scan_id']}/clips/0/video", headers={"Range": "bytes=0-99"}
    )
    assert played.status_code == 206 and len(played.content) == 100

    imported = client.post(
        f"{base}/match/footage-sort/{view['scan_id']}/import", json={"link_mode": "symlink"}
    )

    assert imported.status_code == 200, imported.text
    assert {(i["shooter"], i["stage"], i["role"]) for i in imported.json()["imported"]} == {
        ("alice", 1, "primary"),
        ("bob", 1, "primary"),
    }
    for slug, name in (("alice", "IMG_0001.MOV"), ("bob", "IMG_0002.MOV")):
        project = MatchProject.load(match_model.Match.shooter_root(root, slug))
        assert [Path(v.path).name for v in project.stage(1).videos] == [name]
    assert Path(imported.json()["report"]).exists()


def test_user_decisions_change_what_is_imported(tmp_path: Path, source_clip: Path) -> None:
    _, client, root, base = _match_app(tmp_path)
    view = _scan(client, base, _shared_folder(tmp_path, source_clip))
    second = next(c["clip_id"] for c in view["clips"] if c["filename"] == "IMG_0002.MOV")

    decided = client.put(
        f"{base}/match/footage-sort/{view['scan_id']}/decisions",
        json={"overrides": [{"clip_id": second, "shooter": "alice", "stage": 2}], "checked": {}},
    ).json()

    clip = next(c for c in decided["clips"] if c["clip_id"] == second)
    assert (clip["proposal"]["shooter"], clip["proposal"]["stage"], clip["proposal"]["decided_by"]) == (
        "alice",
        2,
        "user",
    )
    resp = client.post(f"{base}/match/footage-sort/{view['scan_id']}/import", json={})
    assert {(i["shooter"], i["stage"]) for i in resp.json()["imported"]} == {("alice", 1), ("alice", 2)}
    bob = MatchProject.load(match_model.Match.shooter_root(root, "bob"))
    assert bob.stage(1).videos == []


def test_a_rescan_does_not_import_a_clip_twice(tmp_path: Path, source_clip: Path) -> None:
    _, client, _, base = _match_app(tmp_path)
    shared = _shared_folder(tmp_path, source_clip)
    first = _scan(client, base, shared)
    client.post(f"{base}/match/footage-sort/{first['scan_id']}/import", json={})

    again = _scan(client, base, shared)

    assert {c["imported_by"] for c in again["clips"]} == {"alice", "bob"}
    assert not any(c["checked"] for c in again["clips"])
    resp = client.post(f"{base}/match/footage-sort/{again['scan_id']}/import", json={})
    assert resp.json()["imported"] == []


def test_hosted_mode_has_no_footage_sort(
    tmp_path: Path, source_clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.ui import server

    _, client, _, base = _match_app(tmp_path)
    monkeypatch.setattr(server, "_hosted_mode_active", lambda: True)

    resp = client.post(f"{base}/match/footage-sort/scan", json={"source_dir": str(tmp_path)})

    assert resp.status_code == 404


def test_an_anchor_prechecks_the_rest_of_its_camera(tmp_path: Path, source_clip: Path) -> None:
    """A head cam 10 minutes fast: needs an anchor; naming one clip places
    and pre-checks the other."""
    _, client, _, base = _match_app(tmp_path)
    shared = tmp_path / "shared"
    fast = timedelta(minutes=10)
    _tagged(source_clip, shared / "head" / "VID_20260926_110000_00_001.mp4", T0 + fast)
    _tagged(
        source_clip, shared / "head" / "VID_20260926_113000_00_002.mp4", T0 + timedelta(seconds=1200) + fast
    )
    view = _scan(client, base, shared)
    assert {c["proposal"]["confidence"] for c in view["clips"]} == {"needs_you"}
    first, second = sorted(c["clip_id"] for c in view["clips"])

    decided = client.put(
        f"{base}/match/footage-sort/{view['scan_id']}/decisions",
        json={"anchors": [{"clip_id": first, "shooter": "alice", "stage": 1}], "checked": {first: True}},
    ).json()

    rest = next(c for c in decided["clips"] if c["clip_id"] == second)
    assert (rest["proposal"]["shooter"], rest["proposal"]["stage"], rest["proposal"]["confidence"]) == (
        "alice",
        2,
        "medium",
    )
    assert rest["checked"] is True
