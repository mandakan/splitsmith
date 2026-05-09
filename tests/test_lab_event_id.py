"""Event-grouping tests: ``event_id`` derivation + ``list_fixtures`` integration.

The grouping unit is "shooter X's run on stage N at match M". Multi-cam
siblings of the same physical run share an event_id; different shooters
on the same stage are intentionally distinct events so cross-mixing
doesn't pollute training data.
"""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.lab.core import (
    DEFAULT_SHOOTER_KEY,
    build_event_id,
    event_id_from_payload,
    list_fixtures,
    match_stage_from_slug,
    shooter_token,
)


def test_shooter_token_is_stable_and_non_pii() -> None:
    # Same SSI ID -> same token, regardless of how it's typed.
    assert shooter_token(41643) == shooter_token("41643")
    # Format: 's' + 8 hex chars.
    tok = shooter_token(41643)
    assert tok.startswith("s")
    assert len(tok) == 9
    int(tok[1:], 16)  # parses as hex
    # Different IDs -> different tokens.
    assert shooter_token(41643) != shooter_token(44346)


def test_shooter_token_known_value() -> None:
    # Pinned so a repo-wide migration produces stable filenames; if this
    # changes every fixture path in tests/fixtures/ also has to change.
    assert shooter_token(41643) == "s97dcec94"


def test_match_stage_from_slug_parses_canonical_pattern() -> None:
    assert match_stage_from_slug("stage-shots-blacksmith-2026-stage6") == (
        "blacksmith-2026",
        6,
    )
    # Camera-suffixed (multi-cam) slug still parses to the same (match, stage).
    assert match_stage_from_slug("stage-shots-blacksmith-2026-stage6-apple-iphone17pro") == (
        "blacksmith-2026",
        6,
    )
    # Long-form match name slug.
    assert match_stage_from_slug("stage-shots-blacksmith-handgun-open-2026-stage6") == (
        "blacksmith-handgun-open-2026",
        6,
    )


def test_match_stage_from_slug_returns_none_for_non_matching() -> None:
    assert match_stage_from_slug("not-a-fixture-slug") is None
    assert match_stage_from_slug("stage-shots-foo-stagebar") is None


def test_event_id_from_payload_combines_match_stage_and_shooter() -> None:
    payload = {"shooter": {"id": "ssi-12345", "ssi_shooter_id": 12345}}
    assert (
        event_id_from_payload("stage-shots-blacksmith-2026-stage6", payload)
        == "blacksmith-2026:6:ssi-12345"
    )


def test_event_id_from_payload_defaults_shooter_to_self() -> None:
    """Pre-issue-#149 fixtures with no shooter block default to ``self``."""
    payload: dict = {}
    assert (
        event_id_from_payload("stage-shots-blacksmith-2026-stage6", payload)
        == f"blacksmith-2026:6:{DEFAULT_SHOOTER_KEY}"
    )


def test_event_id_from_payload_explicit_event_id_wins_over_slug() -> None:
    """Explicit ``event_id`` on the JSON bypasses slug parsing.

    Used by the migration's ``--alias`` flag to merge a non-canonical
    slug (e.g., ``blacksmith-handgun-open-2026``) into an existing
    short-name event group (e.g., ``blacksmith-2026``).
    """
    payload = {"event_id": "blacksmith-2026:6:self"}
    assert (
        event_id_from_payload("stage-shots-blacksmith-handgun-open-2026-stage6", payload)
        == "blacksmith-2026:6:self"
    )


def test_build_event_id_canonical() -> None:
    assert build_event_id("blacksmith-2026", 6, "ssi-12345") == "blacksmith-2026:6:ssi-12345"
    assert build_event_id("tallmilan-2026", 7, DEFAULT_SHOOTER_KEY) == "tallmilan-2026:7:self"


def test_list_fixtures_groups_multi_cam_siblings_by_event_id(tmp_path: Path) -> None:
    """Two slugs with the same shooter on the same stage yield one event."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    base_payload = {
        "beep_time": 5.0,
        "stage_time_seconds": 10.0,
        "shots": [{"shot_number": 1, "time": 5.5, "ms_after_beep": 500}],
        "shooter": {"id": "ssi-42", "ssi_shooter_id": 42, "name": "Sample"},
    }

    headcam_path = fixtures / "stage-shots-blacksmith-2026-stage6.json"
    phone_path = fixtures / "stage-shots-blacksmith-2026-stage6-apple-iphone17pro.json"
    headcam_path.write_text(json.dumps(base_payload))
    phone_path.write_text(json.dumps(base_payload))
    (fixtures / "stage-shots-blacksmith-2026-stage6.wav").write_bytes(b"")
    (fixtures / "stage-shots-blacksmith-2026-stage6-apple-iphone17pro.wav").write_bytes(b"")

    records = list_fixtures(fixtures)
    by_slug = {r.slug: r for r in records}
    assert by_slug["stage-shots-blacksmith-2026-stage6"].event_id == "blacksmith-2026:6:ssi-42"
    assert (
        by_slug["stage-shots-blacksmith-2026-stage6-apple-iphone17pro"].event_id
        == "blacksmith-2026:6:ssi-42"
    )


def test_list_fixtures_distinct_shooters_get_distinct_events(tmp_path: Path) -> None:
    """Two shooters on the same stage are distinct events (no cross-grouping)."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    me = {"shots": [], "shooter": {"id": "ssi-1", "ssi_shooter_id": 1, "name": "Me"}}
    friend = {"shots": [], "shooter": {"id": "ssi-2", "ssi_shooter_id": 2, "name": "Friend"}}
    (fixtures / "stage-shots-blacksmith-2026-stage6.json").write_text(json.dumps(me))
    (fixtures / "stage-shots-blacksmith-2026-stage6-friend.json").write_text(json.dumps(friend))
    (fixtures / "stage-shots-blacksmith-2026-stage6.wav").write_bytes(b"")
    (fixtures / "stage-shots-blacksmith-2026-stage6-friend.wav").write_bytes(b"")

    records = list_fixtures(fixtures)
    by_slug = {r.slug: r for r in records}
    assert by_slug["stage-shots-blacksmith-2026-stage6"].event_id == "blacksmith-2026:6:ssi-1"
    assert (
        by_slug["stage-shots-blacksmith-2026-stage6-friend"].event_id == "blacksmith-2026:6:ssi-2"
    )
    assert (
        by_slug["stage-shots-blacksmith-2026-stage6"].event_id
        != by_slug["stage-shots-blacksmith-2026-stage6-friend"].event_id
    )


def test_list_fixtures_legacy_fixture_defaults_shooter_to_self(tmp_path: Path) -> None:
    """Pre-#149 fixture with no shooter block resolves to ``...:self``."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "stage-shots-blacksmith-2026-stage6.json").write_text(json.dumps({"shots": []}))
    (fixtures / "stage-shots-blacksmith-2026-stage6.wav").write_bytes(b"")

    records = list_fixtures(fixtures)
    assert records[0].event_id == f"blacksmith-2026:6:{DEFAULT_SHOOTER_KEY}"
