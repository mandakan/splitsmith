"""#756: the capability table is the single encoding of who may write
what. These tests pin (a) the per-origin and per-scope sets and (b) the
route classification, including exact parity with the five exception
regexes the old mirror guard hand-listed."""

from __future__ import annotations

import pytest

from splitsmith.ui.capabilities import (
    EDIT,
    REVIEW,
    SHARE_MANAGE,
    capabilities_for_origin,
    required_capability,
    share_scope_capabilities,
)


def test_origin_capability_sets() -> None:
    assert capabilities_for_origin("hosted") == {EDIT, REVIEW, SHARE_MANAGE}
    assert capabilities_for_origin("desktop") == {REVIEW, SHARE_MANAGE}
    assert capabilities_for_origin("local") == {EDIT, REVIEW}
    # None means "no aliased match bound" (legacy bare-path local traffic)
    # and gets the local set - same fallback get_project uses for origin.
    assert capabilities_for_origin(None) == {EDIT, REVIEW}


def test_share_scope_capability_sets() -> None:
    assert share_scope_capabilities("read") == frozenset()
    # Unknown or absent scopes fail closed - a typo'd scope grants nothing.
    assert share_scope_capabilities("coach") == frozenset()
    assert share_scope_capabilities(None) == frozenset()


@pytest.mark.parametrize(
    ("method", "rest", "expected"),
    [
        # Safe methods never need a capability.
        ("GET", "shooters/anna/project", None),
        ("HEAD", "match/shooters", None),
        ("OPTIONS", "match/stage/1/compare", None),
        # Share management - any method, base and sub-paths.
        ("POST", "match/shares", SHARE_MANAGE),
        ("DELETE", "match/shares/01ABC", SHARE_MANAGE),
        # The review set - exact parity with the old exception regexes.
        ("POST", "match/beep-queue/confirm", REVIEW),
        ("POST", "shooters/anna/stages/3/videos/v1/beep", REVIEW),
        ("POST", "shooters/anna/stages/3/audit/accept", REVIEW),
        ("POST", "shooters/anna/stages/3/attention", REVIEW),
        ("PATCH", "shooters/anna/stages/3/shots/2/coach", REVIEW),
        ("POST", "shooters/anna/stages/3/coach/reclassify", REVIEW),
        # Method mismatches fall through to EDIT - the old guard was
        # method-gated per regex and the table must stay that strict.
        ("DELETE", "shooters/anna/stages/3/videos/v1/beep", EDIT),
        ("POST", "shooters/anna/stages/3/shots/2/coach", EDIT),
        ("PATCH", "shooters/anna/stages/3/coach/reclassify", EDIT),
        # Beep re-detect was never mirror-writable (only .../beep is).
        ("POST", "shooters/anna/stages/3/videos/v1/beep/detect", EDIT),
        # Unlisted writes require EDIT - new routes fail over-restricted,
        # never silently writable.
        ("POST", "match/shooters", EDIT),
        ("PUT", "match/stages", EDIT),
        ("DELETE", "match/shooters/anna", EDIT),
        ("POST", "shooters/anna/stages/3/export", EDIT),
    ],
)
def test_required_capability(method: str, rest: str, expected: str | None) -> None:
    assert required_capability(method, rest) == expected
