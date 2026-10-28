"""The speech review (lab/speech_review.py, #1778): saving the reviewer's
decision into a fixture, the refusals, and the lab routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from splitsmith.fixture_schema import masked_spans, publishable
from splitsmith.lab import speech_mask
from splitsmith.lab import speech_review as sr

SLUG = "stage-shots-m-2026-stage1-sabc"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture()
def fixture(tmp_path: Path) -> Path:
    path = tmp_path / f"{SLUG}.json"
    path.write_text(
        json.dumps(
            {"beep_time": 5.0, "shots": [{"time": 6.0}, {"time": 6.4}], "stage_rounds": {"expected": 2}}
        ),
        encoding="utf-8",
    )
    sf.write(path.with_suffix(".wav"), np.zeros(48000 * 10, dtype=np.float32), 48000)
    return path


def _save(path: Path, decision: str, regions: list[tuple[float, float]], **kw) -> dict:  # noqa: ANN003
    body = sr.SpeechReviewIn(decision=decision, regions=[{"start": s, "end": e} for s, e in regions])
    return sr.save_review(path, body, now=NOW, **kw)


def test_accepting_masks_writes_the_regions_and_the_stamp(fixture: Path) -> None:
    data = _save(fixture, "masked", [(3.0, 4.0), (1.0, 2.0)])
    stored = json.loads(fixture.read_text(encoding="utf-8"))
    assert stored == data
    assert masked_spans(stored) == [(1.0, 2.0), (3.0, 4.0)]
    assert stored["masked_regions"][0]["masker_version"] == speech_mask.MASKER_VERSION
    assert stored["speech_review"]["decision"] == "masked"
    assert stored["stage_rounds"] == {"expected": 2}
    assert publishable(stored)


def test_a_region_over_a_shot_is_refused(fixture: Path) -> None:
    with pytest.raises(sr.SpeechReviewError, match="covers a shot or the beep"):
        _save(fixture, "masked", [(5.9, 6.2)])
    assert "speech_review" not in json.loads(fixture.read_text(encoding="utf-8"))


def test_a_region_over_the_beep_is_refused(fixture: Path) -> None:
    with pytest.raises(sr.SpeechReviewError):
        _save(fixture, "masked", [(4.5, 5.01)])


@pytest.mark.parametrize(
    ("regions", "message"),
    [([(2.0, 2.0)], "no length"), ([(1.0, 2.0), (1.5, 2.5)], "overlap"), ([(9.0, 11.0)], "past the clip")],
)
def test_malformed_regions_are_refused(fixture: Path, regions, message) -> None:  # noqa: ANN001
    with pytest.raises(sr.SpeechReviewError, match=message):
        _save(fixture, "masked", regions, duration=10.0)


def test_accepting_masks_needs_a_region(fixture: Path) -> None:
    with pytest.raises(sr.SpeechReviewError, match="no speech"):
        _save(fixture, "masked", [])


def test_no_speech_clears_earlier_masks(fixture: Path) -> None:
    _save(fixture, "masked", [(1.0, 2.0)])
    stored = _save(fixture, "no_speech", [(1.0, 2.0)])
    assert stored["masked_regions"] == []
    assert publishable(stored)


def test_excluded_keeps_its_masks_and_is_never_published(fixture: Path) -> None:
    stored = _save(fixture, "excluded", [(1.0, 2.0)])
    assert masked_spans(stored) == [(1.0, 2.0)]
    assert not publishable(stored)


def test_load_without_the_detector_shows_what_is_stored(fixture: Path) -> None:
    _save(fixture, "masked", [(1.0, 2.0)])
    state = sr.load_state(fixture, session=None)
    assert state.proposal is None
    assert state.duration == 10.0
    assert [(r.start, r.end) for r in state.masked_regions] == [(1.0, 2.0)]
    assert state.review is not None and state.review.decision == "masked"
    assert len(state.protected) == 3


@pytest.mark.parametrize("slug", ["../secret", "a/b", "", ".hidden"])
def test_a_slug_never_leaves_the_fixtures_root(tmp_path: Path, slug: str) -> None:
    with pytest.raises(FileNotFoundError):
        sr.fixture_path(tmp_path, slug)


def test_the_lab_routes_load_and_save(fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.lab import core as lab_core
    from splitsmith.ui.server import create_app

    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", fixture.parent)

    def no_detector() -> None:
        raise OSError("offline")

    monkeypatch.setattr(speech_mask, "load_vad", no_detector)
    from splitsmith import match_model

    project = tmp_path / "match"
    match = match_model.Match.init(project, name="Speech Review Test")
    match.add_shooter(project, match_model.Shooter(slug="me", name="Me"))
    client = TestClient(create_app(project_root=project, project_name="ignored", lab_enabled=True))

    got = client.get(f"/api/lab/fixtures/{SLUG}/speech")
    assert got.status_code == 200
    assert got.json()["proposal"] is None and got.json()["review"] is None

    refused = client.put(
        f"/api/lab/fixtures/{SLUG}/speech",
        json={"decision": "masked", "regions": [{"start": 5.95, "end": 6.1}]},
    )
    assert refused.status_code == 422
    assert "covers a shot" in refused.json()["detail"]

    saved = client.put(
        f"/api/lab/fixtures/{SLUG}/speech",
        json={"decision": "masked", "regions": [{"start": 1.0, "end": 2.0}]},
    )
    assert saved.status_code == 200
    assert saved.json()["review"]["decision"] == "masked"
    assert client.get("/api/lab/fixtures/../x/speech").status_code == 404
