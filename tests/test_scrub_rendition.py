"""The Audit screen scrubs the trim's 720p rendition (#1192, #1191).

Local ``kind=web`` serves a *fresh* rendition from disk (non-empty, not
older than the trim), else the trim, else the source. ``kind=trim`` never
substitutes. ``scrub_version`` on the project payload names the same file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import trim as trim_module
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.server import create_app


def _bootstrap(tmp_path: Path) -> tuple[TestClient, str, Path, Path]:
    """A one-stage local match; returns (client, match base URL, trim path, web path).

    Neither the trim nor the rendition exists yet; the source does.
    """
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Scrub Match")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "v.mp4").write_bytes(b"source bytes")
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
    app = create_app(project_root=root, project_name="Scrub Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}", trim, trim_module.web_trim_path(trim)


def _age(path: Path, seconds: int) -> None:
    """Move ``path``'s mtime ``seconds`` into the past."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - seconds * 1_000_000_000))


def _stream(client: TestClient, base: str, kind: str) -> bytes:
    resp = client.get(f"{base}/shooters/me/videos/stream", params={"path": "raw/v.mp4", "kind": kind})
    assert resp.status_code == 200, resp.text
    return resp.content


# --- fresh_web_trim -----------------------------------------------------------


def test_fresh_web_trim_accepts_a_rendition_cut_after_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim.write_bytes(b"trim")
    _age(trim, 10)
    web = trim_module.web_trim_path(trim)
    web.write_bytes(b"web")
    assert audio_helpers.fresh_web_trim(trim) == web


def test_fresh_web_trim_rejects_a_rendition_older_than_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    web = trim_module.web_trim_path(trim)
    web.write_bytes(b"web")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert audio_helpers.fresh_web_trim(trim) is None


def test_fresh_web_trim_rejects_an_empty_rendition(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim.write_bytes(b"trim")
    _age(trim, 10)
    trim_module.web_trim_path(trim).write_bytes(b"")
    assert audio_helpers.fresh_web_trim(trim) is None


def test_fresh_web_trim_needs_the_trim(tmp_path: Path) -> None:
    trim = tmp_path / "stage1_cam_x_trimmed.mp4"
    trim_module.web_trim_path(trim).write_bytes(b"orphan web")
    assert audio_helpers.fresh_web_trim(trim) is None


# --- stream_video, local ------------------------------------------------------


def test_local_web_kind_serves_a_fresh_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    assert _stream(client, base, "web") == b"web bytes"


def test_local_web_kind_serves_the_trim_when_the_rendition_is_stale(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"old window")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert _stream(client, base, "web") == b"re-cut trim"


def test_local_web_kind_serves_the_trim_without_a_rendition(tmp_path: Path) -> None:
    client, base, trim, _web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    assert _stream(client, base, "web") == b"trim bytes"


def test_local_web_kind_ignores_an_orphan_rendition(tmp_path: Path) -> None:
    """No trim: the rendition has nothing to anchor it, so the source plays."""
    client, base, _trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"orphan web")
    assert _stream(client, base, "web") == b"source bytes"


def test_local_trim_kind_never_serves_the_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    assert _stream(client, base, "trim") == b"trim bytes"
    assert _stream(client, base, "auto") == b"trim bytes"


# --- scrub_version on the payload ---------------------------------------------


def _video(client: TestClient, base: str) -> dict:
    resp = client.get(f"{base}/shooters/me/project")
    assert resp.status_code == 200, resp.text
    return resp.json()["stages"][0]["videos"][0]


def test_scrub_version_is_none_without_a_rendition(tmp_path: Path) -> None:
    client, base, trim, _web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    assert _video(client, base)["scrub_version"] is None


def test_scrub_version_names_a_fresh_rendition(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 10)
    web.write_bytes(b"web bytes")
    st = web.stat()
    video = _video(client, base)
    assert video["scrub_version"] == f"{st.st_mtime_ns:x}-{st.st_size:x}"
    assert video["scrub_version"] != video["trim_version"]


def test_scrub_version_is_none_while_the_rendition_is_stale(tmp_path: Path) -> None:
    """Mid re-cut: the new trim is written, its rendition is not yet."""
    client, base, trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"old window")
    _age(web, 10)
    trim.write_bytes(b"re-cut trim")
    assert _video(client, base)["scrub_version"] is None


def test_scrub_version_is_none_for_an_orphan_rendition(tmp_path: Path) -> None:
    client, base, _trim, web = _bootstrap(tmp_path)
    web.write_bytes(b"orphan web")
    assert _video(client, base)["scrub_version"] is None


def test_scrub_version_moves_when_the_rendition_is_recut(tmp_path: Path) -> None:
    client, base, trim, web = _bootstrap(tmp_path)
    trim.write_bytes(b"trim bytes")
    _age(trim, 20)
    web.write_bytes(b"first web")
    _age(web, 10)
    first = _video(client, base)["scrub_version"]
    web.write_bytes(b"other web")
    second = _video(client, base)["scrub_version"]
    assert first is not None and second is not None and first != second


# --- /api/settings/scrub ------------------------------------------------------


def test_scrub_setting_defaults_off_and_round_trips(tmp_path: Path) -> None:
    from splitsmith import user_config

    client, _base, _trim, _web = _bootstrap(tmp_path)
    assert client.get("/api/settings/scrub").json() == {"full_res_scrub": False}
    resp = client.put("/api/settings/scrub", json={"full_res_scrub": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"full_res_scrub": True}
    assert client.get("/api/settings/scrub").json() == {"full_res_scrub": True}
    assert user_config.load_global_prefs().full_res_scrub is True


def test_scrub_setting_rejects_unknown_fields(tmp_path: Path) -> None:
    client, _base, _trim, _web = _bootstrap(tmp_path)
    assert client.put("/api/settings/scrub", json={"full_res_scrub": True, "x": 1}).status_code == 422


def test_scrub_setting_is_local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import server

    client, _base, _trim, _web = _bootstrap(tmp_path)
    monkeypatch.setattr(server, "_hosted_mode_active", lambda: True)
    assert client.get("/api/settings/scrub").status_code == 404
    assert client.put("/api/settings/scrub", json={"full_res_scrub": True}).status_code == 404
