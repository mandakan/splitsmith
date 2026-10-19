"""The beep step's banded peaks and spectrum routes (spec 2026-10-17)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

SR = 48000


def _write_overlap_wav(path: Path) -> None:
    t = np.arange(3 * SR) / SR
    sig = np.where((t >= 1.0) & (t < 1.4), 0.5 * np.sin(2 * np.pi * 2800 * t), 0.0)
    rng = np.random.default_rng(1)
    i, n = int(0.96 * SR), int(0.15 * SR)
    # A cross-bay shot's level: over the tone full range, well under it in band.
    sig[i : i + n] += 0.3 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.02 * SR))
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), sig.astype(np.float32), SR)


@pytest.fixture
def overlap(tmp_path: Path):
    from splitsmith.match_project import MatchProject
    from splitsmith.ui import audio as audio_helpers

    from .ui._helpers import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter = root / "shooters" / "me"
    project = MatchProject.load(shooter)
    video = project.stage(1).primary()
    wav = audio_helpers.video_audio_path(shooter, 1, video, project=project)
    _write_overlap_wav(wav)
    return client, video.video_id


def _url(vid: str) -> str:
    return f"/api/shooters/me/stages/1/videos/{vid}/peaks?bins=2000&start=0.5&end=1.5"


def test_banded_peaks_lose_the_shot(overlap) -> None:
    client, vid = overlap
    full = client.get(_url(vid)).json()["peaks"]
    banded = client.get(_url(vid) + "&band=2200-3500").json()["peaks"]
    shot_bin = int((0.97 - 0.5) / 1.0 * 2000)
    assert full[shot_bin] > 0.3
    assert banded[shot_bin] < 0.3 * max(banded)


@pytest.mark.parametrize("band", ["3500-2200", "50-3000", "2200-13000", "abc", "2200"])
def test_band_validation(overlap, band: str) -> None:
    client, vid = overlap
    assert client.get(_url(vid) + f"&band={band}").status_code == 422


def test_spectrum_shows_the_tone(overlap) -> None:
    import base64

    client, vid = overlap
    r = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0.5&end=1.5")
    assert r.status_code == 200, r.text
    body = r.json()
    grid = np.frombuffer(base64.b64decode(body["data"]), dtype=np.uint8).reshape(
        body["columns"], len(body["hz"])
    )
    col = int((1.2 - body["start"]) / body["duration"] * body["columns"])
    assert abs(body["hz"][int(grid[col].argmax())] - 2800) < 100
    lo, hi = body["tone_band"]
    assert lo < 2800 < hi  # the timer's own band, for the Timer tone filter


def test_spectrum_window_is_capped(overlap) -> None:
    client, vid = overlap
    body = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0&end=60").json()
    assert body["duration"] <= 4.0 + 1e-6


def test_spectrum_without_audio_is_pending(tmp_path) -> None:
    from .ui._helpers import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    from splitsmith.match_project import MatchProject

    vid = MatchProject.load(root / "shooters" / "me").stage(1).primary().video_id
    r = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0&end=1")
    assert r.status_code == 202 and r.json()["pending"] is True
