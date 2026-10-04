"""The competitor's division on a generated title page: where it is
stored, where an older project finds it, and how it reaches the single
shooter's and the grid's title cards."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from splitsmith.compare.cards import CardOptions, bundle_divisions, title_cards
from splitsmith.compare.project_loader import CompareShooterBundle
from splitsmith.division import competitor_division, roster_lines
from splitsmith.match_model import Match
from splitsmith.match_project import MatchProject
from splitsmith.ui.match_exports import title_info_lines
from splitsmith.ui.scoreboard.cache import CachingScoreboardClient, read_cached_match
from splitsmith.ui.scoreboard.models import MatchData

FIXTURE = Path(__file__).parent / "fixtures" / "scoreboard" / "match_22_27190.json"
OPEN_MAJOR = 727542  # Jörgen Broman, "Open Major" in the fixture
PRODUCTION_OPTICS = 727539


def _match_data() -> MatchData:
    return MatchData.model_validate(json.loads(FIXTURE.read_text()))


def _pinned(competitor_id: int | None = OPEN_MAJOR, **kw: object) -> MatchProject:
    return MatchProject(
        name="Bromma",
        scoreboard_match_id="27190",
        scoreboard_content_type=22,
        selected_competitor_id=competitor_id,
        **kw,
    )


def _seed_cache(project_dir: Path) -> None:
    """Write the cache file exactly as ``CachingScoreboardClient`` does."""

    class _Once:
        def get_match(self, content_type: int, match_id: int) -> MatchData:
            return _match_data()

    cache = CachingScoreboardClient.for_project(_Once(), project_dir)  # type: ignore[arg-type]
    cache.get_match(22, 27190)


def test_merge_takes_the_pinned_competitors_division() -> None:
    project = _pinned()
    assert project.merge_competitor_division(_match_data()) is True
    assert project.competitor_division == "Open Major"
    assert project.merge_competitor_division(_match_data()) is False


def test_merge_follows_a_repin_and_ignores_an_unlisted_competitor() -> None:
    project = _pinned(competitor_division="Open Major", competitor_id=PRODUCTION_OPTICS)
    project.merge_competitor_division(_match_data())
    assert project.competitor_division == "Production Optics"
    gone = _pinned(competitor_division="Open Major", competitor_id=1)
    assert gone.merge_competitor_division(_match_data()) is False
    assert gone.competitor_division == "Open Major"


def test_stored_division_wins_without_touching_disk(tmp_path: Path) -> None:
    project = _pinned(competitor_division="Classic Major")
    assert competitor_division(project, tmp_path / "missing") == "Classic Major"


def test_an_older_project_reads_the_cached_match_and_never_the_network(tmp_path: Path) -> None:
    _seed_cache(tmp_path)
    assert read_cached_match(tmp_path, 22, 27190) is not None
    project = _pinned()
    assert competitor_division(project, tmp_path) == "Open Major"
    # Resolving does not write the project: an export reads, it never edits.
    assert project.competitor_division is None


def test_an_older_project_reads_a_dropped_match_json(tmp_path: Path) -> None:
    (tmp_path / "scoreboard").mkdir()
    shutil.copy(FIXTURE, tmp_path / "scoreboard" / "match.json")
    assert competitor_division(_pinned(), tmp_path) == "Open Major"


def test_no_scoreboard_means_no_division(tmp_path: Path) -> None:
    assert competitor_division(MatchProject(name="Bromma"), tmp_path) is None
    assert competitor_division(_pinned(), tmp_path) is None  # pinned, nothing cached
    assert competitor_division(_pinned(competitor_id=None), tmp_path) is None


def test_title_lines_put_the_division_under_the_name() -> None:
    project = MatchProject(name="Bromma", competitor_name="Martin Engström", match_date=date(2026, 10, 3))
    assert title_info_lines(project, extra="Level II", division="Classic Major") == (
        "2026-10-03",
        "Martin Engström",
        "Classic Major",
        "Level II",
    )
    assert title_info_lines(project, division=None) == ("2026-10-03", "Martin Engström")
    assert title_info_lines(project, division="  ") == ("2026-10-03", "Martin Engström")


def test_roster_lines_name_every_shooter_once_anyone_has_a_division() -> None:
    assert roster_lines([("Martin", "Classic Major"), ("Mathias", None)]) == (
        "Martin · Classic Major",
        "Mathias",
    )
    assert roster_lines([("Martin", None), ("Mathias", None)]) == ()
    assert roster_lines([]) == ()


def _grid_match() -> Match:
    return Match(name="Hugelsta Six Station", match_date=date(2026, 10, 3))


def test_grid_title_lists_shooters_in_slot_order_with_their_divisions(tmp_path: Path) -> None:
    bundles = [
        CompareShooterBundle(
            label="Mathias", project_root=tmp_path, project=_pinned(competitor_division="Production Optics")
        ),
        CompareShooterBundle(
            label="Martin", project_root=tmp_path, project=_pinned(competitor_division="Classic Major")
        ),
    ]
    divisions = bundle_divisions(bundles)
    assert divisions == [("Martin", "Classic Major"), ("Mathias", "Production Optics")]
    title, closing = title_cards(
        _grid_match(),
        CardOptions(title_page=True, closing_card=True, title_info="Level II"),
        divisions=divisions,
    )
    assert title is not None and closing is not None
    assert title.info == ("2026-10-03", "Martin · Classic Major", "Mathias · Production Optics", "Level II")
    assert closing.info == title.info


def test_grid_title_without_the_option_is_as_before(tmp_path: Path) -> None:
    divisions = [("Martin", "Classic Major")]
    title, _ = title_cards(
        _grid_match(), CardOptions(title_page=True, title_division=False), divisions=divisions
    )
    assert title is not None
    assert title.info == ("2026-10-03",)


def test_a_bundle_whose_project_cannot_be_read_has_no_division(tmp_path: Path) -> None:
    bundle = CompareShooterBundle(label="Ghost", project_root=tmp_path / "nowhere")
    assert bundle_divisions([bundle]) == [("Ghost", None)]
