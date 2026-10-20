"""The picker's "N / M stages audited" counts finished stages (#1613).

A stage is finished when at least one shooter audited it and every shooter
filmed on it is audited or skipped. A shooter with no footage on a stage has
no say there, the Overview's rule. The old integer average of per-shooter
counts over every shooter sank when a squadmate was added before their
footage and rose past the match's real progress when one shooter was far
ahead.
"""

from __future__ import annotations

from splitsmith.match_project import StageStatus as S
from splitsmith.ui import server

MATHIAS = {1: S.todo, 2: S.audited, 3: S.audited, 4: S.audited, 5: S.audited, 6: S.in_progress}
ANNA = {1: S.todo, 2: S.audited, 3: S.audited, 4: S.audited, 5: S.in_progress, 6: S.todo}
NOBODY = dict.fromkeys(range(1, 7), S.todo)


def test_two_shooters_count_the_stages_both_finished() -> None:
    assert server._stages_audited_across([("ma", MATHIAS), ("aj", ANNA)]) == 3


def test_a_squadmate_without_footage_changes_nothing() -> None:
    # Before the fix: (4 + 3 + 0) // 3 == 2, and stage 4 went yellow.
    assert server._stages_audited_across([("ma", MATHIAS), ("aj", ANNA), ("qt", NOBODY)]) == 3


def test_one_shooter_far_ahead_does_not_count_for_the_other() -> None:
    # Before the fix: (6 + 0) // 2 == 3 "audited" stages nobody finished together.
    ahead = dict.fromkeys(range(1, 7), S.audited)
    behind = dict.fromkeys(range(1, 7), S.ready)
    assert server._stages_audited_across([("a", ahead), ("b", behind)]) == 0


def test_a_skipped_stage_counts_as_done_for_that_shooter_only() -> None:
    assert server._stages_audited_across([("a", {1: S.audited}), ("b", {1: S.skipped})]) == 1
    # Everyone skipped it: nothing was audited, so it is not an audited stage.
    assert server._stages_audited_across([("a", {1: S.skipped}), ("b", {1: S.skipped})]) == 0


def test_the_exported_state_reads_the_walk_not_the_count() -> None:
    """A squadmate still waiting for footage keeps the match in progress even
    when every filmed stage is finished."""
    names = {1: "One", 2: "Two"}
    done = {1: S.audited, 2: S.audited}
    assert server._stages_audited_across([("a", done), ("b", NOBODY)]) == 2
    step = server._next_step_from_statuses([("a", done), ("b", {1: S.todo, 2: S.todo})], names, 2)
    assert step is not None and step.kind == "footage"
    assert server._nothing_left_to_do(step) is False
    assert server._nothing_left_to_do(server._next_step_from_statuses([("a", done)], names, 2)) is True
