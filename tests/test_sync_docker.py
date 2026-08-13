"""Docker-compose RLS proof for the desktop-to-hosted sync MVP (#631, Task 12).

Reuses ``test_hosted_docker_smoke.py``'s ``hosted_stack`` fixture (docker
compose up/down) plus its ``_psql`` / ``_psql_run`` / ``_magic_link_login``
helpers and ``API_BASE``, so this file pays no extra setup cost beyond
bringing its own copy of the compose stack up - the same idiom every other
``@pytest.mark.docker`` test in this repo already uses.

Four things the in-process (SQLite) test suite cannot prove, all driven
against live Postgres under the non-superuser ``splitsmith_app`` role the
production API and worker actually run as:

1. ``desktop_tokens`` carries NO RLS policy (see ``DesktopTokenRow``'s
   docstring - resolution happens before any ``app.user_id`` GUC exists,
   so RLS would break the very bearer-auth path it's meant to protect).
   Isolation there is ``DesktopTokenStore``'s own ``WHERE user_id = ...``
   filter. This file proves that filter actually holds under live
   Postgres, not just against the in-memory SQLite doubles the rest of
   the suite uses.
2. ``matches`` and ``state_docs`` - the two tables a sync push actually
   writes to (mirror row + match/project/audit docs) - DO carry RLS
   (migrations ``a7c4e9d21b06`` - RLS on tenant tables incl. matches,
   and ``d1f7b25c8a3e`` - state_docs joins the tenant_isolation policy
   family, asserted end-to-end in
   ``test_hosted_docker_smoke.test_rls_blocks_cross_tenant_reads_and_writes``).
   This file re-proves it scoped to those two tables, seeded with the
   exact row shape a sync push produces (``matches.origin = 'desktop'``).
3. The raw-factory bearer resolver (``DesktopTokenAuth``, constructed
   from the RAW session factory - the pre-tenant path, same rationale as
   ``MagicLinkAuth``) resolves both users' tokens correctly when driven
   against the ``splitsmith_app`` role, before any tenant GUC is set.
4. The manifest + version-guarded PUT stack (Tasks 2/3) over real HTTP:
   a coalesce-unique-index-backed ``ProjectStateStore`` upsert racing
   against a stale ``expected_version`` actually 409s ``version_conflict``
   under live Postgres, and the manifest/GET routes reflect the winning
   write, not the rejected one - aiosqlite's simpler locking can't
   exercise the same race.

Run with ``PATH=~/.claude-tmp/bin:$PATH pytest -m docker tests/test_sync_docker.py -v``
(docker CLI lives outside the default non-interactive PATH on this host).
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from .test_hosted_docker_smoke import API_BASE, POSTGRES_PORT, _magic_link_login, _psql

# ``hosted_stack`` is re-exported for global fixture discovery via
# conftest.py (same idiom as ``hosted_app`` / ``hosted_env`` - importing it
# directly here would trigger ruff F811 since the fixture name also appears
# as a function parameter below).

pytestmark = pytest.mark.docker

# The container's own SPLITSMITH_DATABASE_URL (docker-compose.yml) uses this
# exact role - connecting as it here from the host exercises the same
# non-superuser path the API/worker run under, not the ``splitsmith``
# superuser ``test_hosted_docker_smoke.py`` seeds with.
#
# The port comes from the smoke module rather than being written out
# here: the compose stack's published ports are overridable and default
# to shifted values, so a literal 5432 would silently connect to
# whatever else the developer's box happens to be running there instead
# of to the test stack -- which fails as a confusing SQL error, not as a
# connection refusal.
HOST_APP_DB_URL = f"postgresql+asyncpg://splitsmith_app:splitsmith_app@localhost:{POSTGRES_PORT}/splitsmith"


def _seed_two_users() -> tuple[str, str]:
    """Insert two fresh users as the superuser and return their ids.

    Unique per test run (uuid suffix) so this file's tests don't collide
    with rows ``test_hosted_docker_smoke.py`` seeds in a module-scoped
    stack that (per that file's ``hosted_stack`` fixture) gets its own
    ``down -v`` between runs anyway, but never between tests within one
    run of this file.
    """
    uid_a = f"user-a-sync-{uuid.uuid4().hex[:8]}"
    uid_b = f"user-b-sync-{uuid.uuid4().hex[:8]}"
    _psql(
        "INSERT INTO users (id, email, entitlement) VALUES "
        f"('{uid_a}', '{uid_a}@hosted.local', 'free'), "
        f"('{uid_b}', '{uid_b}@hosted.local', 'free') "
        "ON CONFLICT (id) DO NOTHING"
    )
    return uid_a, uid_b


def test_desktop_token_store_isolation_and_bearer_resolution_under_app_role(
    hosted_stack: None,
) -> None:
    """(a) store-level isolation for desktop_tokens (no RLS on this table -
    see module docstring) and (c) the raw-factory bearer resolver working
    for both tenants under the non-superuser app role, pre-tenant.

    Drives the real ``DesktopTokenStore`` / ``DesktopTokenAuth`` classes
    against the compose Postgres from the host, over the ``splitsmith_app``
    role - same idiom as ``test_hosted_docker_smoke._magic_link_login``
    driving ``MagicLinkAuth`` cross-process.
    """
    from splitsmith.db import create_engine, sessionmaker
    from splitsmith.db.desktop_tokens import DesktopTokenAuth, DesktopTokenStore

    uid_a, uid_b = _seed_two_users()

    async def _flow() -> tuple[str, str]:
        # One event loop for the whole flow - separate asyncio.run() calls
        # would each spin a fresh loop and a later asyncpg connection bound
        # to the first would be reused from a different loop.
        sf = sessionmaker(create_engine(HOST_APP_DB_URL, pool_disabled=True))
        store_a = DesktopTokenStore(sf, user_id=uid_a)
        store_b = DesktopTokenStore(sf, user_id=uid_b)

        rec_a, raw_a = await store_a.create("A's laptop")
        rec_b, raw_b = await store_b.create("B's laptop")

        # (a) DesktopTokenStore.list() is scoped by the store's own
        # user_id filter, not a Postgres policy - prove it actually holds
        # under live Postgres with both tenants' rows in the same table.
        list_a = await store_a.list()
        list_b = await store_b.list()
        assert [r.id for r in list_a] == [rec_a.id], "A's list leaked or missed a row"
        assert [r.id for r in list_b] == [rec_b.id], "B's list leaked or missed a row"

        # A cannot revoke B's token by id - same filter guards writes.
        revoked_cross_tenant = await store_a.revoke(rec_b.id)
        assert revoked_cross_tenant is False
        still_live = await store_b.list()
        assert still_live[0].revoked_at is None, "A's revoke call reached B's row"

        # (c) the raw-factory bearer resolver: both raw tokens resolve to
        # their own owner, pre-tenant (no app.user_id GUC set anywhere in
        # this flow - DesktopTokenAuth must not need one).
        auth = DesktopTokenAuth(sf)
        user_a = await auth.authenticate_request(_bearer_request(raw_a))
        user_b = await auth.authenticate_request(_bearer_request(raw_b))
        assert user_a is not None and user_a.id == uid_a
        assert user_b is not None and user_b.id == uid_b
        return user_a.id, user_b.id

    resolved_a, resolved_b = asyncio.run(_flow())
    assert resolved_a != resolved_b

    # Guard: the whole test is meaningless if the app role can bypass RLS
    # (same guard idiom as test_hosted_docker_smoke's RLS test) - desktop_tokens
    # itself has no policy, but this proves the role driving the flow above
    # really is the restricted one production runs as, not a superuser that
    # would make "isolation" trivially true regardless of the store's filter.
    assert _psql("SELECT rolsuper FROM pg_roles WHERE rolname='splitsmith_app'") == "f"


def _bearer_request(token: str):
    from fastapi import Request

    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


def _seed_two_tenant_mirrors() -> tuple[str, str]:
    """Seed A and B each with one desktop-origin ``matches`` row + one
    ``state_docs`` row - the exact shape a sync push writes
    (``POST /api/sync/matches`` then ``PUT .../docs/match``). Runs as the
    superuser, the only role that can write both tenants' rows in one pass."""
    uid_a, uid_b = _seed_two_users()
    _psql(
        "INSERT INTO matches (id, user_id, match_id, name, storage_prefix, origin) VALUES "
        f"('m-sync-a-{uid_a}', '{uid_a}', 'mid-sync-a-{uid_a}', 'A mirror', "
        f"'matches/mid-sync-a-{uid_a}', 'desktop'), "
        f"('m-sync-b-{uid_b}', '{uid_b}', 'mid-sync-b-{uid_b}', 'B mirror', "
        f"'matches/mid-sync-b-{uid_b}', 'desktop') "
        "ON CONFLICT (id) DO NOTHING"
    )
    _psql(
        "INSERT INTO state_docs (id, user_id, match_id, doc_kind, doc, version) VALUES "
        f"('sd-sync-a-{uid_a}', '{uid_a}', 'mid-sync-a-{uid_a}', 'match', '{{}}'::jsonb, 1), "
        f"('sd-sync-b-{uid_b}', '{uid_b}', 'mid-sync-b-{uid_b}', 'match', '{{}}'::jsonb, 1) "
        "ON CONFLICT (id) DO NOTHING"
    )
    return uid_a, uid_b


def test_mirror_matches_and_state_docs_isolated_by_rls(hosted_stack: None) -> None:
    """(b) genuine RLS isolation for the two tables a sync push writes to.

    Mirrors ``test_hosted_docker_smoke.test_rls_blocks_cross_tenant_reads_and_writes``
    (same GUC dance, same non-superuser role), scoped to ``matches`` +
    ``state_docs`` and seeded with desktop-mirror-shaped rows rather than
    generic ones - a sync push is exactly the write path this table pair
    needs to keep isolated under RLS regardless of ``origin``.
    """
    uid_a, uid_b = _seed_two_tenant_mirrors()
    seeded = f"('{uid_a}', '{uid_b}')"

    # Guard: meaningless if the app role can bypass RLS.
    assert _psql("SELECT rolsuper FROM pg_roles WHERE rolname='splitsmith_app'") == "f"
    assert _psql("SELECT rolbypassrls FROM pg_roles WHERE rolname='splitsmith_app'") == "f"

    for table in ("matches", "state_docs"):
        visible_a = _psql(
            f"SET app.user_id = '{uid_a}'; "
            f"SELECT user_id FROM {table} WHERE user_id IN {seeded} ORDER BY user_id",
            user="splitsmith_app",
        )
        assert visible_a == uid_a, f"{table}: tenant A saw {visible_a!r}, expected only {uid_a!r}"

        visible_b = _psql(
            f"SET app.user_id = '{uid_b}'; "
            f"SELECT user_id FROM {table} WHERE user_id IN {seeded} ORDER BY user_id",
            user="splitsmith_app",
        )
        assert visible_b == uid_b, f"{table}: tenant B saw {visible_b!r}, expected only {uid_b!r}"

        # No GUC set: fail-closed, zero rows visible.
        unset = _psql(
            f"SELECT count(*) FROM {table} WHERE user_id IN {seeded}",
            user="splitsmith_app",
        )
        assert unset == "0", f"{table}: GUC-unset query leaked {unset} rows (should be 0)"

    # A desktop-origin match row is a normal ``matches`` row as far as RLS
    # is concerned - the read-only-mirror gate is an app-layer rule
    # (server.py's ``_match_id_alias``), not a Postgres policy. RLS
    # isolates by ``user_id`` regardless of ``origin``.
    origin_a = _psql(
        f"SET app.user_id = '{uid_a}'; SELECT origin FROM matches WHERE id = 'm-sync-a-{uid_a}'",
        user="splitsmith_app",
    )
    assert origin_a == "desktop"


def test_mirror_matches_with_check_blocks_cross_tenant_insert(hosted_stack: None) -> None:
    """WITH CHECK: tenant A inserting a mirror row owned by B is rejected."""
    from .test_hosted_docker_smoke import _psql_run

    uid_a, uid_b = _seed_two_users()
    bad_insert = _psql_run(
        f"SET app.user_id = '{uid_a}'; "
        "INSERT INTO matches (id, user_id, match_id, name, storage_prefix, origin) "
        f"VALUES ('m-sync-x-{uid_a}', '{uid_b}', 'mid-sync-x-{uid_a}', 'x', "
        f"'matches/mid-sync-x-{uid_a}', 'desktop')",
        user="splitsmith_app",
    )
    assert bad_insert.returncode != 0, "RLS WITH CHECK let tenant A insert a mirror row owned by tenant B"
    assert "row-level security" in bad_insert.stderr.lower()


def test_manifest_and_version_guarded_put_round_trip_over_http(hosted_stack: None) -> None:
    """Full HTTP round trip through the running API container, driven the
    way the desktop client actually drives it (session cookie to mint a
    desktop token, then bearer auth for every ``/api/sync/*`` call):

    adopt -> PUT project doc at ``expected_version=0`` -> manifest shows
    version 1 -> PUT again at ``expected_version=1`` -> ok, version 2 ->
    stale PUT at ``expected_version=1`` again -> 409 ``version_conflict``
    -> GET returns the latest body, not the rejected stale write.

    This proves the coalesce-unique-index + RLS + version-guard stack
    end to end against real Postgres under the non-superuser app role -
    the one thing the in-process (aiosqlite) suite can't stand in for,
    since the whole point is the optimistic-lock race living in a real
    transaction, not a test double's in-memory dict.
    """
    email = f"sync-http-{uuid.uuid4().hex[:8]}@example.com"
    _, secret = _magic_link_login(email)
    cookies = {"splitsmith_session": secret}

    token_resp = httpx.post(
        f"{API_BASE}/api/me/desktop-tokens",
        json={"name": "docker-smoke"},
        cookies=cookies,
        timeout=5.0,
    )
    assert token_resp.status_code == 201, token_resp.text
    bearer = {"Authorization": f"Bearer {token_resp.json()['token']}"}

    match_id = f"mid-sync-http-{uuid.uuid4().hex[:8]}"
    adopt = httpx.post(
        f"{API_BASE}/api/sync/matches",
        json={"match_id": match_id, "name": "HTTP round trip"},
        headers=bearer,
        timeout=5.0,
    )
    assert adopt.status_code == 200, adopt.text
    assert adopt.json()["origin"] == "desktop"

    slug = "alice"
    doc_url = f"{API_BASE}/api/sync/matches/{match_id}/docs/project/{slug}"

    put0 = httpx.put(
        doc_url, params={"expected_version": 0}, json={"name": "Alice"}, headers=bearer, timeout=5.0
    )
    assert put0.status_code == 200, put0.text
    assert put0.json()["version"] == 1

    manifest = httpx.get(f"{API_BASE}/api/sync/matches/{match_id}/docs", headers=bearer, timeout=5.0)
    assert manifest.status_code == 200, manifest.text
    versions = {(d["doc_kind"], d["slug"]): d["version"] for d in manifest.json()["docs"]}
    assert versions[("project", slug)] == 1

    put1 = httpx.put(
        doc_url, params={"expected_version": 1}, json={"name": "Alice v2"}, headers=bearer, timeout=5.0
    )
    assert put1.status_code == 200, put1.text
    assert put1.json()["version"] == 2

    stale = httpx.put(
        doc_url,
        params={"expected_version": 1},
        json={"name": "Alice stale write"},
        headers=bearer,
        timeout=5.0,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "version_conflict"

    latest = httpx.get(doc_url, headers=bearer, timeout=5.0)
    assert latest.status_code == 200, latest.text
    assert latest.json()["version"] == 2
    assert latest.json()["doc"]["name"] == "Alice v2"
