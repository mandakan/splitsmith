"""Review-state surfacing on ``FixtureRecord`` (batch-promote gap).

102 of the 126 corpus fixtures entered via the batch promote-stages
panel, which stamps ``promoted_at`` but no ``anchor`` block. The review
queue used to key on ``anchor_slug`` alone, so every batch-promoted
fixture skipped review entirely. These tests pin the replacement rule:
promoted fixtures stay pending until a human label pass lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.lab.core import FixtureRecord, list_fixtures


def _write_fixture(root: Path, slug: str, payload: dict) -> None:
    (root / f"{slug}.json").write_text(json.dumps(payload))
    (root / f"{slug}.wav").write_bytes(b"")


def test_list_fixtures_surfaces_promoted_at_and_label_counts(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
        {
            "promoted_at": "2026-08-14T12:38:39+00:00",
            "shots": [
                {"shot_number": 1, "time": 5.5, "subclass": "paper"},
                {"shot_number": 2, "time": 6.0},
            ],
            "_candidates_pending_audit": {
                "candidates": [],
                "labels_by_time": {"7.100": "echo", "8.250": "cross_bay"},
            },
        },
    )
    (rec,) = list_fixtures(fixtures)
    assert rec.promoted_at == "2026-08-14T12:38:39+00:00"
    assert rec.n_labeled_shots == 1
    assert rec.n_labeled_rejects == 2


def test_list_fixtures_defaults_review_fields_for_legacy_fixture(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(fixtures, "stage-shots-blacksmith-2026-stage6", {"shots": [{"time": 5.5}]})
    (rec,) = list_fixtures(fixtures)
    assert rec.promoted_at is None
    assert rec.n_labeled_shots == 0
    assert rec.n_labeled_rejects == 0
    assert rec.needs_review is False


def test_needs_review_anchor_promoted_regardless_of_labels() -> None:
    """Anchor fixtures copy subclasses from the anchor, so label presence
    proves nothing -- they stay pending for the diff-confirm screen."""
    rec = _record(anchor_slug="stage-shots-foo-2026-stage1", n_labeled_shots=5)
    assert rec.needs_review is True


def test_needs_review_batch_promoted_until_any_label_lands() -> None:
    unlabeled = _record(promoted_at="2026-08-14T12:00:00+00:00")
    assert unlabeled.needs_review is True

    subclassed = _record(promoted_at="2026-08-14T12:00:00+00:00", n_labeled_shots=1)
    assert subclassed.needs_review is False

    reason_only = _record(promoted_at="2026-08-14T12:00:00+00:00", n_labeled_rejects=1)
    assert reason_only.needs_review is False


def test_confirm_review_stamps_and_clears_pending(tmp_path: Path) -> None:
    """The queue's "Approve to corpus" writes ``review.confirmed_at``
    into the fixture JSON; a confirmed fixture stops pending regardless
    of promotion path or label state."""
    from splitsmith.lab.core import confirm_review

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_fixture(
        fixtures,
        "stage-shots-hfo-masters-2026-stage1-s0fe3d797",
        {"promoted_at": "2026-08-14T12:38:39+00:00", "shots": [{"time": 5.5}]},
    )
    path = fixtures / "stage-shots-hfo-masters-2026-stage1-s0fe3d797.json"

    (rec,) = list_fixtures(fixtures)
    assert rec.needs_review is True

    stamp = confirm_review(path)
    assert stamp  # ISO timestamp

    (rec,) = list_fixtures(fixtures)
    assert rec.review_confirmed_at == stamp
    assert rec.needs_review is False
    # The payload's shots survived the rewrite.
    assert json.loads(path.read_text())["shots"] == [{"time": 5.5}]


def test_needs_review_confirmation_trumps_anchor() -> None:
    rec = _record(
        anchor_slug="stage-shots-foo-2026-stage1",
        review_confirmed_at="2026-08-17T10:00:00+00:00",
    )
    assert rec.needs_review is False


def _record(**overrides) -> FixtureRecord:
    base: dict = {
        "slug": "stage-shots-foo-2026-stage1",
        "audit_path": "/x.json",
        "audio_path": "/x.wav",
        "has_audio": True,
        "n_shots": 10,
        "audit_mtime": 0.0,
    }
    base.update(overrides)
    return FixtureRecord(**base)


def test_run_eval_routes_the_fixture_camera_class(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lab eval must score each fixture through its own camera-class
    model, matching production's shot-detect endpoint. It used to omit
    ``camera_class`` entirely, so every fixture went through the DEFAULT
    (headcam) GBDT + thresholds and a retrain that only moved the
    handheld class produced bit-identical eval numbers (2026-08-17 A/B).

    Plumbing test only: the wav is synthetic silence because no audio is
    analysed -- ``detect_shots_ensemble`` is stubbed to capture kwargs.
    """
    import wave
    from types import SimpleNamespace

    import splitsmith.lab.core as core

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    slug = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    (fixtures / f"{slug}.json").write_text(
        json.dumps(
            {
                "beep_time": 1.0,
                "stage_time_seconds": 2.0,
                "shots": [{"shot_number": 1, "time": 1.5}],
                "camera": {"mount": "hand", "make": "Apple", "model": "iPhone 17 Pro"},
            }
        )
    )
    with wave.open(str(fixtures / f"{slug}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)

    captured: dict = {}

    def fake_detect(_audio, _sr, _beep, _stage, _runtime, **kw):
        captured.update(kw)
        return SimpleNamespace(candidates=[])

    monkeypatch.setattr(core, "detect_shots_ensemble", fake_detect)
    runtime = SimpleNamespace(
        calibration=SimpleNamespace(voter_a_floor=0.1, voter_b_threshold=0.1, voter_c_threshold=0.5)
    )
    core.run_eval(runtime, fixtures_root=fixtures, slugs=[slug])  # type: ignore[arg-type]

    assert captured["camera_class"] == "handheld"
    assert captured["camera_make"] == "Apple"
    assert captured["camera_model"] == "iPhone 17 Pro"
