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


def _match_app(tmp_path: Path, scorecards: dict[str, dict[int, int]] = SCORECARDS):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Sort Test")
    match.stages = [match_model.MatchStageDefinition(stage_number=n, stage_name=f"S{n}") for n in (1, 2)]
    match.save(root)
    for slug, by_stage in scorecards.items():
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


def _scan(client, base: str, folder: Path | None) -> dict:  # type: ignore[no-untyped-def]
    body = {"source_dir": str(folder)} if folder is not None else {"unassigned": True}
    resp = client.post(f"{base}/match/footage-sort/scan", json=body)
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


def test_footage_added_to_the_wrong_shooter_is_sorted_and_moved(tmp_path: Path, source_clip: Path) -> None:
    """The real sequence (2026-10-02): alice processed her own head cam,
    then added a club mate's folder with the per-shooter Add footage while
    she was the active shooter, so everything landed in her unassigned list.
    Sorting the parent folder places each clip: hers onto her stage beside
    her head cam, bob's moved to bob. Her own assigned clip stays put."""
    _, client, root, base = _match_app(tmp_path)
    shared = _shared_folder(tmp_path, source_clip)
    _tagged(source_clip, shared / "head" / "VID_20260926_110010_00_001.mp4", T0 + timedelta(seconds=10))
    first = client.post(
        f"{base}/shooters/alice/videos/scan",
        json={"source_dir": str(shared / "head"), "auto_assign_primary": True},
    )
    assert first.json()["auto_assigned"] == {"1": "raw/VID_20260926_110010_00_001.mp4"}
    client.post(
        f"{base}/shooters/alice/videos/scan",
        json={"source_dir": str(shared / "from-carol"), "auto_assign_primary": False},
    )

    view = _scan(client, base, shared)

    by_name = {c["filename"]: c for c in view["clips"]}
    assert by_name["VID_20260926_110010_00_001.mp4"]["imported_by"] == "alice"
    assert not by_name["VID_20260926_110010_00_001.mp4"]["checked"]
    for name, shooter in (("IMG_0001.MOV", "alice"), ("IMG_0002.MOV", "bob")):
        clip = by_name[name]
        assert (clip["unassigned_in"], clip["proposal"]["shooter"], clip["checked"]) == (
            "alice",
            shooter,
            True,
        )

    resp = client.post(f"{base}/match/footage-sort/{view['scan_id']}/import", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["not_imported"] == {"head/VID_20260926_110010_00_001.mp4": "not checked"}
    alice = MatchProject.load(match_model.Match.shooter_root(root, "alice"))
    bob = MatchProject.load(match_model.Match.shooter_root(root, "bob"))
    assert [(Path(v.path).name, v.role) for v in alice.stage(1).videos] == [
        ("VID_20260926_110010_00_001.mp4", "primary"),
        ("IMG_0001.MOV", "secondary"),
    ]
    assert [(Path(v.path).name, v.role) for v in bob.stage(1).videos] == [("IMG_0002.MOV", "primary")]
    assert alice.unassigned_videos == []
    assert (match_model.Match.shooter_root(root, "bob") / "raw" / "IMG_0002.MOV").resolve() == (
        shared / "from-carol" / "IMG_0002.MOV"
    ).resolve()


def test_per_shooter_import_leaves_a_squad_mates_run_unassigned(tmp_path: Path, source_clip: Path) -> None:
    """Bob shoots right before alice. A clip of bob's run starts inside
    alice's scorecard window, so the per-shooter import as alice used to
    make it her primary (Anton's glasses on Mathias's stages, Höstfinalen
    2026). With both shooters' scorecards it stays unassigned; alice's own
    clip, on her next stage, is still placed."""
    _, client, root, base = _match_app(
        tmp_path, scorecards={"alice": {1: 600, 2: 2000}, "bob": {1: 300, 2: 1700}}
    )
    shared = tmp_path / "shared"
    _tagged(source_clip, shared / "from-carol" / "IMG_0001.MOV", T0 + timedelta(seconds=200))
    _tagged(source_clip, shared / "head" / "VID_20260926_113150_00_001.mp4", T0 + timedelta(seconds=1910))

    bobs = client.post(
        f"{base}/shooters/alice/videos/scan",
        json={"source_dir": str(shared / "from-carol"), "auto_assign_primary": True},
    ).json()
    hers = client.post(
        f"{base}/shooters/alice/videos/scan",
        json={"source_dir": str(shared / "head"), "auto_assign_primary": True},
    ).json()

    assert bobs["auto_assigned"] == {}
    assert hers["auto_assigned"] == {"2": "raw/VID_20260926_113150_00_001.mp4"}
    alice = MatchProject.load(match_model.Match.shooter_root(root, "alice"))
    assert [Path(v.path).name for v in alice.unassigned_videos] == ["IMG_0001.MOV"]


def test_path_keys_compare_umlauts_composed() -> None:
    """Höstfinalen 2026: the per-shooter import got the folder path typed
    (composed "ö"), the sort listed it from disk (decomposed on macOS's
    APFS, where both spellings open the same folder). The lookup key must
    not depend on the spelling. Pure: on Linux the two spellings would be
    two folders, so this checks the key, not a filesystem."""
    import unicodedata

    from splitsmith.ui.footage_sort_api import _resolved

    composed = unicodedata.normalize("NFC", "/nonexistent/Höstfinalen XI - anton/IMG_5262.MOV")
    decomposed = unicodedata.normalize("NFD", composed)

    assert composed != decomposed
    assert _resolved(composed) == _resolved(decomposed)


def test_sort_across_shooters_reads_every_unassigned_clip_without_a_folder(
    tmp_path: Path, source_clip: Path
) -> None:
    """The Footage page's "Sort across shooters" sorts exactly the videos
    unassigned in any shooter's project, wherever they came from, with no
    folder to pick (2026-10-02: a second picker sent the user back into the
    per-shooter import), and import places them."""
    _, client, root, base = _match_app(tmp_path)
    shared = _shared_folder(tmp_path, source_clip)
    _tagged(source_clip, shared / "elsewhere" / "IMG_0099.MOV", T0 + timedelta(hours=3))
    client.post(
        f"{base}/shooters/alice/videos/scan",
        json={
            "source_paths": [str(shared / "from-carol" / n) for n in ("IMG_0001.MOV", "IMG_0002.MOV")],
            "auto_assign_primary": False,
        },
    )

    view = _scan(client, base, None)

    assert view["status"] == "ready"
    assert sorted(c["filename"] for c in view["clips"]) == ["IMG_0001.MOV", "IMG_0002.MOV"]
    assert {c["proposal"]["shooter"] for c in view["clips"]} == {"alice", "bob"}
    client.post(f"{base}/match/footage-sort/{view['scan_id']}/import", json={})
    bob = MatchProject.load(match_model.Match.shooter_root(root, "bob"))
    assert [Path(v.path).name for v in bob.stage(1).videos] == ["IMG_0002.MOV"]


def test_sort_across_shooters_with_nothing_unassigned_says_so(tmp_path: Path, source_clip: Path) -> None:
    _, client, _, base = _match_app(tmp_path)

    resp = client.post(f"{base}/match/footage-sort/scan", json={"unassigned": True})

    assert resp.status_code == 409


def test_scrub_strips_follow_the_scan(tmp_path: Path, source_clip: Path) -> None:
    """The review opens as soon as the clips are read; the hover-scrub
    strips are built after, and the view reports them as they land."""
    from PIL import Image

    _, client, _, base = _match_app(tmp_path)
    view = _scan(client, base, _shared_folder(tmp_path, source_clip))

    deadline = time.monotonic() + 60
    while view["strips_pending"] and time.monotonic() < deadline:
        time.sleep(0.2)
        view = client.get(f"{base}/match/footage-sort/{view['scan_id']}").json()

    assert view["strips_pending"] == 0
    assert all(c["strip"] for c in view["clips"])
    strip = client.get(f"{base}/match/footage-sort/{view['scan_id']}/thumbs/0/strip.jpg")
    assert strip.status_code == 200
    out = tmp_path / "strip.jpg"
    out.write_bytes(strip.content)
    with Image.open(out) as img:
        assert img.size == (1600, 90)


def test_import_one_shooter_and_keep_reviewing(tmp_path: Path, source_clip: Path) -> None:
    """The per-shooter Import: only that shooter's checked clips go in, the
    review stays open with the rest, and each batch keeps its report."""
    _, client, root, base = _match_app(tmp_path)
    view = _scan(client, base, _shared_folder(tmp_path, source_clip))

    first = client.post(f"{base}/match/footage-sort/{view['scan_id']}/import", json={"shooters": ["bob"]})

    assert first.status_code == 200, first.text
    assert [(i["shooter"], i["stage"]) for i in first.json()["imported"]] == [("bob", 1)]
    alice_clip = next(c["clip_id"] for c in view["clips"] if c["filename"] == "IMG_0001.MOV")
    assert first.json()["not_imported"][alice_clip] == "another shooter's import"
    after = client.get(f"{base}/match/footage-sort/{view['scan_id']}").json()
    assert after["status"] == "ready"
    by_name = {c["filename"]: c for c in after["clips"]}
    assert by_name["IMG_0002.MOV"]["imported_by"] == "bob"
    assert by_name["IMG_0001.MOV"]["checked"] is True
    alice = MatchProject.load(match_model.Match.shooter_root(root, "alice"))
    assert alice.stage(1).videos == []

    second = client.post(f"{base}/match/footage-sort/{view['scan_id']}/import", json={})

    assert [(i["shooter"], i["stage"]) for i in second.json()["imported"]] == [("alice", 1)]
    assert Path(first.json()["report"]).name.endswith("-report.json")
    assert Path(second.json()["report"]).name.endswith("-report-2.json")
    assert client.get(f"{base}/match/footage-sort/{view['scan_id']}").json()["status"] == "imported"
