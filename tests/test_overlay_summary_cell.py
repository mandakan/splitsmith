"""The stage summary as one full-frame still (issue #972)."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import overlay_summary_cell as cell
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_html import single_html
from splitsmith.overlay_layout import Anchor, ColorToken, Element, Emphasis, Flow, Group, Role
from splitsmith.overlay_theme import load_theme
from splitsmith.stage_summary_data import TileShot, TileStageData, load_stage_reloads, load_stage_shots

THEME = load_theme("splitsmith")


class _FakeRasterizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append(html)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def _tile() -> TileStageData:
    return TileStageData(
        label="Me",
        stage_number=3,
        shots=(TileShot(1.2, 1.2), TileShot(1.5, 0.3), TileShot(1.9, 0.4)),
        stage_time_seconds=4.5,
        scorecard=StageScorecard(hit_factor=12.0, alphas=10, charlies=1, deltas=1, misses=0),
    )


def _frame(tmp_path: Path) -> Path:
    path = tmp_path / "last.png"
    Image.new("RGB", (640, 360), (200, 200, 200)).save(path)
    return path


def test_summary_groups_are_the_grids_cell_declaration() -> None:
    """The single-shooter summary says exactly what a grid cell says: the
    hoist moved the function, not the design."""
    from splitsmith.compare import overlay_summary as grid

    assert grid._cell_groups is cell.summary_groups
    assert grid._count_elements is cell.count_elements
    assert grid._summary_scale is cell.summary_scale


def test_still_composes_text_over_the_blurred_frame(tmp_path: Path) -> None:
    fake = _FakeRasterizer()
    still = cell.build_summary_still(
        _tile(), "Me", width=320, height=180, theme=THEME, rasterizer=fake, backdrop=_frame(tmp_path)
    )
    assert still is not None
    assert still.size == (320, 180) and still.mode == "RGB"
    (html,) = fake.calls
    for text in ("Me", "12.00", "4.50s", "A10", "0.30", "Draw"):
        assert text in html
    r, g, b = still.getpixel((160, 90))
    assert 0 < r < 200 and r == g == b


def test_no_browser_keeps_the_blurred_frame_without_text(tmp_path: Path) -> None:
    still = cell.build_summary_still(
        _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=None, backdrop=_frame(tmp_path)
    )
    assert still is not None
    assert still.size == (64, 36)


def test_no_frame_paints_the_surface_under_the_text(tmp_path: Path) -> None:
    still = cell.build_summary_still(
        _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=_FakeRasterizer(), backdrop=None
    )
    assert still is not None
    assert still.getpixel((1, 1)) == THEME.surface


def test_nothing_to_hold_on_is_none(tmp_path: Path) -> None:
    assert (
        cell.build_summary_still(
            _tile(), "Me", width=64, height=36, theme=THEME, rasterizer=None, backdrop=None
        )
        is None
    )


def test_summary_of_a_stage_without_shots_draws_time_and_scoring_only() -> None:
    """A stage exported from its beep and stage time alone (no audit) still
    gets a summary hold: the Scoring band with the stage time (and the
    scorecard when there is one), and no Splits band. Time-only when the
    stage was timed by hand and never scored."""
    from splitsmith.overlay_layout import Role
    from splitsmith.overlay_summary_cell import summary_groups, summary_scale
    from splitsmith.stage_summary_data import TileStageData

    tile = TileStageData(label="Mathias", stage_number=3, stage_time_seconds=21.37, stage_time_is_manual=True)
    groups = summary_groups(tile, "Mathias", scale=summary_scale(1080), cell_width=1920, cell_height=1080)
    texts = [e.text for g in groups for e in g.elements]
    assert "Mathias" in texts
    assert any(t.startswith("21.37") for t in texts)
    assert "Splits" not in texts and "Draw" not in [e.caption for g in groups for e in g.elements]
    assert not any(
        e.role == Role.HEADLINE and e.caption in ("Best", "Avg", "Worst") for g in groups for e in g.elements
    )


def test_the_summary_carries_the_shooters_accent(tmp_path: Path) -> None:
    fake = _FakeRasterizer()
    cell.build_summary_still(
        _tile(), "Me", width=320, height=180, theme=THEME, rasterizer=fake, backdrop=None, accent="#abcdef"
    )
    (html,) = fake.calls
    assert '<div class="cell" style="--accent:#abcdef">' in html


# --- stage events on the summary (spec 2026-10-08, part 2: Summary card) ----

#: One stage: a draw, standing splits, a movement with two splits on the
#: move, a second movement a confirmed reload ends 0.31 s after, then two
#: standing splits. ``(seconds from beep, interval class)``.
_EVENT_SHOTS = (
    (1.10, "first_shot"),
    (1.35, "split"),
    (1.58, "split"),
    (2.90, "transition"),
    (3.12, "split"),
    (4.35, "movement"),
    (4.66, "split"),
    (4.98, "split"),
    (7.70, "movement"),
    (9.60, "reload"),
    (9.85, "split"),
    (10.12, "split"),
)
_MOVE_1 = {"id": "evt-1", "kind": "movement", "start": 3.4, "end": 6.1, "source": "manual"}
_MOVE_2 = {"id": "evt-2", "kind": "movement", "start": 7.6, "end": 9.16, "source": "manual"}
_RELOAD = {"id": "evt-3", "kind": "reload", "start": 8.05, "end": 9.47, "source": "manual"}
_AUTO_RELOAD = {"id": "evt-4", "kind": "reload", "start": 2.9, "end": 3.1, "source": "auto"}
_SCORECARD = StageScorecard(
    hit_factor=6.42, alphas=20, charlies=3, deltas=1, misses=0, no_shoots=0, procedurals=1
)


def _audit(
    tmp_path: Path, events: list[dict] | None, shots: tuple[tuple[float, str], ...] = _EVENT_SHOTS
) -> Path:
    doc: dict = {
        "stage_number": 3,
        "shots": [
            {
                "shot_number": i + 1,
                "ms_after_beep": round(t * 1000),
                "interval_class": c,
                "interval_class_source": "auto",
            }
            for i, (t, c) in enumerate(shots)
        ],
    }
    if events is not None:
        doc["events"] = events
    path = tmp_path / f"audit-{len(list(tmp_path.iterdir()))}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _events_tile(path: Path) -> TileStageData:
    return TileStageData(
        label="Me",
        stage_number=3,
        shots=load_stage_shots(path),
        stage_time_seconds=10.4,
        scorecard=_SCORECARD,
        reloads=load_stage_reloads(path),
    )


def _groups(tile: TileStageData) -> tuple[Group, ...]:
    return cell.summary_groups(tile, "Me", scale=cell.summary_scale(1080), cell_width=1920, cell_height=1080)


def _splits_rows(groups: tuple[Group, ...]) -> list[list[tuple[str | None, str]]]:
    """Every grid row after the Splits label, as ``(caption, text)`` pairs."""
    texts = [[e.text for e in g.elements] for g in groups]
    start = texts.index(["Splits"])
    return [[(e.caption, e.text) for e in g.elements] for g in groups[start + 1 :]]


#: The groups a stage without confirmed regions declared before stage
#: events reached the summary (fc0d6acd), written out literally.
_UNTOUCHED_SHOTS = ((1.10, "first_shot"), (1.35, "split"), (2.90, "transition"), (3.13, "split"))
_PLAIN = Emphasis.PLAIN
_PINNED_UNTOUCHED = (
    Group(Anchor.TOP_CENTER, Flow.ROW, (Element(Role.IDENTITY, "Me"),), align="left"),
    Group(Anchor.MIDDLE_CENTER, Flow.ROW, (Element(Role.LABEL, "Scoring", drop_priority=8),), align="left"),
    Group(
        Anchor.MIDDLE_CENTER,
        Flow.ROW,
        (
            Element(Role.DETAIL, "A20", _PLAIN, color=ColorToken.SPLIT_GOOD, drop_priority=2),
            Element(Role.DETAIL, "C3", _PLAIN, color=ColorToken.INK, drop_priority=3),
            Element(Role.DETAIL, "D1", _PLAIN, color=ColorToken.SPLIT, drop_priority=4),
            Element(Role.DETAIL, "M0", _PLAIN, color=ColorToken.ACCENT_TEXT, drop_priority=0),
            Element(Role.DETAIL, "NS0", _PLAIN, color=ColorToken.ACCENT_TEXT, drop_priority=1),
            Element(Role.DETAIL, "P1", Emphasis.PLATE, color=ColorToken.ACCENT_TEXT, drop_priority=5),
        ),
        align="left",
        gap=38,
    ),
    Group(
        Anchor.MIDDLE_CENTER,
        Flow.ROW,
        (
            Element(Role.HEADLINE, "6.42", unit="HF", drop_priority=6),
            Element(Role.HEADLINE, "10.40s", drop_priority=7),
        ),
        align="left",
        gap=160,
    ),
    Group(Anchor.MIDDLE_CENTER, Flow.ROW, (Element(Role.LABEL, "Splits"),), align="left", margin_top=22),
    Group(
        Anchor.MIDDLE_CENTER,
        Flow.GRID,
        (
            Element(Role.HEADLINE, "0.23", caption="Best"),
            Element(Role.HEADLINE, "0.24", caption="Avg"),
            Element(Role.HEADLINE, "0.25", caption="Worst"),
            Element(Role.HEADLINE, "1.10", caption="Draw"),
        ),
        align="left",
        gap=80,
    ),
)

#: ``_group_div`` of the pinned Splits grid at fc0d6acd: the only markup a
#: summary row change could move. Identical groups plus identical markup
#: for them is identical pixels.
_PINNED_SPLITS_GRID_HTML = (
    '<div class="group flow-grid" style="grid-template-columns: repeat(4, 1fr); '
    'gap: calc(var(--fit-scale, 1) * 80px)">'
    '<div class="el"><span class="caption">Best</span>'
    '<span class="value role-headline emphasis-plain">0.23</span></div>'
    '<div class="el"><span class="caption">Avg</span>'
    '<span class="value role-headline emphasis-plain">0.24</span></div>'
    '<div class="el"><span class="caption">Worst</span>'
    '<span class="value role-headline emphasis-plain">0.25</span></div>'
    '<div class="el"><span class="caption">Draw</span>'
    '<span class="value role-headline emphasis-plain">1.10</span></div>'
    "</div>"
)


@pytest.mark.parametrize("events", [None, [], [_AUTO_RELOAD, {**_MOVE_1, "source": "auto"}]])
def test_a_stage_without_confirmed_regions_declares_exactly_what_it_did_before(
    tmp_path: Path, events: list[dict] | None
) -> None:
    """No regions, an empty list, and auto proposals only all declare the
    pre-change groups (Review Focus 1: an auto proposal renders as none)."""
    tile = _events_tile(_audit(tmp_path, events, shots=_UNTOUCHED_SHOTS))
    assert _groups(tile) == _PINNED_UNTOUCHED


def test_the_untouched_splits_grid_markup_is_unchanged() -> None:
    from splitsmith.overlay_html import _group_div

    assert _group_div(_PINNED_UNTOUCHED[-1]) == _PINNED_SPLITS_GRID_HTML


def _without_priorities(groups: tuple[Group, ...]) -> list[Group]:
    return [replace(g, elements=tuple(replace(e, drop_priority=None) for e in g.elements)) for g in groups]


def test_a_confirmed_reload_adds_exactly_the_reload_row(tmp_path: Path) -> None:
    """One row more, on the Splits band's four columns; everything else is
    declared as before but for the drop order (pinned below)."""
    tile = _events_tile(_audit(tmp_path, [_MOVE_2, _RELOAD]))
    without = _events_tile(_audit(tmp_path, [_MOVE_2]))
    groups, base = _groups(tile), _groups(without)
    assert _splits_rows(groups)[-1] == [("Reloads", "1"), ("Reload avg", "1.42"), ("Exposed", "0.31")]
    assert groups[-1].columns == 4 and groups[-1].flow is Flow.GRID
    # The Best/Avg/Worst/Draw row above it is the same row, now on the
    # band's shared four columns.
    assert groups[-2].elements == base[-1].elements and groups[-2].columns == 4
    assert _without_priorities(groups[:-2]) == _without_priorities(base[:-1])


def test_the_reload_row_drops_after_the_unlit_faults_and_before_the_rest(tmp_path: Path) -> None:
    """A cell too small for everything gives up the unlit faults, then the
    reload row (exposed first, count last), then the scoring it did before;
    the split figures never carry a priority."""
    groups = _groups(_events_tile(_audit(tmp_path, [_MOVE_2, _RELOAD])))
    order = sorted(
        (e.drop_priority, e.text) for g in groups for e in g.elements if e.drop_priority is not None
    )
    assert [text for _, text in order] == [
        "M0", "NS0", "0.31", "1.42", "1", "A20", "C3", "D1", "P1", "6.42", "10.40s", "Scoring",
    ]  # fmt: skip
    assert [p for p, _ in order] == list(range(len(order)))


def test_an_auto_reload_adds_no_row(tmp_path: Path) -> None:
    auto = _events_tile(_audit(tmp_path, [_MOVE_2, {**_RELOAD, "source": "auto"}]))
    none = _events_tile(_audit(tmp_path, [_MOVE_2]))
    assert _groups(auto) == _groups(none)


def test_a_standing_reload_is_exposed_for_its_whole_duration(tmp_path: Path) -> None:
    """A standing reload cost all of its time: the row draws Exposed, the
    reload's full duration, unsigned (never omitted, never ``+0.00``)."""
    reload_ = {**_RELOAD, "start": 9.0, "end": 9.5}
    rows = _splits_rows(_groups(_events_tile(_audit(tmp_path, [reload_]))))
    assert rows[-1] == [("Reloads", "1"), ("Reload avg", "0.50"), ("Exposed", "0.50")]


