import pytest

from splitsmith.highlights import StageReadiness, reel_unlocked, stage_readiness
from splitsmith.match_project import StageStatus


def _doc(*, seeded: bool = True, sources: tuple[str, ...] = ()) -> dict:
    doc: dict = {
        "events": [
            {"id": f"evt-{i}", "kind": "movement", "start": 1.0 + i, "end": 1.5 + i, "source": s}
            for i, s in enumerate(sources)
        ]
    }
    if seeded:
        doc["events_seeded"] = 4
    return doc


def test_audited_seeded_stage_without_proposals_is_ready():
    row = stage_readiness(1, StageStatus.audited, _doc(sources=("manual",)), has_footage=True)
    assert row == StageReadiness(1, "ready")


def test_auto_proposals_block_and_are_counted():
    row = stage_readiness(2, StageStatus.audited, _doc(sources=("auto", "manual", "auto")), has_footage=True)
    assert row == StageReadiness(2, "proposals_left", proposals=2)


def test_never_opened_in_breakdown_blocks():
    row = stage_readiness(3, StageStatus.audited, _doc(seeded=False), has_footage=True)
    assert row.state == "breakdown_not_opened"


def test_not_audited_blocks_before_breakdown_is_looked_at():
    row = stage_readiness(4, StageStatus.ready, None, has_footage=True)
    assert row.state == "not_audited"


def test_skipped_and_footageless_stages_do_not_block():
    rows = [
        stage_readiness(1, StageStatus.audited, _doc(), has_footage=True),
        stage_readiness(2, StageStatus.skipped, None, has_footage=False),
        stage_readiness(3, StageStatus.audited, _doc(), has_footage=False),
    ]
    assert [r.state for r in rows] == ["ready", "skipped", "no_footage"]
    assert reel_unlocked(rows)


def test_nothing_ready_is_not_unlocked():
    assert not reel_unlocked([stage_readiness(1, StageStatus.skipped, None, has_footage=False)])
    assert not reel_unlocked([])


def test_events_that_are_not_a_list_count_as_none():
    doc = {"events_seeded": 4, "events": "garbage"}
    assert stage_readiness(1, StageStatus.audited, doc, has_footage=True).state == "ready"
