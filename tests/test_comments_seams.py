"""Cross-cutting checks no single task owns (Task 12).

CLAUDE.md's review practice: "One defect lived in a seam no single task
owned; only a cross-cutting read found it." The per-task tests each cover
one layer; these cover the boundaries between them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.ui.server import _SHARE_PATH_RE, _SHARE_WRITE_ROUTES, _share_write_admits
from tests.hosted_helpers import login, seed_match
from tests.test_comments_moderation import (
    KEY_A,
    MID,
    SLUG,
    STAGE,
    _AliasedClient,
    _mint_comment_link,
    _post_comment,
    _seed_state_docs,
)
from tests.test_mirror_read_only import _seed_recent_project


def test_the_two_allowlists_are_distinct_objects() -> None:
    """They are separate so _SHARE_PATH_RE's GET-only docstring stays
    true. If a future edit merges the read and write allowlists, this
    fails."""
    assert not any(_SHARE_PATH_RE is pattern for _, pattern in _SHARE_WRITE_ROUTES)
    assert _SHARE_PATH_RE.pattern not in {pattern.pattern for _, pattern in _SHARE_WRITE_ROUTES}


def test_no_shape_is_admitted_by_both_allowlists_except_the_thread() -> None:
    """The comment thread is the one path shape that is both readable
    (GET, through any scope) and writable (POST/DELETE, through a
    comment-scoped token). Anything else admitted by both means a read
    shape leaked onto the write surface, or vice versa."""
    shapes = [
        "match/shooters",
        "shooters/alice/project",
        "shooters/alice/stages/3/coach",
        "shooters/alice/coach/distributions",
        "shooters/alice/videos/stream",
        "match/stage/3/compare",
        "match/shooters/alice/videos/stream",
        "og.png",
        "og-meta",
        "shooters/alice/stages/3/comments",
    ]
    both = [
        s
        for s in shapes
        if _SHARE_PATH_RE.fullmatch(s)
        and (_share_write_admits("POST", s) or _share_write_admits("DELETE", s))
    ]
    assert both == ["shooters/alice/stages/3/comments"]


@pytest.fixture
def owner_client(
    hosted_env: str, hosted_app: tuple[TestClient, object], tmp_path: Path
) -> Iterator[tuple[_AliasedClient, TestClient, str]]:
    """Owner session on a match seeded with state docs *and* a picker row
    (``recent_projects``), so the real delete route -
    ``POST /api/me/recent-projects/delete`` - can resolve ``match_id``
    from ``path`` the way the SPA picker does.

    Yields ``(aliased, raw, path)``: ``aliased`` (matches
    ``test_comments_moderation``'s wrapper) for the comment routes, which
    need the ``/api/matches/{match_id}/...`` alias; ``raw`` for the
    top-level delete route, which must NOT be aliased; ``path`` for the
    delete request body.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
    path = str((tmp_path / "picker-entry" / MID).resolve())
    _seed_recent_project(hosted_env, "owner@example.com", path=path, match_id=MID, name="Seam test match")
    yield _AliasedClient(client, MID), client, path


@pytest.fixture
def seeded_comment(owner_client: tuple[_AliasedClient, TestClient, str]) -> str:
    aliased, _raw, _path = owner_client
    _, token = _mint_comment_link(aliased)
    return _post_comment(aliased, token, key=KEY_A)


def test_deleting_a_match_purges_its_comments(
    owner_client: tuple[_AliasedClient, TestClient, str], seeded_comment: str
) -> None:
    """Nothing cascades from the matches registry row - _delete_hosted
    deletes state_docs explicitly for that reason. Comments need the same
    step, or 'delete my match' leaves other people's text behind."""
    aliased, raw, path = owner_client
    before = aliased.get(f"/api/shooters/{SLUG}/stages/{STAGE}/comments").json()
    assert len(before["comments"]) == 1

    resp = raw.post("/api/me/recent-projects/delete", json={"path": path})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["comments_removed"] == 1


def test_match_delete_reports_comments_in_its_summary(
    owner_client: tuple[_AliasedClient, TestClient, str], seeded_comment: str
) -> None:
    """The summary is the audit trail for a destructive action (CLAUDE.md:
    optimize for the audit trail). A silent purge is worse than none."""
    _aliased, raw, path = owner_client
    resp = raw.post("/api/me/recent-projects/delete", json={"path": path})
    assert resp.status_code == 200, resp.text
    assert "comments_removed" in resp.json()["summary"]