def test_a_reload_hidden_inside_its_movement_is_exposed_for_none(tmp_path: Path) -> None:
    """A reload finished inside its movement cost nothing: 0.00."""
    hidden = {**_RELOAD, "start": 7.7, "end": 9.0}
    rows = _splits_rows(_groups(_events_tile(_audit(tmp_path, [_MOVE_2, hidden]))))
    assert rows[-1] == [("Reloads", "1"), ("Reload avg", "1.30"), ("Exposed", "0.00")]


def test_static_and_moving_splits_become_two_rows(tmp_path: Path) -> None:
    rows = _splits_rows(_groups(_events_tile(_audit(tmp_path, [_MOVE_1]))))
    assert rows == [
        [(None, "Static"), ("Best", "0.22"), ("Avg", "0.24"), ("Worst", "0.27")],
        [(None, "Moving"), (None, "0.31"), (None, "0.32"), (None, "0.32")],
        [("Draw", "1.10")],
    ]


def test_static_moving_and_a_reload_share_the_last_row_with_the_draw(tmp_path: Path) -> None:
    rows = _splits_rows(_groups(_events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD, _AUTO_RELOAD]))))
    assert rows == [
        [(None, "Static"), ("Best", "0.22"), ("Avg", "0.24"), ("Worst", "0.27")],
        [(None, "Moving"), (None, "0.31"), (None, "0.32"), (None, "0.32")],
        [("Draw", "1.10"), ("Reloads", "1"), ("Reload avg", "1.42"), ("Exposed", "0.31")],
    ]


