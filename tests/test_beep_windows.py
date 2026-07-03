from datetime import UTC, datetime, timedelta

from splitsmith.beep_windows import (
    StagePrior,
    derive_scoreboard_windows,
    find_beep_conflicts,
    sequential_window,
)
from splitsmith.config import BeepWindowConfig

CFG = BeepWindowConfig()
T0 = datetime(2026, 6, 14, 9, 0, 0, tzinfo=UTC)


def _prior(n: int, minutes_after_start: float, stage_time: float = 20.0) -> StagePrior:
    return StagePrior(
        stage_number=n,
        scorecard_updated_at=T0 + timedelta(minutes=minutes_after_start),
        time_seconds=stage_time,
    )


def test_scoreboard_window_centers_on_expected_beep() -> None:
    # scorecard 10 min in, stage 20 s, lead 120 s -> expected beep at 460 s
    [w] = derive_scoreboard_windows(T0, 3600.0, [_prior(3, 10.0)], CFG)
    assert w.stage_number == 3
    assert w.source == "scoreboard"
    assert w.start_s == 460.0 - CFG.pre_pad_s
    assert w.end_s == 460.0 + CFG.post_pad_s


def test_windows_clamp_to_file_bounds() -> None:
    [w] = derive_scoreboard_windows(T0, 300.0, [_prior(1, 2.0)], CFG)
    assert w.start_s == 0.0
    assert w.end_s <= 300.0


def test_min_window_widens_toward_other_bound() -> None:
    # Expected beep lands past the end of a short file; the clamped
    # window must still be at least min_window_s long, hugging the end.
    [w] = derive_scoreboard_windows(T0, 100.0, [_prior(1, 30.0)], CFG)
    assert w.end_s == 100.0
    assert w.end_s - w.start_s >= CFG.min_window_s


def test_prior_without_scorecard_is_skipped() -> None:
    priors = [
        _prior(1, 10.0),
        StagePrior(stage_number=2, scorecard_updated_at=None, time_seconds=20.0),
    ]
    windows = derive_scoreboard_windows(T0, 3600.0, priors, CFG)
    assert [w.stage_number for w in windows] == [1]


def test_sequential_window_from_anchor() -> None:
    assert sequential_window(None, 900.0, CFG) == (0.0, 900.0)
    assert sequential_window(310.0, 900.0, CFG) == (310.0, 900.0)


def test_conflicts_flag_both_stages() -> None:
    assert find_beep_conflicts({1: 100.0, 2: 101.0, 3: 500.0}, 2.0) == {1, 2}
    assert find_beep_conflicts({1: 100.0, 3: 500.0}, 2.0) == set()
