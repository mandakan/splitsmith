"""``trim_version`` on the project payload's stage videos.

The Audit page pins ``kind=trim`` and puts this version in the URL. A
re-cut deletes the trim before encoding the new one, so a player that
seeks during the encode errors on a 404; without a new URL afterwards it
stayed in error (shown as endless "Buffering...") until a full reload.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.server import create_app


def _bootstrap(tmp_path: Path) -> tuple[TestClient, str, Path]:
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Trim Version Match")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="S1",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=8.0)],
        )
    ]
    project.save(shooter_root)
    stamped = MatchProject.load(shooter_root)
    trim = audio_helpers.trimmed_video_path(shooter_root, 1, stamped.stages[0].videos[0], project=stamped)
    trim.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(project_root=root, project_name="Trim Version Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}", trim


def _version(client: TestClient, base: str) -> str | None:
    resp = client.get(f"{base}/shooters/me/project")
    assert resp.status_code == 200, resp.text
    return resp.json()["stages"][0]["videos"][0]["trim_version"]


def test_trim_version_is_none_without_a_trim(tmp_path: Path) -> None:
    client, base, _trim = _bootstrap(tmp_path)
    assert _version(client, base) is None


def test_trim_version_moves_when_the_trim_is_recut(tmp_path: Path) -> None:
    client, base, trim = _bootstrap(tmp_path)
    trim.write_bytes(b"first cut")
    first = _version(client, base)
    assert first is not None
    assert _version(client, base) == first  # stable while the file is unchanged

    # A re-cut: deleted, then a new file with the same size, later mtime.
    trim.unlink()
    assert _version(client, base) is None
    trim.write_bytes(b"other cut")
    st = trim.stat()
    os.utime(trim, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    second = _version(client, base)
    assert second is not None
    assert second != first


def test_versioned_trim_url_streams_the_trim(tmp_path: Path) -> None:
    """The extra ``v`` query parameter is ignored by the route."""
    client, base, trim = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    version = _version(client, base)
    resp = client.get(
        f"{base}/shooters/me/videos/stream",
        params={"path": "raw/v.mp4", "kind": "trim", "v": version},
        headers={"Range": "bytes=0-3"},
    )
    assert resp.status_code == 206, resp.text
    assert resp.content == b"trim"