def test_a_short_cell_keeps_one_split_row_and_the_reload_row(tmp_path: Path) -> None:
    """A 640x360 card cannot fit separate Static and Moving rows without
    the fit dropping the Scoring figures, so it keeps
    the one Best/Avg/Worst/Draw row over every split; the reload row stays
    (it is the first thing such a cell may drop)."""
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    scale = cell.summary_scale(360)
    short = cell.summary_groups(tile, "Me", scale=scale, cell_width=640, cell_height=360)
    assert _splits_rows(short) == [
        [("Best", "0.22"), ("Avg", "0.26"), ("Worst", "0.32"), ("Draw", "1.10")],
        [("Reloads", "1"), ("Reload avg", "1.42"), ("Exposed", "0.31")],
    ]
    tall = cell.summary_groups(tile, "Me", scale=cell.summary_scale(480), cell_width=853, cell_height=480)
    assert [row[0] for row in _splits_rows(tall)[:2]] == [(None, "Static"), (None, "Moving")]


def test_only_moving_splits_keep_one_row(tmp_path: Path) -> None:
    """Every split on the move: there is nothing to compare, so the row
    stays the plain Best/Avg/Worst/Draw over the same splits."""
    shots = ((1.0, "first_shot"), (1.3, "split"), (1.62, "split"))
    move = {**_MOVE_1, "start": 1.05, "end": 2.0}
    moving = _groups(_events_tile(_audit(tmp_path, [move], shots=shots)))
    plain = _groups(_events_tile(_audit(tmp_path, None, shots=shots)))
    assert moving == plain
    assert _splits_rows(moving) == [[("Best", "0.30"), ("Avg", "0.31"), ("Worst", "0.32"), ("Draw", "1.00")]]


