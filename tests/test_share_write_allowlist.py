"""The containment boundary for anonymous writes.

These are the tests that matter most on this branch. The player either
works or obviously does not; a hole here is silent.
"""

from __future__ import annotations

import re

from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_PATH_RE

COMMENTS = "shooters/alice/stages/3/comments"


def test_read_pattern_admits_the_comment_thread() -> None:
    assert _SHARE_PATH_RE.fullmatch(COMMENTS)


def test_read_pattern_does_not_admit_a_comment_id() -> None:
    """Reading one comment by id is not a shape we serve; the thread is."""
    assert _SHARE_PATH_RE.fullmatch(f"{COMMENTS}/01J000000000000000000000") is None


def test_write_pattern_admits_post_and_delete_shapes_only() -> None:
    assert _SHARE_WRITE_PATH_RE.fullmatch(COMMENTS)
    assert _SHARE_WRITE_PATH_RE.fullmatch(f"{COMMENTS}/01J000000000000000000000")


def test_write_pattern_admits_nothing_else_from_the_read_surface() -> None:
    """The two patterns are separate on purpose. If someone ever merges
    them, this fails."""
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
        assert _SHARE_WRITE_PATH_RE.fullmatch(shape) is None, shape


def test_write_pattern_rejects_traversal_and_extra_segments() -> None:
    for shape in (
        "shooters/alice/stages/3/comments/../../../match/shooters",
        "shooters/alice/stages/3/comments/abc/def",
        "shooters/alice/stages/x/comments",
        "shooters/alice/stages/3/comments/",
        "SHOOTERS/alice/stages/3/comments",
    ):
        assert _SHARE_WRITE_PATH_RE.fullmatch(shape) is None, shape


def test_write_pattern_is_anchored_against_a_trailing_newline() -> None:
    """_REVIEW_ROUTES documents why \\Z beats $ on an allow-list: plain $
    also matches before one trailing newline, and the widened form grants
    more than intended."""
    assert _SHARE_WRITE_PATH_RE.fullmatch(f"{COMMENTS}\n") is None
    assert _SHARE_WRITE_PATH_RE.pattern.endswith(r")\Z")
    assert _SHARE_WRITE_PATH_RE.flags & re.IGNORECASE == 0
