from splitsmith.ui.scoreboard.reconcile import (
    CompetitorRef,
    LocalShooter,
    propose_shooter_links,
)


def test_exact_name_matches():
    local = [LocalShooter(slug="jl", name="Johan Larsson", division="Production Optics")]
    comps = [
        CompetitorRef(competitor_id=222, shooter_id=111, name="Johan Larsson", division="Production Optics")
    ]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 222 and p.shooter_id == 111 and not p.ambiguous


def test_case_and_order_insensitive():
    local = [LocalShooter(slug="jl", name="larsson johan", division=None)]
    comps = [CompetitorRef(competitor_id=5, shooter_id=9, name="Johan Larsson", division=None)]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 5


def test_no_match_leaves_unlinked():
    local = [LocalShooter(slug="x", name="Nobody Here", division=None)]
    comps = [CompetitorRef(competitor_id=1, shooter_id=2, name="Someone Else", division=None)]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id is None


def test_division_breaks_tie():
    local = [LocalShooter(slug="a", name="Sam Smith", division="Open")]
    comps = [
        CompetitorRef(competitor_id=1, shooter_id=1, name="Sam Smith", division="Production"),
        CompetitorRef(competitor_id=2, shooter_id=2, name="Sam Smith", division="Open"),
    ]
    [p] = propose_shooter_links(local, comps)
    assert p.competitor_id == 2
