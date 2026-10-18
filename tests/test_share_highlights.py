"""The highlight reel on a share link (part 2 plan, task 6): the share
surface admits the reel's MP4 and the route naming the newest one, and
nothing else under exports."""

from __future__ import annotations

import pytest

from splitsmith.export_naming import REEL_FILE_RE, reel_file_name
from splitsmith.ui.server import _SHARE_PATH_RE

from .test_highlights_api import _fake_make_reel, _ready_match, _run_job


@pytest.mark.parametrize(
    "path",
    [
        "shooters/me/highlights/latest",
        "shooters/me/exports/file/little-trouble-in-big-s-tra-2026-highlights.mp4",
        "shooters/me/exports/file/bromma-highlights-short.mp4",
    ],
)
def test_the_share_surface_admits_the_reel(path):
    assert _SHARE_PATH_RE.fullmatch(path)


@pytest.mark.parametrize(
    "path",
    [
        "shooters/me/exports/file/bromma-match.mp4",
        "shooters/me/exports/file/bromma-highlights-youtube.json",
        "shooters/me/exports/file/bromma-highlights.fcpxml",
        "shooters/me/exports/file/stage1_a_trimmed.mp4",
        "shooters/me/exports/file/..%2fproject.json",
        "shooters/me/exports/file/../bromma-highlights.mp4",
        "shooters/me/exports/file/Bromma-highlights.mp4",
        "shooters/me/highlights",
        "shooters/me/highlights/latest/x",
    ],
)
def test_the_share_surface_admits_nothing_else_under_exports(path):
    assert not _SHARE_PATH_RE.fullmatch(path)


def test_the_reel_name_is_the_shape_the_share_admits():
    for name in (reel_file_name("Little Trouble in Big Sätra 2026"), reel_file_name("Bromma", short=True)):
        assert REEL_FILE_RE.fullmatch(name)
        assert _SHARE_PATH_RE.fullmatch(f"shooters/me/exports/file/{name}")


def test_latest_names_the_newest_reel_or_404s(tmp_path, monkeypatch):
    client, _root = _ready_match(tmp_path)
    assert client.get("/api/shooters/me/highlights/latest").status_code == 404
    monkeypatch.setattr("splitsmith.ui.highlights_api.make_reel", _fake_make_reel([]))
    _run_job(client, {"moments": ["fastest_draw"]})
    _run_job(client, {"moments": ["fastest_draw"], "short": True, "upright": True})
    latest = client.get("/api/shooters/me/highlights/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["filename"].endswith("-highlights-short.mp4")
    assert body["short"] is True
