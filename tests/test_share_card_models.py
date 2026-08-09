"""Card models: roster ordering and content hashing (spec 2026-08-09)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from splitsmith.share_card import (
    MatchCard,
    RosterEntry,
    StageCard,
    card_hash,
    stage_figures,
)


@dataclass(frozen=True)
class _Shot:
    """The two attributes ``coach.SplitStatInterval`` reads."""

    split: float
    interval_class: str | None


def _figs() -> object:
    return stage_figures(
        (
            _Shot(split=1.28, interval_class="first_shot"),
            _Shot(split=0.19, interval_class="split"),
        ),
    )


def _stage_card(stage_name: str = "Per told me to do it!") -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name=stage_name,
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=2,
        stage_time=14.74,
        figures=_figs(),
    )


def test_roster_is_sorted_alphabetically_by_name() -> None:
    card = MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[
            RosterEntry(name="Petra Lind", division="Standard"),
            RosterEntry(name="Anders Berg", division="Production Optics"),
            RosterEntry(name="Mathias Axell", division="Production Optics"),
        ],
    )
    assert [r.name for r in card.roster] == ["Anders Berg", "Mathias Axell", "Petra Lind"]


def test_match_card_is_frozen_against_attribute_assignment() -> None:
    card = MatchCard(match_name="Tallmilan 2026", match_date="2026-04-26", stage_count=7, roster=[])
    with pytest.raises(ValidationError):
        card.roster = [RosterEntry(name="Zoe"), RosterEntry(name="Bob")]


def test_model_validate_of_a_dumped_card_preserves_the_sort() -> None:
    card = MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[
            RosterEntry(name="Petra Lind", division="Standard"),
            RosterEntry(name="Anders Berg", division="Production Optics"),
            RosterEntry(name="Mathias Axell", division="Production Optics"),
        ],
    )
    dumped = card.model_dump(mode="json")
    # Scramble the order in the dumped payload so re-validation is the only
    # thing that could put it back -- a no-op re-sort on already-sorted data
    # wouldn't prove the validator ran.
    dumped["roster"] = list(reversed(dumped["roster"]))
    rebuilt = MatchCard.model_validate(dumped)
    assert [r.name for r in rebuilt.roster] == ["Anders Berg", "Mathias Axell", "Petra Lind"]


def test_hash_is_stable_across_equal_cards() -> None:
    assert card_hash(_stage_card()) == card_hash(_stage_card())


def test_hash_changes_when_any_displayed_figure_changes() -> None:
    before = card_hash(_stage_card())
    after = card_hash(_stage_card(stage_name="Short and Sweet"))
    assert before != after


def test_hash_changes_when_the_average_split_changes() -> None:
    base = _stage_card()
    moved = base.model_copy(
        update={
            "figures": stage_figures(
                (
                    _Shot(split=1.28, interval_class="first_shot"),
                    _Shot(split=0.31, interval_class="split"),
                ),
            )
        }
    )
    assert card_hash(base) != card_hash(moved)


def test_hash_is_sixteen_hex_characters() -> None:
    h = card_hash(_stage_card())
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
