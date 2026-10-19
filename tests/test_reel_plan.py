from pathlib import Path

import pytest

from splitsmith.highlights import Moment, ReelPicks
from splitsmith.reel.plan import (
    FootageClip,
    ReelCard,
    ReelClip,
    StageFootage,
    figure_text,
    plan_reel,
)

P1 = Path("/m/s1.mp4")
S1 = Path("/m/s1_cam_b.mp4")


def _footage(beep=5.0, duration=30.0, secondary=False) -> dict[int, StageFootage]:
    return {
        1: StageFootage(
            1,
            "Stage 1",
            FootageClip(P1, beep, duration),
            FootageClip(S1, 4.0, duration) if secondary else None,
        )
    }


def _split(stage=1, focus=3.0, start=2.4, end=3.6) -> Moment:
    return Moment("fastest_split", stage, start, end, focus, 0.2)


def _plan(picks, footage=None):
    return plan_reel(
        picks,
        footage if footage is not None else _footage(),
        title="Bromma 2026",
        shooter="Anna",
        end_lines=("Draw 0.94",),
    )


def test_spine_shape_for_one_beat():
    m = _split()
    items = _plan(ReelPicks(cold_open=m, beats=(m,), montage=((1, 3.0),)))
    kinds = [(type(i).__name__, getattr(i, "kind", None)) for i in items]
    assert kinds == [
        ("ReelCard", "intro"),  # the mood: drone, the match name revealed, a riser
        ("ReelCard", "title"),  # the braam, the shooter slammed in
        ("ReelCard", "slam"),  # black, the timer's beep
        ("ReelClip", None),  # cold open slow motion
        ("ReelCard", "callout"),
        ("ReelCard", "slam"),
        ("ReelClip", None),
        ("ReelCard", "callout"),
        ("ReelClip", None),  # montage cut
        ("ReelCard", "end"),
    ]


def test_the_opening_builds_before_anything_happens():
    m = _split()
    intro, title, beep = _plan(ReelPicks(cold_open=m, beats=(), montage=()))[:3]
    assert (intro.text, intro.sfx) == ("Bromma 2026", ("drone",))
    assert intro.seconds >= 4.0
    # The riser ends on the title's cut, where the braam hits.
    assert (title.text, title.sfx) == ("Anna", ("riser", "braam"))
    assert beep.sfx == ("beep",)


def test_clip_times_are_clip_local():
    m = _split()
    items = _plan(ReelPicks(None, (m,), ()))
    clip = next(i for i in items if isinstance(i, ReelClip))
    assert clip.source == P1
    assert clip.start == pytest.approx(5.0 + 2.4)
    assert clip.seconds == pytest.approx(1.2)
    callout = next(i for i in items if isinstance(i, ReelCard) and i.kind == "callout")
    assert callout.backdrop is not None and callout.backdrop.at == pytest.approx(5.0 + 3.0)
    assert (callout.figure, callout.unit) == ("0.20", "SEC")


def test_window_clamps_to_clip_and_drops_slivers():
    early = Moment("fastest_draw", 1, -0.4, 1.5, 0.9, 0.9)
    items = _plan(ReelPicks(None, (early,), ()), footage=_footage(beep=0.2))
    clip = next(i for i in items if isinstance(i, ReelClip))
    assert clip.start == 0.0 and clip.seconds == pytest.approx(1.7)
    sliver = Moment("fastest_split", 1, 24.9, 31.0, 25.0, 0.2)  # the clip ends 25 s after the beep
    assert not any(isinstance(i, ReelClip) for i in _plan(ReelPicks(None, (sliver,), ())))


def test_a_stage_without_footage_drops_its_beat_and_cuts():
    m = _split(stage=7)
    items = _plan(ReelPicks(m, (m,), ((7, 3.0),)))
    assert not any(isinstance(i, ReelClip) for i in items)


def test_secondary_angle_replays_in_slow_motion():
    m = _split()
    items = _plan(ReelPicks(None, (m,), ()), footage=_footage(secondary=True))
    replay = [i for i in items if isinstance(i, ReelClip) and i.source == S1]
    assert len(replay) == 1
    assert replay[0].speed == 0.5 and replay[0].sfx == ("slowmo",)
    assert replay[0].start == pytest.approx(4.0 + 3.0 - 1.0)


def test_the_shorts_cut_is_tighter_and_never_replays():
    m = _split()
    picks = ReelPicks(cold_open=m, beats=(m,), montage=((1, 3.0),))
    normal = plan_reel(picks, _footage(secondary=True), title="T", shooter="A", end_lines=())
    short = plan_reel(picks, _footage(secondary=True), title="T", shooter="A", end_lines=(), short=True)

    def total(items):
        return sum(i.out_seconds if isinstance(i, ReelClip) else i.seconds for i in items)

    assert short[0].kind == "intro" and short[0].seconds == pytest.approx(2.4)
    assert not any(isinstance(i, ReelClip) and i.source == S1 for i in short)
    assert any(isinstance(i, ReelClip) and i.source == S1 for i in normal)
    assert short[-1].kind == "end"
    assert total(short) < total(normal) - 4


def test_figure_text():
    assert figure_text(Moment("longest_streak", 1, 0, 1, 1, 7)) == ("7", "SHOTS")
    assert figure_text(Moment("best_stage", 1, 0, 1, 1, 97.46)) == ("97.5", "%")
    assert figure_text(Moment("fastest_draw", 1, 0, 1, 1, 0.94)) == ("0.94", "SEC")
    assert figure_text(Moment("longest_burst", 1, 0, 1, 1, 5)) == ("5", "SHOTS")
    assert figure_text(Moment("most_reloads", 1, 0, 1, 1, 4)) == ("4", "MAGS")
    assert figure_text(Moment("most_shots", 1, 0, 1, 1, 32)) == ("32", "ROUNDS")
    assert figure_text(Moment("top_hit_factor", 1, 0, 1, 1, 7.123)) == ("7.12", "HF")
    assert figure_text(Moment("moving_reload", 1, 0, 1, 1, 0.21)) == ("0.21", "SEC EXPOSED")


def test_every_kind_has_a_card_label():
    from splitsmith.highlights import MOMENT_KINDS
    from splitsmith.reel.plan import MOMENT_LABELS

    assert set(MOMENT_LABELS) == set(MOMENT_KINDS)
    assert MOMENT_LABELS["thinker"] == "THE THINKER"


def test_a_moments_qualifier_prints_under_its_stage():
    burst = Moment("longest_burst", 1, 2.4, 3.6, 3.0, 5, label="IN 1 S")
    callout = next(
        i for i in _plan(ReelPicks(None, (burst,), ())) if isinstance(i, ReelCard) and i.kind == "callout"
    )
    assert callout.lines == ("Stage 1", "IN 1 S")
