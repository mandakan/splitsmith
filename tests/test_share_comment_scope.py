"""The 'comment' share scope and what it does (and does not) unlock."""

from __future__ import annotations

import pytest

from splitsmith.ui.capabilities import (
    COMMENT_WRITE,
    EDIT,
    REVIEW,
    SHARE_MANAGE,
    share_scope_capabilities,
)

share_guard = pytest.importorskip("splitsmith.db.share_guard")


def _with_scope(scope):  # type: ignore[no-untyped-def]
    token = share_guard.current_share_scope.set(scope)
    try:
        return share_guard.share_request_is_read_only()
    finally:
        share_guard.current_share_scope.reset(token)


def test_read_scope_is_still_read_only() -> None:
    """The regression that matters: _WRITE_CAPABLE_SCOPES gaining a
    member must not turn the check off for the scope every existing
    share link carries."""
    assert _with_scope("read") is True


def test_unknown_scope_still_fails_closed() -> None:
    assert _with_scope("kommentar") is True
    assert _with_scope("") is True


def test_comment_scope_is_write_capable() -> None:
    assert _with_scope("comment") is False


def test_no_scope_at_all_is_not_a_share_request() -> None:
    assert _with_scope(None) is False


def test_comment_scope_grants_only_comment_write() -> None:
    caps = share_scope_capabilities("comment")
    assert COMMENT_WRITE in caps
    assert EDIT not in caps
    assert REVIEW not in caps
    assert SHARE_MANAGE not in caps


def test_read_scope_grants_nothing() -> None:
    assert share_scope_capabilities("read") == frozenset()


def test_unknown_scope_grants_nothing() -> None:
    assert share_scope_capabilities("comment ") == frozenset()
    assert share_scope_capabilities(None) == frozenset()
