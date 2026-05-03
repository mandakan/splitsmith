"""Tests for ``LocalJsonScoreboard`` (offline ``ScoreboardClient``, issue #48).

The fixture under ``tests/fixtures/scoreboard/match_22_27190.json`` is a
real ``GET /api/v1/match/22/27190`` response captured for #47 -- the same
shape the upcoming ``SsiHttpClient`` will return online. Verifying that
``LocalJsonScoreboard`` round-trips it satisfies the #14 acceptance
criterion that both code paths produce an identical internal shape.

A ``socket.socket`` patch around the whole module guards the
"no network calls" criterion: any accidental connect() during tests will
raise.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

import pytest

from splitsmith.ui.scoreboard.local import LocalJsonScoreboard
from splitsmith.ui.scoreboard.models import MatchData, MatchRef, ShooterRef
from splitsmith.ui.scoreboard.protocol import ScoreboardClient

FIXTURE = Path(__file__).parent / "fixtures" / "scoreboard" / "match_22_27190.json"

# Captured from the fixture's ``ssi_url``.
FIXTURE_CT = 22
FIXTURE_ID = 27190


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any code path opens a socket during these tests."""

    def _refuse(*_args: object, **_kwargs: object) -> socket.socket:
        raise RuntimeError("network access not allowed in LocalJsonScoreboard tests")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.fixture
def client() -> LocalJsonScoreboard:
    return LocalJsonScoreboard(FIXTURE)


def test_satisfies_protocol(client: LocalJsonScoreboard) -> None:
    assert isinstance(client, ScoreboardClient)


def test_get_match_returns_parsed_match_data(client: LocalJsonScoreboard) -> None:
    match = client.get_match(FIXTURE_CT, FIXTURE_ID)
    assert isinstance(match, MatchData)
    assert match.name == "SPSK Open 2026"
    assert len(match.stages) == match.stages_count
    assert match.competitors, "fixture must have competitors"
    assert all(c.shooterId > 0 for c in match.competitors)


def test_get_match_round_trips_to_wire_shape(client: LocalJsonScoreboard) -> None:
    """The same JSON loaded online should equal the loaded-then-dumped local copy."""
    import json as _json

    raw = _json.loads(FIXTURE.read_text())
    dumped = client.get_match(FIXTURE_CT, FIXTURE_ID).model_dump(by_alias=True, exclude_none=False)
    # Strip keys the model defaulted-None for fields the wire didn't carry.
    present = {k: v for k, v in dumped.items() if not (v is None and k not in raw)}
    # Top-level scalar parity is enough for the round-trip claim; the deep
    # equality assertion lives in test_scoreboard_models.test_match_roundtrip.
    for key in ("name", "level", "discipline", "stages_count", "ssi_url"):
        assert present[key] == raw[key]


def test_get_match_rejects_mismatched_id(client: LocalJsonScoreboard) -> None:
    with pytest.raises(KeyError, match="local scoreboard holds"):
        client.get_match(FIXTURE_CT, FIXTURE_ID + 1)


def test_search_matches_finds_loaded_match(client: LocalJsonScoreboard) -> None:
    hits = client.search_matches("SPSK")
    assert len(hits) == 1
    ref = hits[0]
    assert isinstance(ref, MatchRef)
    assert ref.id == FIXTURE_ID
    assert ref.content_type == FIXTURE_CT
    assert ref.name == "SPSK Open 2026"


def test_search_matches_is_case_insensitive(client: LocalJsonScoreboard) -> None:
    assert client.search_matches("spsk")
    assert client.search_matches("OPEN")


def test_search_matches_empty_query_returns_match(client: LocalJsonScoreboard) -> None:
    """Empty query mirrors the online ``GET /api/v1/events`` "list everything" call."""
    assert len(client.search_matches("")) == 1


def test_search_matches_unknown_query_returns_empty(client: LocalJsonScoreboard) -> None:
    assert client.search_matches("absolutely-not-a-real-match") == []


def test_find_shooter_returns_matching_competitors(client: LocalJsonScoreboard) -> None:
    match = client.get_match(FIXTURE_CT, FIXTURE_ID)
    target = match.competitors[0]
    hits = client.find_shooter(target.name)
    assert hits, "should find the competitor by exact name"
    assert any(h.shooterId == target.shooterId for h in hits)
    assert all(isinstance(h, ShooterRef) for h in hits)


def test_find_shooter_substring_match(client: LocalJsonScoreboard) -> None:
    match = client.get_match(FIXTURE_CT, FIXTURE_ID)
    # Search for a single letter present in many names; ensures substring
    # matching, not equality, is what the implementation does.
    hits = client.find_shooter("a")
    assert (
        any(h.name == match.competitors[0].name for h in hits) or hits
    ), "substring search should return at least one competitor"


def test_find_shooter_unknown_name_returns_empty(client: LocalJsonScoreboard) -> None:
    assert client.find_shooter("zzzzz-not-a-real-shooter") == []


def test_find_shooter_empty_query_returns_empty(client: LocalJsonScoreboard) -> None:
    assert client.find_shooter("") == []
    assert client.find_shooter("   ") == []


def test_get_shooter_raises_in_offline_mode(client: LocalJsonScoreboard) -> None:
    with pytest.raises(NotImplementedError, match="shooter dashboard"):
        client.get_shooter(51842)


def test_from_project_resolves_conventional_path(tmp_path: Path) -> None:
    scoreboard_dir = tmp_path / "scoreboard"
    scoreboard_dir.mkdir()
    shutil.copy(FIXTURE, scoreboard_dir / "match.json")

    client = LocalJsonScoreboard.from_project(tmp_path)
    assert client.match.name == "SPSK Open 2026"


def test_constructor_fails_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalJsonScoreboard(tmp_path / "does-not-exist.json")
