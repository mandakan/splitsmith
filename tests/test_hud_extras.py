"""Opt-in HUD data: ammo, bursts, scoring spreads, and the opt-in itself
(spec 2026-10-16-hud-scoring-data-design)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import looks
from splitsmith.config import StageEvent
from splitsmith.hud_extras import (
    PROVIDERS,
    SPREADS,
    HudDataProvider,
    HudExtras,
    HudInputs,
    ammo_plan,
    bursts,
    declared_data,
    extras_identity,
    hud_extras_data,
    register,
    scoring_data,
    spread,
)
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_hud import HudOptions, overlay_data_identity, overlay_settings
from splitsmith.stage_summary_data import TileShot


def _shots(*gaps: tuple[str | None, float]) -> list[TileShot]:
    out: list[TileShot] = []
    t = 0.0
    for cls, split in gaps:
        t = round(t + split, 6)
        out.append(TileShot(time_from_beep=t, split=split, interval_class=cls))  # type: ignore[arg-type]
    return out


def _reload(start: float, end: float, *, source: str = "manual", n: int = 1) -> StageEvent:
    return StageEvent(id=f"evt-{n}", kind="reload", start=start, end=end, source=source)  # type: ignore[arg-type]


def _card(**counts: int) -> StageScorecard:
    return StageScorecard(hit_factor=5.0, stage_points=90, stage_pct=80.0, **counts)


# -- declared_data -------------------------------------------------------


def test_declared_data_reads_the_meta_tag_in_key_order(tmp_path: Path) -> None:
    t = tmp_path / "hud.html"
    t.write_text('<meta name="splitsmith-data" content="bursts, nonsense ,scoring">', encoding="utf-8")
    assert declared_data(t) == ("scoring", "bursts")


def test_a_template_without_the_tag_declares_nothing(tmp_path: Path) -> None:
    t = tmp_path / "hud.html"
    t.write_text("<!doctype html>", encoding="utf-8")
    assert declared_data(t) == ()
    assert declared_data(tmp_path / "missing.html") == ()


def test_no_shipped_template_declares_a_key() -> None:
    """What keeps every shipped HUD's context, digest and cached MOV as they
    were: they get no extras at all. The broadcast Look is the reference
    for every piece of data a template can read and opts in on purpose."""
    # The starters are not shipped Looks: they are copied into a Look of your own.
    templates = sorted(
        t
        for t in looks.shipped_looks_dir().rglob("*.html")
        if "_starters" not in t.parts and "broadcast" not in t.parts
    )
    assert templates
    for template in templates:
        assert declared_data(template) == (), template.name
    broadcast = looks.shipped_looks_dir() / "broadcast"
    declaring = {t.name: declared_data(t) for t in broadcast.glob("*.html") if declared_data(t)}
    assert declaring == {
        "broadcast-hud.html": ("scoring", "ammo", "bursts"),
        "broadcast-summary.html": ("scoring", "ammo", "bursts"),
    }


def test_nothing_declared_adds_no_key() -> None:
    shots = _shots(("first_shot", 1.0), ("split", 0.2))
    assert hud_extras_data((), shots, beep_in_clip=1.0, events=(), extras=HudExtras(capacity=15)) == {}


# -- ammo ----------------------------------------------------------------


def test_ammo_counts_down_from_a_full_magazine_plus_one() -> None:
    plan = ammo_plan(
        _shots(("first_shot", 1.0), ("split", 0.2), ("split", 0.2)), beep_in_clip=1.0, events=(), capacity=3
    )
    assert plan == {"capacity": 3, "start": 4, "per_shot": [3, 2, 1], "refills": [], "ok": True}


def test_a_shot_at_the_reloads_start_was_fired_before_it() -> None:
    """A seeded reload spans its whole gap, so it starts on the magazine's
    last shot: that shot counts down the old magazine, and the counter
    shows what is left until the new one seats."""
    shots = _shots(("first_shot", 1.0), ("split", 0.2), ("reload", 2.0), ("split", 0.2))
    plan = ammo_plan(shots, beep_in_clip=0.0, events=[_reload(1.2, 3.2)], capacity=3)
    assert plan is not None
    assert plan["per_shot"] == [3, 2, 2, 1]
    assert plan["refills"] == [{"t": 3.2, "rounds": 3}]


def test_a_reload_with_rounds_left_refills_to_the_capacity() -> None:
    """Only the first magazine, on a stage that starts loaded, holds one
    more than the capacity: a reload seats the capacity, whatever was left."""
    shots = _shots(("first_shot", 1.0), ("split", 0.2), ("reload", 2.0), ("split", 0.2))
    plan = ammo_plan(shots, beep_in_clip=1.0, events=[_reload(1.4, 2.9)], capacity=3)
    assert plan is not None
    # 4 -> 3, 2; the reload seats 3; then 2, 1.
    assert plan["per_shot"] == [3, 2, 2, 1]
    assert plan["refills"] == [{"t": 3.9, "rounds": 3}]


def test_a_slide_lock_reload_refills_to_the_capacity() -> None:
    shots = _shots(("first_shot", 1.0), ("split", 0.2), ("reload", 2.0), ("split", 0.2))
    plan = ammo_plan(shots, beep_in_clip=0.0, events=[_reload(1.4, 2.9)], capacity=1)
    assert plan is not None
    assert plan["per_shot"] == [1, 0, 0, -1]
    assert plan["refills"] == [{"t": 2.9, "rounds": 1}]
    assert plan["ok"] is False, "one magazine of one plus one cannot fire four"


def test_a_missing_reload_goes_negative_rather_than_clamping() -> None:
    shots = _shots(*([("first_shot", 1.0)] + [("split", 0.2)] * 4))
    plan = ammo_plan(shots, beep_in_clip=0.0, events=(), capacity=2)
    assert plan is not None
    assert plan["per_shot"] == [2, 1, 0, -1, -2]
    assert plan["ok"] is False


def test_a_proposal_never_refills() -> None:
    shots = _shots(("first_shot", 1.0), ("reload", 2.0))
    plan = ammo_plan(shots, beep_in_clip=0.0, events=[_reload(1.2, 2.8, source="auto")], capacity=5)
    assert plan is not None and plan["refills"] == [] and plan["per_shot"] == [5, 4]


def test_a_reload_after_the_last_shot_still_seats_a_magazine() -> None:
    plan = ammo_plan(_shots(("first_shot", 1.0)), beep_in_clip=0.0, events=[_reload(2.0, 3.0)], capacity=5)
    assert plan is not None and plan["refills"] == [{"t": 3.0, "rounds": 5}]


def test_no_capacity_is_no_ammo() -> None:
    shots = _shots(("first_shot", 1.0))
    assert ammo_plan(shots, beep_in_clip=0.0, events=(), capacity=None) is None
    assert hud_extras_data(("ammo",), shots, beep_in_clip=0.0, events=(), extras=None) == {"ammo": None}


# -- bursts --------------------------------------------------------------


def test_bursts_break_at_transitions_movements_and_reloads() -> None:
    shots = _shots(
        ("first_shot", 1.1),
        ("split", 0.2),
        ("split", 0.3),
        ("transition", 0.6),
        ("split", 0.25),
        ("movement", 1.8),
        ("reload", 2.2),
    )
    out = bursts(shots)
    assert out["shot_burst"] == [0, 0, 0, 1, 1, 2, 3]
    first, second, third, fourth = out["bursts"]
    assert (first["first"], first["last"], first["shots"]) == (0, 2, 3)
    assert first["mean_split"] == pytest.approx(0.25) and first["min_split"] == pytest.approx(0.2)
    assert second["mean_split"] == pytest.approx(0.25)
    assert third["mean_split"] is None and fourth["min_split"] is None


def test_an_unclassified_gap_breaks_a_burst_over_the_split_cutoff() -> None:
    out = bursts(_shots((None, 1.0), (None, 0.3), (None, 1.6), (None, 0.2)))
    assert out["shot_burst"] == [0, 0, 1, 1]


# -- spreads -------------------------------------------------------------


_STAGE = _shots(
    ("first_shot", 1.2),
    ("split", 0.30),
    ("split", 0.18),
    ("transition", 0.70),
    ("split", 0.22),
    ("split", 0.40),
    ("movement", 1.60),
    ("split", 0.25),
)


@pytest.mark.parametrize("name", SPREADS)
def test_every_spread_ends_on_the_scorecard(name: str) -> None:
    card = _card(alphas=10, charlies=3, deltas=1, misses=1, no_shoots=1)
    out = spread(name, _STAGE, [False] * len(_STAGE), card)
    assert len(out) == len(_STAGE)
    assert out[-1]["totals"] == {"A": 10, "C": 3, "D": 1, "M": 1, "NS": 1}
    assert sum(len(entry["hits"]) for entry in out) == 15
    # Fifteen hits over eight shots: every shot takes one or two.
    assert {len(entry["hits"]) for entry in out} == {1, 2}
    assert out == spread(name, _STAGE, [False] * len(_STAGE), card), "deterministic"


def test_fewer_hits_than_shots_leaves_puffs() -> None:
    out = spread("even", _STAGE, [False] * len(_STAGE), _card(alphas=3, misses=1))
    assert sum(1 for entry in out if not entry["hits"]) == len(_STAGE) - 4
    assert out[-1]["totals"]["A"] == 3 and out[-1]["totals"]["M"] == 1


def test_fast_puts_the_miss_on_the_fastest_split() -> None:
    out = spread("fast", _STAGE, [False] * len(_STAGE), _card(alphas=7, misses=1))
    assert out[2]["hits"] == ["M"], "0.18 is the fastest split"


def test_end_puts_the_damage_on_the_last_shots() -> None:
    out = spread("end", _STAGE, [False] * len(_STAGE), _card(alphas=6, deltas=1, misses=1))
    assert [entry["hits"] for entry in out[-2:]] == [["D"], ["M"]]
    assert all(entry["hits"] == ["A"] for entry in out[:-2])


def test_moving_puts_the_damage_on_shots_fired_moving() -> None:
    moving = [False] * len(_STAGE)
    moving[5] = True
    out = spread("moving", _STAGE, moving, _card(alphas=7, misses=1))
    assert out[5]["hits"] == ["M"]


def test_even_spaces_the_bad_hits_apart() -> None:
    out = spread("even", _STAGE, [False] * len(_STAGE), _card(alphas=6, misses=2))
    misses = [i for i, entry in enumerate(out) if "M" in entry["hits"]]
    assert len(misses) == 2 and misses[1] - misses[0] >= 3


def test_no_shoots_are_capped_at_the_shot_count() -> None:
    shots = _STAGE[:2]
    out = spread("fast", shots, [False, False], _card(alphas=2, no_shoots=5))
    assert out[-1]["totals"]["NS"] == 2


def test_scoring_without_a_scorecard_carries_the_stage_only() -> None:
    extras = HudExtras(stage_name="Bay 3", stage_number=3, division="Production Optics")
    out = scoring_data(_STAGE, events=(), extras=extras)
    assert out["name"] == "Bay 3" and out["number"] == 3 and out["division"] == "Production Optics"
    assert out["hit_factor"] is None and out["totals"] is None and out["spreads"] is None


def test_scoring_reads_moving_from_confirmed_regions_only() -> None:
    region = StageEvent(id="evt-1", kind="movement", start=2.9, end=3.1, source="manual")
    proposal = StageEvent(id="evt-2", kind="movement", start=0.0, end=2.8, source="auto")
    extras = HudExtras(scorecard=_card(alphas=7, misses=1))
    out = scoring_data(_STAGE, events=[region, proposal], extras=extras)
    assert out["spreads"]["moving"][5]["hits"] == ["M"], "shot 6 at 3.0 s is inside the confirmed region"


# -- the record ----------------------------------------------------------


def test_extras_identity_is_none_without_keys_and_moves_with_the_scorecard() -> None:
    assert extras_identity((), HudExtras(capacity=15)) is None
    one = extras_identity(("scoring",), HudExtras(scorecard=_card(alphas=10)))
    other = extras_identity(("scoring",), HudExtras(scorecard=_card(alphas=9, charlies=1)))
    assert one and other and one != other
    assert extras_identity(("ammo",), HudExtras()) != extras_identity(("scoring",), HudExtras())


def test_a_record_without_hud_data_is_what_it_always_was() -> None:
    kwargs = dict(  # noqa: C408
        look="splitsmith",
        variant="plate",
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision="r1",
        template="t",
        theme="th",
    )
    assert "hud_data" not in overlay_settings(**kwargs)  # type: ignore[arg-type]
    assert overlay_settings(**kwargs, hud_data="s")["hud_data"] == "s"  # type: ignore[arg-type]


def test_scoring_identity_is_none_for_classic_and_for_shipped_templates() -> None:
    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    assert overlay_data_identity("splitsmith", "default", extras) is None
    assert overlay_data_identity("splitsmith", "plate", extras) is None
    assert overlay_data_identity("splitsmith", "no-such-style", extras) is None


@pytest.fixture
def retro_look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user Look whose overlay template declares every key."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "retro"
    manifest["slots"] = {"overlay": {"retro": "hud-retro.html"}}
    root = tmp_path / "looks" / "retro"
    root.mkdir(parents=True)
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "hud-retro.html").write_text(
        '<!doctype html><meta name="splitsmith-data" content="scoring,ammo,bursts">', encoding="utf-8"
    )
    return root


def test_a_declaring_template_records_a_scoring_identity(retro_look: Path) -> None:
    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    identity = overlay_data_identity("retro", "retro", extras)
    assert identity == extras_identity(("scoring", "ammo", "bursts"), extras)
    rescored = HudExtras(scorecard=_card(alphas=9, misses=1), capacity=15)
    assert overlay_data_identity("retro", "retro", rescored) != identity


def test_a_rescored_stage_does_not_reuse_its_overlay(retro_look: Path) -> None:
    """The CLI's and the MCP tool's reuse check (no ``wanted``): the record
    must have been drawn with the scorecard as it stands."""
    from splitsmith.ui.exports import overlay_record_matches

    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    record = overlay_settings(
        look="retro",
        variant="retro",
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision="r1",
        hud_data=overlay_data_identity("retro", "retro", extras),
    )
    assert overlay_record_matches(record, audit_revision="r1", hud_extras=extras)
    rescored = HudExtras(scorecard=_card(alphas=9, misses=1), capacity=15)
    assert not overlay_record_matches(record, audit_revision="r1", hud_extras=rescored)
    assert not overlay_record_matches(record, audit_revision="r1"), "no extras is not this scorecard"


# -- the registry --------------------------------------------------------


def test_the_registry_holds_the_three_keys_in_order() -> None:
    assert list(PROVIDERS) == ["scoring", "ammo", "bursts"]


@pytest.mark.parametrize("key", ["stage", "options", "safe_area", "Bad-Key", "scoring"])
def test_register_refuses_reserved_malformed_and_duplicate_keys(key: str) -> None:
    with pytest.raises(ValueError):
        register(HudDataProvider(key, 1, lambda _i: None))
    assert "Bad-Key" not in PROVIDERS


def test_a_new_provider_reaches_only_a_template_that_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(PROVIDERS, "shot_count", HudDataProvider("shot_count", 1, lambda i: len(i.shots)))
    template = tmp_path / "hud.html"
    template.write_text('<meta name="splitsmith-data" content="shot_count">', encoding="utf-8")
    keys = declared_data(template)
    assert keys == ("shot_count",)
    out = hud_extras_data(keys, _STAGE, beep_in_clip=1.0, events=(), extras=None)
    assert out == {"shot_count": len(_STAGE)}


def test_bumping_a_provider_version_moves_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    before = extras_identity(("bursts",), HudExtras())
    monkeypatch.setitem(PROVIDERS, "bursts", HudDataProvider("bursts", 2, PROVIDERS["bursts"].build))
    assert extras_identity(("bursts",), HudExtras()) != before


def test_providers_never_see_a_proposal() -> None:
    seen: list[list[str]] = []

    def build(inputs: HudInputs) -> None:
        seen.append([e.source for e in inputs.events])

    proposal = StageEvent(id="evt-1", kind="reload", start=1.0, end=2.0, source="auto")
    confirmed_one = StageEvent(id="evt-2", kind="reload", start=3.0, end=4.0, source="manual")
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(PROVIDERS, "probe", HudDataProvider("probe", 1, build))
        hud_extras_data(("probe",), _STAGE, beep_in_clip=0.0, events=[proposal, confirmed_one], extras=None)
    assert seen == [["manual"]]


# -- the resolver ----------------------------------------------------------


def test_hud_extras_for_resolves_the_division_as_breakdown_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project pinned before the division was stored: Breakdown's seeder
    finds it in the shooter directory's scoreboard files, so the HUD must
    too, or its ammo counter vanishes where Breakdown proposed reloads."""
    from splitsmith.match_project import MatchProject, StageEntry
    from splitsmith.ui import exports

    asked: list[Path | None] = []

    def resolver(project: MatchProject, project_dir: Path | None) -> str | None:
        asked.append(project_dir)
        return "Production Optics"

    monkeypatch.setattr(exports, "competitor_division", resolver)
    project = MatchProject(name="M")
    stage = StageEntry(stage_number=2, stage_name="Bay 2", time_seconds=10.0)
    extras = exports.hud_extras_for(project, stage, tmp_path)
    assert asked == [tmp_path]
    assert (extras.division, extras.capacity) == ("Production Optics", 15)
    assert extras.burst_split_max is not None
    assert extras.min_rounds is None, "no scoreboard rounds, no minimum"


