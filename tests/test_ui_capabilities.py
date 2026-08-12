"""#756: the capability table is the single encoding of who may write
what. These tests pin (a) the per-origin and per-scope sets and (b) the
route classification, including exact parity with the exception regexes
the old mirror guard hand-listed."""

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
        # The by-id coach PATCH (#631 Task 3) - the form a client that did
        # not just write the document must use, since shot_number renumbers.
        ("PATCH", "shooters/anna/stages/3/shots/by-id/cand-2/coach", REVIEW),
        ("PATCH", "shooters/anna/stages/3/shots/by-id/manual-t6500/coach", REVIEW),
        # The full stage audit PUT (#631 Task 6).
        ("PUT", "shooters/anna/stages/3/audit", REVIEW),
        # Method mismatches fall through to EDIT - the old guard was
        # method-gated per regex and the table must stay that strict.
        ("DELETE", "shooters/anna/stages/3/videos/v1/beep", EDIT),
        ("POST", "shooters/anna/stages/3/shots/2/coach", EDIT),
        ("PATCH", "shooters/anna/stages/3/coach/reclassify", EDIT),
        # Beep re-detect was never mirror-writable (only .../beep is).
        ("POST", "shooters/anna/stages/3/videos/v1/beep/detect", EDIT),
        # The audit exemption is one exact path and one method: a POST to
        # it, a trailing slash, a sibling path and a non-numeric stage all
        # fall through to EDIT.
        ("POST", "shooters/anna/stages/3/audit", EDIT),
        ("PUT", "shooters/anna/stages/3/audit/", EDIT),
        ("PUT", "shooters/anna/stages/3/audit/extra", EDIT),
        ("PUT", "shooters/anna/stages/x/audit", EDIT),
        # A shot id outside [A-Za-z0-9._-] is not addressable by id.
        ("PATCH", "shooters/anna/stages/3/shots/by-id/bad id/coach", EDIT),
        ("PATCH", "shooters/anna/stages/3/shots/by-id//coach", EDIT),
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


@pytest.mark.parametrize(
    ("method", "rest"),
    [
        ("POST", "match/beep-queue/confirm\n"),
        ("POST", "shooters/anna/stages/3/videos/v1/beep\n"),
        ("POST", "shooters/anna/stages/3/audit/accept\n"),
        ("PATCH", "shooters/anna/stages/3/shots/2/coach\n"),
        ("PATCH", "shooters/anna/stages/3/shots/by-id/cand-2/coach\n"),
        ("POST", "shooters/anna/stages/3/coach/reclassify\n"),
        ("PUT", "shooters/anna/stages/3/audit\n"),
    ],
)
def test_review_routes_do_not_admit_a_trailing_newline(method: str, rest: str) -> None:
    """``\\Z``, not ``$``: an allow-list entry means one exact path.

    ``$`` also matches just before a single trailing ``\\n``, which on an
    allow-list widens REVIEW over a string the table means to send to
    EDIT. No such ``rest`` reaches the middleware today (``urlsplit()``
    strips CR/LF/TAB), so this pins the table's own contract rather than a
    reachable bypass - ``required_capability`` is a public function that
    does not see or control who builds its argument.
    """
    assert required_capability(method, rest) == EDIT