def test_the_split_rows_partition_exactly_the_statistic_splits(tmp_path: Path) -> None:
    """The Static and Moving rows are ``statistic_splits``' own selection,
    split by ``moving``: a movement-classed interval on the move is not a
    moving split, and nothing outside the selection reaches either row."""
    from splitsmith.coach import statistic_splits

    tile = _events_tile(_audit(tmp_path, [_MOVE_1]))
    static = [s.split for s in tile.shots if s.interval_class == "split" and not s.moving]
    moving = [s.split for s in tile.shots if s.interval_class == "split" and s.moving]
    assert sorted(static + moving) == sorted(statistic_splits(tile.shots))

    def stats(xs: list[float]) -> list[str]:
        return [f"{min(xs):.2f}", f"{sum(xs) / len(xs):.2f}", f"{max(xs):.2f}"]

    static_row, moving_row, _draw = _splits_rows(_groups(tile))
    assert [text for _, text in static_row[1:]] == stats(static)
    assert [text for _, text in moving_row[1:]] == stats(moving)


def test_the_grid_hold_keeps_one_comparable_split_row(tmp_path: Path) -> None:
    """Grid cells compare shooters, so every cell keeps the combined
    Best/Avg/Worst/Draw row whatever its size; a marked-up shooter still
    gains the reload row. The single-shooter card at the same size splits."""
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    grid = cell.summary_groups(
        tile, "Me", scale=cell.summary_scale(540), cell_width=960, cell_height=540, split_rows=False
    )
    assert _splits_rows(grid) == [
        [("Best", "0.22"), ("Avg", "0.26"), ("Worst", "0.32"), ("Draw", "1.10")],
        [("Reloads", "1"), ("Reload avg", "1.42"), ("Exposed", "0.31")],
    ]
    single = cell.summary_groups(tile, "Me", scale=cell.summary_scale(540), cell_width=960, cell_height=540)
    assert _splits_rows(single)[0][0] == (None, "Static")