def test_min_rounds_comes_from_the_scoreboard_and_reaches_scoring(tmp_path: Path) -> None:
    from splitsmith.config import StageRounds
    from splitsmith.match_project import MatchProject, StageEntry
    from splitsmith.ui import exports

    stage = StageEntry(
        stage_number=2,
        stage_name="Skolhuset",
        time_seconds=36.18,
        stage_rounds=StageRounds(expected=32, paper_targets=16, steel_targets=0),
    )
    extras = exports.hud_extras_for(MatchProject(name="M"), stage, tmp_path)
    assert extras.min_rounds == 32
    out = hud_extras_data(("scoring",), _STAGE, beep_in_clip=0.0, events=(), extras=extras)
    assert out["scoring"]["min_rounds"] == 32
    assert extras_identity(("scoring",), extras) != extras_identity(
        ("scoring",), extras.model_copy(update={"min_rounds": 30})
    )


def test_the_burst_cutoff_rides_the_extras_and_the_identity() -> None:
    shots = _shots((None, 1.0), (None, 0.3), (None, 0.8), (None, 0.2))
    keys = ("bursts",)
    loose = hud_extras_data(keys, shots, beep_in_clip=0.0, events=(), extras=HudExtras(burst_split_max=1.0))
    tight = hud_extras_data(keys, shots, beep_in_clip=0.0, events=(), extras=HudExtras(burst_split_max=0.5))
    assert loose["bursts"]["shot_burst"] == [0, 0, 0, 0]
    assert tight["bursts"]["shot_burst"] == [0, 0, 1, 1]
    assert extras_identity(keys, HudExtras(burst_split_max=1.0)) != extras_identity(
        keys, HudExtras(burst_split_max=0.5)
    )
