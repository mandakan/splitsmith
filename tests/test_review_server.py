"""Tests for the review SPA backend (stdlib HTTP server)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from splitsmith.review_server import ReviewConfig, make_server


@pytest.fixture
def serve(tmp_path: Path):
    """Yield a (base_url, fixture_path, audio_path) tuple for an ephemeral server."""
    fixture = tmp_path / "f.json"
    audio = tmp_path / "f.wav"
    fixture.write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "test",
                "beep_time": 0.5,
                "stage_time_seconds": 10.0,
                "fixture_window_in_source": [12.0, 30.0],
                "shots": [],
                "_candidates_pending_audit": {
                    "candidates": [{"candidate_number": 1, "time": 1.0, "ms_after_beep": 500}]
                },
            },
            indent=2,
        )
        + "\n"
    )
    audio.write_bytes(b"RIFF" + b"\x00" * 100)  # minimal pseudo-wav

    config = ReviewConfig(
        fixture_path=fixture,
        audio_path=audio,
        video_path=None,
        video_offset_seconds=12.0,
    )
    server = make_server("127.0.0.1", 0, config)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Tiny wait for the server to actually accept connections.
    time.sleep(0.05)
    try:
        yield base, fixture, audio
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def _get(url: str) -> tuple[int, bytes, dict]:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 -- localhost test
        return resp.status, resp.read(), dict(resp.headers)


def _put(url: str, body: bytes, content_type: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return resp.status, resp.read()


def test_get_meta_returns_video_offset(serve) -> None:
    base, _, _ = serve
    status, body, _ = _get(base + "/api/meta")
    assert status == 200
    payload = json.loads(body)
    assert payload["has_video"] is False
    assert payload["video_offset_seconds"] == 12.0


def test_get_fixture_returns_json(serve) -> None:
    base, fixture, _ = serve
    status, body, headers = _get(base + "/api/fixture")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    parsed = json.loads(body)
    assert parsed["stage_number"] == 1
    assert parsed["beep_time"] == 0.5


def test_put_fixture_writes_atomically_and_creates_backup(serve) -> None:
    base, fixture, _ = serve
    new_payload = json.loads(fixture.read_text())
    new_payload["shots"] = [
        {"shot_number": 1, "candidate_number": 1, "time": 1.0, "source": "detected"}
    ]
    status, body = _put(base + "/api/fixture", json.dumps(new_payload).encode("utf-8"))
    assert status == 200
    assert json.loads(body) == {"ok": True}

    saved = json.loads(fixture.read_text())
    assert saved["shots"] == new_payload["shots"]
    bak = fixture.with_suffix(fixture.suffix + ".bak")
    assert bak.exists(), "backup of previous fixture should be created"


def test_put_fixture_rejects_invalid_json(serve) -> None:
    base, _, _ = serve
    with pytest.raises(HTTPError) as info:
        _put(base + "/api/fixture", b"this is not json")
    assert info.value.code == 400


def test_put_fixture_rejects_non_object_root(serve) -> None:
    base, _, _ = serve
    with pytest.raises(HTTPError) as info:
        _put(base + "/api/fixture", b"[1, 2, 3]")
    assert info.value.code == 400


def test_get_audio_serves_file(serve) -> None:
    base, _, audio = serve
    status, body, _ = _get(base + "/api/audio")
    assert status == 200
    assert body == audio.read_bytes()


def test_get_audio_supports_range(serve) -> None:
    base, _, audio = serve
    full = audio.read_bytes()
    req = urllib.request.Request(base + "/api/audio")
    req.add_header("Range", "bytes=0-9")
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        assert resp.status == 206
        assert resp.headers["Content-Range"] == f"bytes 0-9/{len(full)}"
        assert resp.read() == full[0:10]


def test_get_video_404s_when_not_configured(serve) -> None:
    base, _, _ = serve
    with pytest.raises(HTTPError) as info:
        _get(base + "/api/video")
    assert info.value.code == 404


def test_static_index_html_is_served(serve) -> None:
    base, _, _ = serve
    status, body, headers = _get(base + "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"splitsmith review" in body


def test_static_path_traversal_is_blocked(serve) -> None:
    base, _, _ = serve
    with pytest.raises(HTTPError) as info:
        _get(base + "/../../../../etc/passwd")
    assert info.value.code in (403, 404)


def test_video_serving_when_configured(tmp_path: Path) -> None:
    fixture = tmp_path / "f.json"
    fixture.write_text("{}")
    audio = tmp_path / "f.wav"
    audio.write_bytes(b"x")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 50)

    config = ReviewConfig(
        fixture_path=fixture,
        audio_path=audio,
        video_path=video,
        video_offset_seconds=0.0,
    )
    server = make_server("127.0.0.1", 0, config)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        status, body, _ = _get(f"http://127.0.0.1:{port}/api/video")
        assert status == 200
        assert body == video.read_bytes()
        # And meta reports has_video=True
        _, meta_body, _ = _get(f"http://127.0.0.1:{port}/api/meta")
        meta = json.loads(meta_body)
        assert meta["has_video"] is True
        assert meta["video_filename"] == "v.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