def test_the_grid_hold_still_declares_no_static_or_moving_row(tmp_path: Path) -> None:
    from splitsmith.compare import overlay_summary as grid
    from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement

    fake = _FakeRasterizer()
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    grid.build_hold_still(
        [TilePlacement(label="Me", row=0, col=0, present=True)],
        {"Me": tile},
        {},
        SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=1, cols=1),
        theme=THEME,
        rasterizer=fake,
    )
    (html,) = fake.calls
    assert ">Static<" not in html and ">Moving<" not in html
    assert ">Reloads<" in html and ">Exposed<" in html and ">0.31<" in html


@pytest.mark.parametrize(("width", "height"), [(1080, 1920), (720, 1280), (1080, 1081)])
def test_a_narrow_card_keeps_one_split_row_and_the_reload_row_on_its_own(
    tmp_path: Path, width: int, height: int
) -> None:
    """A portrait card has too little width for a fourth column of figures
    beside a row label: the Draw would clip beside the reload count and
    read as one number. It keeps the one-row layout, the reload row on its
    own line."""
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    groups = cell.summary_groups(
        tile, "Me", scale=cell.summary_scale(height), cell_width=width, cell_height=height
    )
    assert _splits_rows(groups) == [
        [("Best", "0.22"), ("Avg", "0.26"), ("Worst", "0.32"), ("Draw", "1.10")],
        [("Reloads", "1"), ("Reload avg", "1.42"), ("Exposed", "0.31")],
    ]


