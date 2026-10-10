"""Review priority for fixtures (#1363)."""

import numpy as np
import pytest

from splitsmith.lab.inventory import onset_spread_ms, review_priority

SR = 48000


def _clicks(onsets: list[float], seconds: float = 6.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    audio = rng.normal(0.0, 0.002, int(seconds * SR))
    decay = np.exp(-np.arange(int(0.05 * SR)) / (0.006 * SR))
    for t in onsets:
        i = int(t * SR)
        audio[i : i + decay.size] += rng.normal(0.0, 0.5, decay.size) * decay
    return audio


ONSETS = [1.0, 1.4, 1.9, 2.3, 3.1, 3.6, 4.2, 4.9]


def test_spread_is_small_when_stored_times_follow_the_onsets():
    spread = onset_spread_ms(_clicks(ONSETS), SR, ONSETS)
    assert spread is not None and spread < 1.0


def test_a_constant_offset_is_not_spread():
    """The detector's rise foot sits a few ms before a stricter onset by
    definition; only shot-to-shot disagreement says a time may be off."""
    spread = onset_spread_ms(_clicks(ONSETS), SR, [t - 0.004 for t in ONSETS])
    assert spread is not None and spread < 1.0


def test_jittered_times_spread():
    jitter = [0.0, 0.012, -0.010, 0.015, -0.014, 0.011, -0.012, 0.013]
    spread = onset_spread_ms(_clicks(ONSETS), SR, [t + j for t, j in zip(ONSETS, jitter, strict=True)])
    assert spread is not None and spread > 8.0


def test_too_few_measurable_shots_is_unknown():
    assert onset_spread_ms(_clicks([1.0]), SR, [1.0]) is None


def test_snapped_fixtures_come_first_and_say_why():
    snapped, why = review_priority(
        {"review_status": "needs_review", "derived": True, "edge_fraction": 0.4, "onset_spread_ms": 3.0}
    )
    audited, why_audited = review_priority(
        {"review_status": "needs_review", "derived": False, "edge_fraction": None, "onset_spread_ms": 3.0}
    )
    assert snapped > audited
    assert any("snapped" in r for r in why)
    assert any("window edge" in r for r in why)
    assert not any("snapped" in r for r in why_audited)


def test_onset_spread_raises_priority_and_reviewed_is_zero():
    noisy, why = review_priority({"review_status": "reviewed", "derived": False, "onset_spread_ms": 15.0})
    clean, _ = review_priority({"review_status": "reviewed", "derived": False, "onset_spread_ms": 0.5})
    assert noisy > clean == 0.0
    assert any("15 ms" in r for r in why)


def test_suggested_moves_name_the_shots_that_look_off():
    from splitsmith.lab.inventory import suggested_moves

    audio = _clicks(ONSETS)
    stored = list(ONSETS)
    stored[2] += 0.012  # placed 12 ms late
    stored[5] -= 0.015  # 15 ms early
    moves = suggested_moves(audio, SR, stored)
    assert [m["shot_index"] for m in moves] == [2, 5]
    assert moves[0]["move_ms"] == pytest.approx(-12, abs=2)
    assert moves[1]["move_ms"] == pytest.approx(15, abs=2)
    assert moves[0]["suggested"] == pytest.approx(ONSETS[2], abs=0.002)


def test_suggested_moves_raise_priority_and_say_how_many():
    score, why = review_priority(
        {"review_status": "needs_review", "derived": True, "suggested_moves": 4, "n_shots": 20}
    )
    base, _ = review_priority({"review_status": "needs_review", "derived": True, "n_shots": 20})
    assert score > base
    assert any("4 of 20 shots" in r for r in why)
