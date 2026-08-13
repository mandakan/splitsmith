"""The containment boundary for anonymous writes.

These are the tests that matter most on this branch. The player either
works or obviously does not; a hole here is silent.

Fix round 1, F5: the write surface used to be one pattern matched
against a separate method set (``_SHARE_WRITE_PATH_RE`` +
``_SHARE_WRITE_METHODS``), which took the cross product of "any shape
here" x "any method in the set" - so a POST to the DELETE-by-id shape
was admitted here, then refused 403 (not the uniform 404) once it hit
``required_capability``'s method-paired ``_COMMENT_ROUTES`` table
unmapped. ``_SHARE_WRITE_ROUTES`` pairs each shape with its one valid
method instead, mirroring ``_COMMENT_ROUTES`` one to one.
"""

from __future__ import annotations

import re

from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_ROUTES, _share_write_admits

COMMENTS = "shooters/alice/stages/3/comments"
COMMENT_ID = f"{COMMENTS}/01J000000000000000000000"


def test_read_pattern_admits_the_comment_thread() -> None:
    assert _SHARE_PATH_RE.fullmatch(COMMENTS)


def test_read_pattern_does_not_admit_a_comment_id() -> None:
    """Reading one comment by id is not a shape we serve; the thread is."""
    assert _SHARE_PATH_RE.fullmatch(COMMENT_ID) is None


def test_write_admits_post_on_the_thread_and_delete_on_one_comment() -> None:
    assert _share_write_admits("POST", COMMENTS)
    assert _share_write_admits("DELETE", COMMENT_ID)


def test_write_does_not_admit_the_cross_product() -> None:
    """F5: pairing shape with method, not taking the cross product - a
    POST shaped like the DELETE-by-id route (and vice versa) is refused,
    not silently admitted and refused later with a distinguishable 403."""
    assert _share_write_admits("DELETE", COMMENTS) is False
    assert _share_write_admits("POST", COMMENT_ID) is False


def test_write_admits_nothing_else_from_the_read_surface() -> None:
    """The read and write surfaces are separate on purpose. If someone
    ever merges them, this fails."""
    for shape in (
        "match/shooters",
        "shooters/alice/project",
        "shooters/alice/stages/3/coach",
        "shooters/alice/coach/distributions",
        "shooters/alice/videos/stream",
        "match/stage/3/compare",
        "match/shooters/alice/videos/stream",
        "og.png",
        "og-meta",
    ):
        assert _share_write_admits("POST", shape) is False, shape
        assert _share_write_admits("DELETE", shape) is False, shape


def test_write_rejects_traversal_and_extra_segments() -> None:
    for shape in (
        "shooters/alice/stages/3/comments/../../../match/shooters",
        "shooters/alice/stages/3/comments/abc/def",
        "shooters/alice/stages/x/comments",
        "shooters/alice/stages/3/comments/",
        "SHOOTERS/alice/stages/3/comments",
    ):
        assert _share_write_admits("POST", shape) is False, shape
        assert _share_write_admits("DELETE", shape) is False, shape


def test_write_routes_are_anchored_against_a_trailing_newline() -> None:
    """_REVIEW_ROUTES documents why \\Z beats $ on an allow-list: plain $
    also matches before one trailing newline, and the widened form grants
    more than intended. Every entry in _SHARE_WRITE_ROUTES is \\A...\\Z
    anchored and case-sensitive."""
    assert _share_write_admits("POST", f"{COMMENTS}\n") is False
    assert _share_write_admits("DELETE", f"{COMMENT_ID}\n") is False
    for method, pattern in _SHARE_WRITE_ROUTES:
        assert method in ("POST", "DELETE")
        assert pattern.pattern.startswith(r"\A")
        assert pattern.pattern.endswith(r"\Z")
        assert pattern.flags & re.IGNORECASE == 0
