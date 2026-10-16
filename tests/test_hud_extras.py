"""Opt-in HUD data: ammo, bursts, scoring spreads, and the opt-in itself
(spec 2026-10-16-freedoom-hud-design)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import looks
from splitsmith.config import StageEvent
from splitsmith.hud_extras import (
    SPREADS,
    HudExtras,
    ammo_plan,
    bursts,
    declared_data,
    extras_identity,
    hud_extras_data,
    scoring_data,
    spread,
)
from splitsmith.match_project import StageScorecard
from splitsmith.overlay_hud import HudOptions, overlay_scoring_identity, overlay_settings
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
    were: they get no extras at all."""
    templates = sorted(looks.shipped_looks_dir().rglob("*.html"))
    assert templates
    for template in templates:
        assert declared_data(template) == (), template.name


def test_nothing_declared_adds_no_key() -> None:
    shots = _shots(("first_shot", 1.0), ("split", 0.2))
    assert hud_extras_data((), shots, beep_in_clip=1.0, events=(), extras=HudExtras(capacity=15)) == {}


# -- ammo ----------------------------------------------------------------


def test_ammo_counts_down_from_a_full_magazine_plus_one() -> None:
    plan = ammo_plan(
        _shots(("first_shot", 1.0), ("split", 0.2), ("split", 0.2)), beep_in_clip=1.0, events=(), capacity=3
    )
    assert plan == {"capacity": 3, "start": 4, "per_shot": [3, 2, 1], "refills": [], "ok": True}


def test_a_tactical_reload_keeps_the_chambered_round() -> None:
    shots = _shots(("first_shot", 1.0), ("split", 0.2), ("reload", 2.0), ("split", 0.2))
    plan = ammo_plan(shots, beep_in_clip=1.0, events=[_reload(1.4, 2.9)], capacity=3)
    assert plan is not None
    # 4 -> 3, 2; reload with rounds left: back to 4; then 3, 2.
    assert plan["per_shot"] == [3, 2, 3, 2]
    assert plan["refills"] == [{"t": 3.9, "rounds": 4}]


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
    assert plan is not None and plan["refills"] == [{"t": 3.0, "rounds": 6}]


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


def test_a_record_without_scoring_is_what_it_always_was() -> None:
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
    assert "scoring" not in overlay_settings(**kwargs)  # type: ignore[arg-type]
    assert overlay_settings(**kwargs, scoring="s")["scoring"] == "s"  # type: ignore[arg-type]


def test_scoring_identity_is_none_for_classic_and_for_shipped_templates() -> None:
    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    assert overlay_scoring_identity("splitsmith", "default", extras) is None
    assert overlay_scoring_identity("splitsmith", "plate", extras) is None
    assert overlay_scoring_identity("splitsmith", "no-such-style", extras) is None


@pytest.fixture
def doom_look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user Look whose overlay template declares every key."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "doom"
    manifest["slots"] = {"overlay": {"doom": "hud-doom.html"}}
    root = tmp_path / "looks" / "doom"
    root.mkdir(parents=True)
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "hud-doom.html").write_text(
        '<!doctype html><meta name="splitsmith-data" content="scoring,ammo,bursts">', encoding="utf-8"
    )
    return root


def test_a_declaring_template_records_a_scoring_identity(doom_look: Path) -> None:
    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    identity = overlay_scoring_identity("doom", "doom", extras)
    assert identity == extras_identity(("scoring", "ammo", "bursts"), extras)
    rescored = HudExtras(scorecard=_card(alphas=9, misses=1), capacity=15)
    assert overlay_scoring_identity("doom", "doom", rescored) != identity


def test_a_rescored_stage_does_not_reuse_its_overlay(doom_look: Path) -> None:
    """The CLI's and the MCP tool's reuse check (no ``wanted``): the record
    must have been drawn with the scorecard as it stands."""
    from splitsmith.ui.exports import overlay_record_matches

    extras = HudExtras(scorecard=_card(alphas=10), capacity=15)
    record = overlay_settings(
        look="doom",
        variant="doom",
        options=HudOptions(),
        codec="auto",
        max_height=None,
        max_fps=None,
        audit_revision="r1",
        scoring=overlay_scoring_identity("doom", "doom", extras),
    )
    assert overlay_record_matches(record, audit_revision="r1", hud_extras=extras)
    rescored = HudExtras(scorecard=_card(alphas=9, misses=1), capacity=15)
    assert not overlay_record_matches(record, audit_revision="r1", hud_extras=rescored)
    assert not overlay_record_matches(record, audit_revision="r1"), "no extras is not this scorecard"
