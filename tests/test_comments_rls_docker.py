"""#Task 12 step 1b: prove the ``tenant_isolation`` RLS policy on
``match_comments`` at a real Postgres.

Every isolation test in ``test_comments_store.py`` proves only that
``CommentStore`` filters explicitly on ``user_id`` - the default suite is
sqlite-backed, and sqlite has no row-level security, so the policy itself
(created by migration ``b4d8f1a90c27``, same ``tenant_isolation`` family as
every other tenant table) has never actually been exercised. This file
runs the five assertions verified by hand against ``postgres:16-alpine``
with the migration applied, as the non-superuser ``splitsmith_app`` role
the API actually connects as (the default ``splitsmith`` superuser
bypasses RLS entirely, which is exactly why seeding below uses it).

Run with::

    uv run pytest -m docker -n0 tests/test_comments_rls_docker.py -q

``-n0`` is required whenever a docker run spans more than one
docker-marked file (see CLAUDE.md) - the compose fixtures use fixed
container names, so concurrent xdist workers would collide on them.
"""

from __future__ import annotations

import pytest

from .test_hosted_docker_smoke import _psql, _psql_run

pytestmark = pytest.mark.docker

_UID_A = "own-A"
_UID_B = "own-B"
_MID_A = "mid-comments-a"
_MID_B = "mid-comments-b"


def _seed_two_owners_comments() -> None:
    """Seed users A and B and one ``match_comments`` row each.

    Runs as the ``splitsmith`` superuser, which bypasses RLS - the only
    way to write rows owned by two different tenants in one pass.
    """
    _psql(
        "INSERT INTO users (id, email, entitlement) VALUES "
        f"('{_UID_A}', 'own-a-rls@hosted.local', 'free'), "
        f"('{_UID_B}', 'own-b-rls@hosted.local', 'free') "
        "ON CONFLICT (id) DO NOTHING"
    )
    _psql(
        "INSERT INTO match_comments "
        "(id, user_id, match_id, slug, stage_number, anchor_t, anchor_kind, "
        "author_kind, author_handle, author_key_hash, share_token_id, body) VALUES "
        f"('cmt-a', '{_UID_A}', '{_MID_A}', 'alice', 1, 4.0, 'time', 'handle', "
        "'Steady Popper 01', 'hash-a', 'tok-a', 'comment from A'), "
        f"('cmt-b', '{_UID_B}', '{_MID_B}', 'bob', 1, 5.0, 'time', 'handle', "
        "'Swift Popper 02', 'hash-b', 'tok-b', 'comment from B') "
        "ON CONFLICT (id) DO NOTHING"
    )


def test_owner_a_sees_only_as_rows(hosted_stack: None) -> None:
    """``SET app.user_id='own-A'`` sees only A's rows."""
    _seed_two_owners_comments()
    visible = _psql(
        f"SET app.user_id = '{_UID_A}'; "
        f"SELECT id FROM match_comments WHERE id IN ('cmt-a', 'cmt-b') ORDER BY id",
        user="splitsmith_app",
    )
    assert visible == "cmt-a", f"tenant A saw {visible!r}, expected only 'cmt-a'"


def test_owner_b_sees_only_bs_rows(hosted_stack: None) -> None:
    """``SET app.user_id='own-B'`` sees only B's rows."""
    _seed_two_owners_comments()
    visible = _psql(
        f"SET app.user_id = '{_UID_B}'; "
        f"SELECT id FROM match_comments WHERE id IN ('cmt-a', 'cmt-b') ORDER BY id",
        user="splitsmith_app",
    )
    assert visible == "cmt-b", f"tenant B saw {visible!r}, expected only 'cmt-b'"


def test_no_guc_set_sees_zero_rows(hosted_stack: None) -> None:
    """No ``app.user_id`` set at all: the policy fails closed, not open."""
    _seed_two_owners_comments()
    count = _psql(
        "SELECT count(*) FROM match_comments WHERE id IN ('cmt-a', 'cmt-b')",
        user="splitsmith_app",
    )
    assert count == "0", f"GUC-unset query leaked {count} row(s), expected 0"


def test_a_cannot_delete_bs_row_by_id(hosted_stack: None) -> None:
    """A ``DELETE`` naming B's row by id, issued as tenant A, affects
    nothing - the policy's ``USING`` clause hides the row from the
    statement entirely, so it isn't a permission error, it's zero rows
    matched. B must still see it afterward.

    A same-session recount (still under ``app.user_id = 'own-A'``) would
    prove nothing - A can never see B's row regardless of whether the
    DELETE did anything, so that check can't distinguish "deleted" from
    "merely invisible". The real proof needs a viewpoint the DELETE
    didn't run under: the superuser (bypasses RLS entirely, sees ground
    truth) and tenant B (the owner) both confirm the row is untouched.
    """
    _seed_two_owners_comments()
    _psql_run(
        f"SET app.user_id = '{_UID_A}'; DELETE FROM match_comments WHERE id = 'cmt-b'",
        user="splitsmith_app",
    )

    ground_truth = _psql("SELECT count(*) FROM match_comments WHERE id = 'cmt-b'")
    assert ground_truth == "1", f"A's DELETE on B's row actually removed it (count={ground_truth})"

    still_visible_to_b = _psql(
        f"SET app.user_id = '{_UID_B}'; SELECT id FROM match_comments WHERE id = 'cmt-b'",
        user="splitsmith_app",
    )
    assert still_visible_to_b == "cmt-b", "B's row should be unaffected by A's DELETE"


def test_a_cannot_insert_a_row_owned_by_b(hosted_stack: None) -> None:
    """A ``INSERT`` naming ``user_id = B`` while ``app.user_id = 'own-A'``
    is rejected by the ``WITH CHECK`` clause - a malicious or buggy
    caller can't launder a write into someone else's tenant."""
    _seed_two_owners_comments()
    bad_insert = _psql_run(
        f"SET app.user_id = '{_UID_A}'; "
        "INSERT INTO match_comments "
        "(id, user_id, match_id, slug, stage_number, anchor_t, anchor_kind, "
        "author_kind, author_handle, author_key_hash, share_token_id, body) VALUES "
        f"('cmt-x', '{_UID_B}', '{_MID_B}', 'bob', 1, 6.0, 'time', 'handle', "
        "'Sneaky Popper 99', 'hash-x', 'tok-x', 'smuggled comment')",
        user="splitsmith_app",
    )
    assert bad_insert.returncode != 0, "RLS WITH CHECK let tenant A insert a row owned by tenant B"
    assert "row-level security" in bad_insert.stderr.lower()