def test_a_square_card_splits(tmp_path: Path) -> None:
    tile = _events_tile(_audit(tmp_path, [_MOVE_1]))
    groups = cell.summary_groups(
        tile, "Me", scale=cell.summary_scale(1080), cell_width=1080, cell_height=1080
    )
    assert _splits_rows(groups)[0][0] == (None, "Static")


def test_an_unclassified_stage_partitions_the_threshold_fallback(tmp_path: Path) -> None:
    """No classes at all: ``statistic_splits`` falls back to the split
    threshold (index 0 is the draw, a gap over the cutoff is no split),
    and the two rows partition that same selection."""
    shots = (
        TileShot(1.0, 1.0, moving=False),
        TileShot(1.2, 0.2, moving=False),
        TileShot(3.0, 1.8, moving=True),
        TileShot(3.3, 0.3, moving=True),
    )
    tile = TileStageData(label="Me", stage_number=1, shots=shots)
    rows = _splits_rows(_groups(tile))
    assert rows == [
        [(None, "Static"), ("Best", "0.20"), ("Avg", "0.20"), ("Worst", "0.20")],
        [(None, "Moving"), (None, "0.30"), (None, "0.30"), (None, "0.30")],
        [("Draw", "1.00")],
    ]


def test_shots_are_moving_only_inside_a_confirmed_movement(tmp_path: Path) -> None:
    confirmed = load_stage_shots(_audit(tmp_path, [_MOVE_1]))
    auto = load_stage_shots(_audit(tmp_path, [{**_MOVE_1, "source": "auto"}]))
    assert [s.moving for s in confirmed] == [False] * 5 + [True] * 3 + [False] * 4
    assert not any(s.moving for s in auto)


def test_reloads_load_from_confirmed_regions_only(tmp_path: Path) -> None:
    (fig,) = load_stage_reloads(_audit(tmp_path, [_MOVE_2, _RELOAD, _AUTO_RELOAD]))
    assert fig.event_id == "evt-3"
    assert fig.duration == pytest.approx(1.42)
    assert fig.exposed == pytest.approx(0.31)
    assert load_stage_reloads(_audit(tmp_path, None)) == ()


def test_a_corrupt_events_list_keeps_the_shots_and_draws_no_regions(tmp_path: Path) -> None:
    bad = {"id": "evt-1", "kind": "teleport", "start": 1, "end": 2, "source": "manual"}
    path = _audit(tmp_path, [_MOVE_1, bad])
    shots = load_stage_shots(path)
    assert len(shots) == len(_EVENT_SHOTS)
    assert not any(s.moving for s in shots)
    assert load_stage_reloads(path) == ()
    assert load_stage_reloads(tmp_path / "missing.json") == ()


def test_the_rows_reach_the_rendered_html(tmp_path: Path) -> None:
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    html = single_html(_groups(tile), width=1920, height=1080, scale=cell.summary_scale(1080), theme=THEME)
    for text in (">Static<", ">Moving<", ">Reloads<", ">Reload avg<", ">Exposed<"):
        assert text in html


def test_a_short_row_keeps_the_bands_four_columns(tmp_path: Path) -> None:
    """The reload row under Best/Avg/Worst/Draw has three figures; it is
    laid out on the same four columns so its figures sit under the ones
    above, not stretched to thirds."""
    from splitsmith.overlay_html import _group_div

    groups = _groups(_events_tile(_audit(tmp_path, [_MOVE_2, _RELOAD])))
    reload_row = groups[-1]
    assert [e.caption for e in reload_row.elements] == ["Reloads", "Reload avg", "Exposed"]
    assert "grid-template-columns: repeat(4, 1fr)" in _group_div(reload_row)


# --- portrait columns: no figure is cut to its column (fit.js fitColumns) --

