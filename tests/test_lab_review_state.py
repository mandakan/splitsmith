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
