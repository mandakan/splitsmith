"""A multi-stage single take streams each stage's own trim (#1211).

One source registered on N stages (take spec 2026-07-03) has one
``StageVideo`` -- and one trim -- per stage. The stream routes used to
resolve the path to the first stage holding it, so every stage's Audit
played stage 1's footage under its own markers. ``stage`` on the query
picks the registration.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith import trim as trim_module
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.server import create_app


def _bootstrap(tmp_path: Path) -> tuple[TestClient, str, dict[int, Path]]:
    """Stages 1 and 2 share ``raw/take.mp4``; each has its own trim.

    Returns (client, match base URL, {stage_number: trim path}).
    """
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Take Match")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "take.mp4").write_bytes(b"source bytes")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=20.0,
            videos=[StageVideo(path=Path("raw/take.mp4"), role="primary", beep_time=10.0 + 100 * n)],
        )
        for n in (1, 2)
    ]
    project.save(shooter_root)
    stamped = MatchProject.load(shooter_root)
    trims: dict[int, Path] = {}
    for stage in stamped.stages:
        trim = audio_helpers.trimmed_video_path(
            shooter_root, stage.stage_number, stage.videos[0], project=stamped
        )
        trim.parent.mkdir(parents=True, exist_ok=True)
        trim.write_bytes(f"stage{stage.stage_number} trim".encode())
        trims[stage.stage_number] = trim
    app = create_app(project_root=root, project_name="Take Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}", trims


def _get(client: TestClient, base: str, **params: object):
    return client.get(f"{base}/shooters/me/videos/stream", params={"path": "raw/take.mp4", **params})


def test_each_stage_streams_its_own_trim(tmp_path: Path) -> None:
    client, base, _trims = _bootstrap(tmp_path)
    assert _get(client, base, kind="trim", stage=1).content == b"stage1 trim"
    assert _get(client, base, kind="trim", stage=2).content == b"stage2 trim"
    assert _get(client, base, kind="auto", stage=2).content == b"stage2 trim"


def test_each_stage_streams_its_own_rendition(tmp_path: Path) -> None:
    client, base, trims = _bootstrap(tmp_path)
    for n, trim in trims.items():
        st = trim.stat()
        os.utime(trim, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
        trim_module.web_trim_path(trim).write_bytes(f"stage{n} web".encode())
    assert _get(client, base, kind="web", stage=2).content == b"stage2 web"
    assert _get(client, base, kind="web", stage=1).content == b"stage1 web"


def test_without_a_stage_the_first_registration_still_serves(tmp_path: Path) -> None:
    """Old clients send no stage; they keep today's answer."""
    client, base, _trims = _bootstrap(tmp_path)
    assert _get(client, base, kind="trim").content == b"stage1 trim"


def test_a_stage_that_does_not_hold_the_path_is_a_404(tmp_path: Path) -> None:
    client, base, _trims = _bootstrap(tmp_path)
    resp = _get(client, base, kind="trim", stage=3)
    assert resp.status_code == 404, resp.text


def test_find_video_by_stage(tmp_path: Path) -> None:
    from tests.conftest import scaffold_match

    _root, shooter_root = scaffold_match(tmp_path, name="Find Match")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=20.0,
            videos=[StageVideo(path=Path("raw/take.mp4"), role="primary")],
        )
        for n in (1, 2)
    ]
    project.save(shooter_root)
    stamped = MatchProject.load(shooter_root)
    stage, video = stamped.find_video(Path("raw/take.mp4"), stage_number=2)
    assert stage is not None and stage.stage_number == 2
    assert video.video_id == stamped.stages[1].videos[0].video_id
    assert stamped.find_video(Path("raw/take.mp4"), stage_number=3) is None
    first = stamped.find_video(Path("raw/take.mp4"))
    assert first is not None and first[0] is not None and first[0].stage_number == 1