#: Every visible grid-row figure or caption whose text is wider than its
#: own column, or a caption that wrapped: what ``overflow: hidden`` would
#: cut to a plausible wrong figure ("1.4" for 1.42). Also any whose text
#: ends closer than half its caption's size to the next column in its row
#: (measured independently of fit.js's own 0.6 em): "Reload avg" against
#: "Exposed" reads as one phrase.
_COLUMN_OVERFLOWS_JS = """() => {
  const out = [];
  const visible = (e) => getComputedStyle(e).display !== 'none';
  document.querySelectorAll('.group.flow-grid > .el').forEach((el) => {
    if (!visible(el)) { return; }
    const box = el.getBoundingClientRect();
    let next = el.nextElementSibling;
    while (next && !visible(next)) { next = next.nextElementSibling; }
    const nextBox = next ? next.getBoundingClientRect() : null;
    const sameRow = nextBox && Math.abs(nextBox.top - box.top) < 0.5 && nextBox.left > box.left;
    const caption = el.querySelector('.caption');
    Array.from(el.children).forEach((child) => {
      const range = document.createRange();
      range.selectNodeContents(child);
      const rect = range.getBoundingClientRect();
      const wrapped = child.classList.contains('caption') && range.getClientRects().length > 1;
      const em = parseFloat(getComputedStyle(caption || child).fontSize);
      const crowded = sameRow && nextBox.left - rect.right < 0.5 * em;
      if (rect.width > box.width + 0.5 || wrapped || crowded) { out.push(child.textContent); }
    });
  });
  return out;
}"""


def _column_overflows(html: str, *, width: int, height: int, tmp_path: Path) -> list[str]:
    """Lay ``html`` out in the real browser exactly as ``ChromiumRasterizer.png``
    does (file URL, fonts ready, the fit policy run), then measure."""
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    page_path = tmp_path / "summary.html"
    page_path.write_text(html, encoding="utf-8")
    try:
        with ChromiumRasterizer() as rasterizer:
            context = rasterizer._live_browser().new_context(viewport={"width": width, "height": height})
            try:
                page = context.new_page()
                page.goto(page_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                return page.evaluate(_COLUMN_OVERFLOWS_JS)
            finally:
                context.close()
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), [(1080, 1920), (720, 1280)])
@pytest.mark.parametrize("events", [None, [_MOVE_1, _MOVE_2, _RELOAD], [_RELOAD]])
def test_a_portrait_card_draws_every_figure_whole(
    tmp_path: Path, width: int, height: int, events: list[dict] | None
) -> None:
    """A portrait card's quarter columns are narrower than a headline
    figure at full size: the card shrinks until each figure fits its own
    column, rather than cutting 1.42 to "1.4" and +0.31 to "+0.", and
    keeps a gap before the next column, so a standing reload's "Reload avg"
    and "Exposed" never read as one caption. The HTML is the one
    ``build_summary_still`` hands the rasterizer."""
    fake = _FakeRasterizer()
    tile = _events_tile(_audit(tmp_path, events))
    cell.build_summary_still(
        tile, "Me", width=width, height=height, theme=THEME, rasterizer=fake, backdrop=None
    )
    (html,) = fake.calls
    assert _column_overflows(html, width=width, height=height, tmp_path=tmp_path) == []


def test_the_stage_summary_asks_for_the_column_fit_and_the_live_race_does_not(tmp_path: Path) -> None:
    """The column step is opt-in per document: the single card and the
    grid hold ask for it; the live sprites, whose rows change text frame
    to frame, keep the old policy."""
    from splitsmith.compare import overlay_summary as grid
    from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
    from splitsmith.overlay_html import grid_html

    flag = "window.__splitsmithFitColumns = true;"
    single, hold = _FakeRasterizer(), _FakeRasterizer()
    tile = _events_tile(_audit(tmp_path, [_RELOAD]))
    cell.build_summary_still(
        tile, "Me", width=1080, height=1920, theme=THEME, rasterizer=single, backdrop=None
    )
    geometry = SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=1, cols=1)
    grid.build_hold_still(
        [TilePlacement(label="Me", row=0, col=0, present=True)],
        {"Me": tile},
        {},
        geometry,
        theme=THEME,
        rasterizer=hold,
    )
    assert flag in single.calls[0] and flag in hold.calls[0]
    assert flag not in grid_html([], geometry=geometry, scale=cell.summary_scale(1080), theme=THEME)
