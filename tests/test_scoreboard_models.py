"""Round-trip tests for ``splitsmith.ui.scoreboard`` models.

Each captured fixture under ``tests/fixtures/scoreboard/`` is a raw response
from ``scoreboard.urdr.dev/api/v1/``. We parse it through the Pydantic model
and re-dump it -- the result must equal the source for every key the model
exposes. Unknown keys are ignored on the way in, so we compare the dump
against a filtered view of the source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from splitsmith.ui.scoreboard.models import (
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
)
from splitsmith.ui.scoreboard.protocol import ScoreboardClient

FIXTURES = Path(__file__).parent / "fixtures" / "scoreboard"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _filter_by_dump(src: Any, dumped: Any) -> Any:
    """Return ``src`` restricted to the keys present in ``dumped``.

    Lets round-trip equality hold even though our models drop unknown
    server-side keys (forward-compat per the v1 versioning policy).
    """
    if isinstance(dumped, dict) and isinstance(src, dict):
        return {k: _filter_by_dump(src[k], dumped[k]) for k in dumped if k in src}
    if isinstance(dumped, list) and isinstance(src, list):
        return [_filter_by_dump(s, d) for s, d in zip(src, dumped, strict=True)]
    return src


def _roundtrip_list(model_cls: type, raw: list[Any]) -> None:
    parsed = [model_cls.model_validate(item) for item in raw]
    dumped = [m.model_dump(by_alias=True, exclude_none=False) for m in parsed]
    # exclude_none=False keeps explicit nulls; strip keys the model didn't
    # populate (i.e. defaulted to None when the source omitted them) so we
    # only compare what the wire actually sent.
    for src, out in zip(raw, dumped, strict=True):
        out_present = {k: v for k, v in out.items() if k in src}
        assert out_present == _filter_by_dump(src, out_present)


def test_events_roundtrip() -> None:
    raw = _load("events.json")
    assert isinstance(raw, list) and raw, "events fixture must be a non-empty array"
    _roundtrip_list(MatchRef, raw)


def test_match_roundtrip() -> None:
    raw = _load("match_22_27190.json")
    parsed = MatchData.model_validate(raw)
    dumped = parsed.model_dump(by_alias=True, exclude_none=False)
    dumped_present = _strip_unset_nones(dumped, raw)
    assert dumped_present == _filter_by_dump(raw, dumped_present)
    assert parsed.stages, "match must have stages"
    assert parsed.competitors, "match must have competitors"
    # Globally-stable shooterId must survive the trip.
    assert all(c.shooterId > 0 for c in parsed.competitors)


def test_shooter_search_roundtrip() -> None:
    raw = _load("shooter_search.json")
    assert isinstance(raw, list) and raw, "search fixture must be a non-empty array"
    _roundtrip_list(ShooterRef, raw)


def test_shooter_dashboard_roundtrip() -> None:
    raw = _load("shooter_dashboard.json")
    parsed = ShooterDashboard.model_validate(raw)
    dumped = parsed.model_dump(by_alias=True, exclude_none=False)
    dumped_present = _strip_unset_nones(dumped, raw)
    assert dumped_present == _filter_by_dump(raw, dumped_present)
    # ``from`` alias on dateRange must round-trip to the wire name.
    assert "from" in dumped["stats"]["dateRange"]
    assert "from_" not in dumped["stats"]["dateRange"]


def test_protocol_is_runtime_checkable() -> None:
    class _Stub:
        def search_matches(self, query: str) -> list:
            return []

        def get_match(self, content_type: int, match_id: int):  # noqa: ANN201
            raise NotImplementedError

        def find_shooter(self, name: str) -> list:
            return []

        def get_shooter(self, shooter_id: int):  # noqa: ANN201
            raise NotImplementedError

    assert isinstance(_Stub(), ScoreboardClient)


def _strip_unset_nones(dumped: Any, src: Any) -> Any:
    """Recursively remove keys whose value is ``None`` and which the wire
    response didn't carry. Keeps explicit ``null``s from the source intact.
    """
    if isinstance(dumped, dict) and isinstance(src, dict):
        return {
            k: _strip_unset_nones(v, src.get(k))
            for k, v in dumped.items()
            if not (v is None and k not in src)
        }
    if isinstance(dumped, list) and isinstance(src, list):
        return [_strip_unset_nones(d, s) for d, s in zip(dumped, src, strict=True)]
    return dumped


@pytest.mark.parametrize(
    "fixture",
    [
        "events.json",
        "match_22_27190.json",
        "shooter_search.json",
        "shooter_dashboard.json",
    ],
)
def test_fixture_files_exist_and_are_json(fixture: str) -> None:
    data = _load(fixture)
    assert data is not None
