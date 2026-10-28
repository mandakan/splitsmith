"""Masked speech in fixtures (#1778): the schema helpers, and evaluation
leaving every candidate in masked audio out of the score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.fixture_schema import in_masked_span, masked_spans, publishable, speech_review
from splitsmith.lab import core as lab_core
from tests.test_lab_eval_merge import _candidate, _canned_run

REGION = {"start": 1.0, "end": 2.5, "method": "silero-vad-5.1.2", "masker_version": 1}


def test_masked_spans_read_the_regions_and_skip_a_malformed_one() -> None:
    data = {"masked_regions": [REGION, {"start": "x"}, {**REGION, "start": 3.0, "end": 3.0}]}
    assert masked_spans(data) == [(1.0, 2.5)]
    assert masked_spans({}) == []


def test_a_span_is_half_open() -> None:
    spans = [(1.0, 2.5)]
    assert in_masked_span(1.0, spans) and in_masked_span(2.4, spans)
    assert not in_masked_span(2.5, spans) and not in_masked_span(0.99, spans)


@pytest.mark.parametrize(
    ("review", "expected"),
    [
        (None, False),
        ({"decision": "excluded", "reviewed_at": "2026-10-28T12:00:00Z", "masker_version": 1}, False),
        ({"decision": "masked", "reviewed_at": "2026-10-28T12:00:00Z", "masker_version": 1}, True),
        ({"decision": "no_speech", "reviewed_at": "2026-10-28T12:00:00Z", "masker_version": 1}, True),
        ({"decision": "maybe"}, False),
    ],
)
def test_only_a_reviewed_not_excluded_fixture_is_publishable(review, expected) -> None:  # noqa: ANN001
    data = {} if review is None else {"speech_review": review}
    assert publishable(data) is expected
    if review is not None and expected:
        assert speech_review(data).decision == review["decision"]


def test_a_masked_false_positive_does_not_count() -> None:
    shot = _candidate(1, 3.0, truth=1)
    noise = _candidate(2, 1.5, truth=0).model_copy(update={"masked": True})
    metrics = lab_core._metrics([3.0], [shot, noise])
    assert metrics.false_positives == 0
    assert metrics.precision == 1.0


def test_the_same_false_positive_unmasked_does_count() -> None:
    metrics = lab_core._metrics([3.0], [_candidate(1, 3.0, truth=1), _candidate(2, 1.5, truth=0)])
    assert metrics.false_positives == 1


def test_relabel_refreshes_the_masked_flag_from_the_fixture(tmp_path: Path) -> None:
    """A mask added after the eval ran takes effect on the next label save."""
    audit = tmp_path / "stage-shots-m-2026-stage1-sabc.json"
    audit.write_text(json.dumps({"beep_time": 0.5, "shots": [{"time": 3.0}], "masked_regions": [REGION]}))
    fixture = lab_core.EvalFixture(
        slug=audit.stem,
        audit_path=str(audit),
        audio_path=str(audit.with_suffix(".wav")),
        candidates=[_candidate(1, 3.0, truth=1), _candidate(2, 1.5, truth=0)],
        truth_times=[3.0],
        metrics=lab_core._metrics([3.0], [_candidate(1, 3.0, truth=1), _candidate(2, 1.5, truth=0)]),
        audit_mtime=0.0,
    )
    assert fixture.metrics.false_positives == 1
    run = lab_core.relabel_run(_canned_run([fixture], lab_core.EvalConfig()))
    (after,) = run.universe.fixtures
    assert [c.masked for c in after.candidates] == [False, True]
    assert after.metrics.false_positives == 0


def test_the_build_trains_on_no_candidate_in_masked_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_build_universe`` drops the masked candidate and keeps the
    detector's ``candidate_idx`` for the rest (the sweep join)."""
    import sys
    from types import SimpleNamespace

    import numpy as np

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import build_ensemble_artifacts as mod
    finally:
        sys.path.pop(0)

    stem = "stage-shots-m-2026-stage1-sabc"
    cache = tmp_path / ".cache"
    cache.mkdir()
    (tmp_path / f"{stem}.json").write_text(
        json.dumps(
            {
                "beep_time": 0.5,
                "stage_time_seconds": 5.0,
                "shots": [{"time": 3.0}],
                "camera": {"mount": "head"},
                "masked_regions": [REGION],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / f"{stem}.wav").write_bytes(b"")
    times = [1.5, 3.0, 4.0]
    prompts = mod.feat.CLAP_PROMPTS
    np.savez(
        cache / f"{stem}_clap.npz",
        audio_emb=np.zeros((3, 4)),
        prompts=np.array(prompts, dtype=object),
        text_sims=np.zeros((3, len(prompts))),
    )
    shots = [
        SimpleNamespace(scoring_time=t, time_absolute=t, confidence=0.5, peak_amplitude=0.5) for t in times
    ]
    monkeypatch.setattr(mod, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(mod, "CACHE_DIR", cache)
    monkeypatch.setattr(mod, "load_audio", lambda _p: (np.zeros(48000 * 6, dtype=np.float32), 48000))
    monkeypatch.setattr(mod, "detect_shots", lambda *_a, **_k: shots)
    monkeypatch.setattr(mod, "compute_tta_agreement", lambda *_a, **_k: np.zeros(3))
    monkeypatch.setattr(mod.feat, "compute_hand_features", lambda *_a, **_k: np.zeros((3, 2)))
    monkeypatch.setattr(mod.feat, "clap_diff_from_similarities", lambda sims: np.zeros(len(sims)))

    universe = mod._build_universe([stem], 15.0, log=lambda _m: None)
    assert [(r["candidate_idx"], r["t_absolute"]) for r in universe] == [(1, 3.0), (2, 4.0)]
