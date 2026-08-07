"""The sprite sequence and its concat list: order, dedup, and the time base.

Where the ink lands is ``test_compare_overlay_live_render.py``'s job since
issue #693 -- a sprite is a Chromium render now, so pixel assertions need
a browser and are integration-marked. Everything here is about the
*sequence*: which file each state points at, how long it is held, and how
those boundaries are snapped onto whole output frames. None of it needs a
real render, so the rasterizer is a fake that returns a distinct byte
string per document.
"""

import pytest

from splitsmith import overlay_text
from splitsmith.compare import overlay_live, overlay_sprites
from splitsmith.overlay_theme import load_theme

GEOMETRY = overlay_sprites.SpriteGeometry(canvas_width=1280, canvas_height=720, rows=2, cols=2)
THEME = load_theme("clean")


class _FakeRasterizer:
    """Distinct bytes per distinct document, no browser. These tests care
    that two different states get two different files, never what is
    drawn in them."""

    def __init__(self) -> None:
        self.calls = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls += 1
        return b"\x89PNG\r\n\x1a\n" + str(self.calls).encode()


def _panel(label, row, col, **kwargs):
    base = {
        "label": label,
        "row": row,
        "col": col,
        "present": True,
        "shots_fired": 0,
        "expected_shots": None,
        "last_split": None,
    }
    base.update(kwargs)
    return overlay_sprites.TilePanel(**base)


def _state(panels, start=0.0, duration=1.0):
    return overlay_sprites.OverlayState(start_seconds=start, duration_seconds=duration, panels=tuple(panels))


def _sequence(states, tmp_path, geometry=GEOMETRY, theme=THEME):
    """One sequence through the real writer with a fake rasterizer."""
    return overlay_live.write_sprite_sequence(
        states, geometry, theme=theme, cache_dir=tmp_path, rasterizer=_FakeRasterizer()
    )


def test_concat_list_repeats_the_final_entry(tmp_path):
    # The concat demuxer ignores the last entry's duration unless the
    # file is listed once more after it.
    panels = [_panel("ann", 0, 0)]
    sequence = _sequence([_state(panels, 0.0, 2.0)], tmp_path)
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "sprites.txt", frame_rate=(30, 1))
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    assert lines[0].startswith("file ")
    assert lines[1] == "option framerate 30/1"
    assert lines[2] == "duration 2"
    assert lines[-2] == lines[0]


# --- boundary quantisation ------------------------------------------
#
# The sprite is a stepped image and the clock is a per-frame drawtext
# expression. If a state boundary lands between two output frames the two
# halves of the overlay disagree at every shot -- measured before this
# was fixed, the frame at a shot's own time showed the clock reading
# 0.70 with no counter, and the counter arrived a frame later against
# 0.73. Boundaries therefore have to land on frames that exist.


def _boundaries(durations):
    out, elapsed = [], 0.0
    for duration in durations:
        out.append(elapsed)
        elapsed += duration
    return out


def test_every_boundary_lands_on_a_whole_output_frame():
    # Shot times are millisecond-grained, so raw boundaries sit wherever
    # they land: 1.712 and 2.401 are both mid-frame at 30fps.
    durations = overlay_sprites.quantize_durations([1.712, 0.689, 0.4], frame_rate=(30, 1))
    for boundary in _boundaries(durations):
        frames = boundary * 30
        assert abs(frames - round(frames)) < 1e-6, f"{boundary} is not on a frame"


def test_a_boundary_already_on_a_frame_is_not_pushed_a_frame_late():
    # 8.3 is frame 249 exactly, but ``8.3 * 30`` is 249.00000000000003 in
    # binary floating point, so a bare ceil moves it to frame 250 -- the
    # boundary lands a whole frame late, which is the exact defect the
    # quantiser exists to remove. Of the 20000 millisecond positions in a
    # 20s stage, 8.3, 16.1 and 16.6 are the three that do this at 30fps;
    # 1.7 (the reviewer's example) is exactly 51.0 and would not catch it.
    durations = overlay_sprites.quantize_durations([8.3, 0.3], frame_rate=(30, 1))
    assert _boundaries(durations)[1] == pytest.approx(8.3, abs=1e-9)


def test_a_mid_frame_boundary_rounds_up_never_to_nearest():
    # 1.712s is 51.36 frames. Rounding to nearest would put it at frame
    # 51 (1.700s) -- the counter would step up on a frame shown 12ms
    # before the shot was fired.
    durations = overlay_sprites.quantize_durations([1.712, 0.5], frame_rate=(30, 1))
    assert _boundaries(durations)[1] == pytest.approx(52 / 30, abs=1e-9)


def test_quantising_preserves_the_total_exactly():
    raw = [1.712, 0.689, 0.4, 1.011]
    durations = overlay_sprites.quantize_durations(raw, frame_rate=(30, 1))
    assert sum(durations) == pytest.approx(sum(raw), abs=1e-9)


def test_a_fractional_rate_quantises_to_its_own_frames():
    durations = overlay_sprites.quantize_durations([1.712, 0.5], frame_rate=(30000, 1001))
    boundary = _boundaries(durations)[1]
    frames = boundary * 30000 / 1001
    assert abs(frames - round(frames)) < 1e-6


def test_two_events_inside_one_frame_collapse_the_superseded_state():
    # 0.705s and 0.710s are both inside frame 22 at 30fps (0.7333s). The
    # first state can never be displayed, so it collapses to zero length.
    durations = overlay_sprites.quantize_durations([0.705, 0.005, 0.29], frame_rate=(30, 1))
    assert durations[1] == pytest.approx(0.0, abs=1e-9)
    assert durations[0] > 0.0 and durations[2] > 0.0


def test_the_final_state_never_gets_a_negative_duration():
    # 0.99s falls inside the *last* frame of a 0.991s segment at 30fps,
    # so rounding its boundary up names frame 30 -- a frame the segment
    # does not contain. The final duration came out -0.009, which
    # write_concat_list drops silently: the last state's sprite is never
    # written, the trailing repeat shows the previous one, and that
    # shot's counter increment never reaches the screen.
    durations = overlay_sprites.quantize_durations([0.99, 0.001], frame_rate=(30, 1))
    assert durations[-1] > 0.0, f"final state has a non-positive duration: {durations}"
    assert sum(durations) == pytest.approx(0.991, abs=1e-9)


def test_the_final_state_is_clamped_to_the_last_frame_that_exists():
    # Same shape, a different pair: the boundary is pulled back to frame
    # 29 (the last one a 0.985s segment at 30fps has) rather than pushed
    # to frame 30, which nothing would ever render.
    durations = overlay_sprites.quantize_durations([0.98, 0.005], frame_rate=(30, 1))
    assert durations[-1] > 0.0, f"final state has a non-positive duration: {durations}"
    assert sum(durations) == pytest.approx(0.985, abs=1e-9)
    assert _boundaries(durations)[1] == pytest.approx(29 / 30, abs=1e-9)


def test_a_shot_in_the_last_frame_still_reaches_the_concat_list(tmp_path):
    # The pixels half of the two tests above: the surviving entry has to
    # be the state carrying the *second* shot, not the first.
    two_shots = _sequence(
        [_state([_panel("ann", 0, 0, shots_fired=2)], 0.0, 1.0)],
        tmp_path,
    )
    sequence = _sequence(
        [
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.0, 0.99),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.99, 0.001),
        ],
        tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    files = [ln for ln in list_path.read_text().splitlines() if ln.startswith("file ")]
    assert (
        files[-1] == f"file '{two_shots[0][0].resolve()}'"
    ), "the last shot's sprite is not the one left on screen at the end of the segment"
    assert files[-2] == files[-1]


def test_a_collapsed_state_is_dropped_from_the_list_not_written_as_zero(tmp_path):
    # A ``duration 0`` entry is a state no frame can ever show, and the
    # demuxer's handling of it is not something to rely on.
    sequence = _sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 0.705),
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.705, 0.005),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.710, 0.29),
        ],
        tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    text = list_path.read_text()
    assert "duration 0\n" not in text
    durations = [ln.split()[1] for ln in text.splitlines() if ln.startswith("duration")]
    assert len(durations) == 2


def test_a_collapsed_state_never_loses_a_shot(tmp_path):
    # The state that survives is the *later* one, so both shots are still
    # counted. Losing the surviving state instead would drop a shot off
    # the tile's counter permanently.
    two_shots = _sequence(
        [_state([_panel("ann", 0, 0, shots_fired=2)], 0.0, 1.0)],
        tmp_path,
    )
    sequence = _sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 0.705),
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.705, 0.005),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.710, 0.29),
        ],
        tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    files = [ln for ln in list_path.read_text().splitlines() if ln.startswith("file ")]
    # last real entry + the trailing repeat, both the 2-shot sprite
    assert files[-1] == f"file '{two_shots[0][0].resolve()}'"
    assert files[-2] == files[-1]


def test_the_list_pins_the_demuxer_framerate_on_every_entry(tmp_path):
    # Without this the concat demuxer opens each PNG through image2 at its
    # default 25fps, takes its time base from that, and snaps every
    # boundary to 1/25s -- a grid on which 1/30s boundaries do not exist.
    sequence = _sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 1.6),
            _state([_panel("ann", 0, 0, shots_fired=1)], 1.6, 0.4),
        ],
        tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30000, 1001))
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    files = [i for i, ln in enumerate(lines) if ln.startswith("file ")]
    assert files, "no entries written"
    for index in files:
        assert lines[index + 1] == "option framerate 30000/1001"


def test_materialize_font_writes_a_readable_file(tmp_path):
    path = overlay_text.materialize_font("splitsmith-mono", tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.parent == tmp_path
