"""Tests for WorkersStore.

Runs against SQLite in-memory via aiosqlite - same pattern as
test_share_tokens_store.py. WorkersStore is operator-scoped (no user_id),
so no user seeding needed for most tests.
"""

from __future__ import annotations

import asyncio

from splitsmith.db import Base, create_engine, sessionmaker
from splitsmith.db.workers import WorkerRecord, WorkersStore


def _fresh_store() -> WorkersStore:
    """Create an in-memory SQLite engine + WorkersStore."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    return WorkersStore(session_factory)


# - create_self_hosted() returns a plaintext token and a pending (unregistered) record
def test_create_returns_plaintext_token_and_pending_record() -> None:
    store = _fresh_store()
    record, token = asyncio.run(store.create_self_hosted("worker-1"))
    assert isinstance(record, WorkerRecord)
    assert record.name == "worker-1"
    assert record.kind == "self_hosted"
    assert record.enabled is True
    assert record.priority == 10
    # Not registered until register() is called
    assert record.registered is False
    # token_urlsafe(32) always produces 43 chars
    assert isinstance(token, str)
    assert len(token) == 43
    # Record is in the store
    fetched = asyncio.run(store.get(record.id))
    assert fetched is not None
    assert fetched.id == record.id


# - register() succeeds once, second call with the same token returns None
def test_register_succeeds_once_second_call_returns_none() -> None:
    store = _fresh_store()
    _record, reg_token = asyncio.run(store.create_self_hosted("worker-reg"))
    result = asyncio.run(store.register(reg_token, {"hostname": "box-1"}))
    assert result is not None
    worker_record, worker_token = result
    assert worker_record.registered is True
    assert isinstance(worker_token, str)
    assert len(worker_token) == 43
    assert worker_record.info == {"hostname": "box-1"}
    # Second call with the same plaintext registration token - must return None
    result2 = asyncio.run(store.register(reg_token, {"hostname": "box-1"}))
    assert result2 is None


# - register() with an expired token (ttl_hours=0) returns None
def test_expired_registration_token_returns_none() -> None:
    store = _fresh_store()
    _record, reg_token = asyncio.run(store.create_self_hosted("worker-exp", ttl_hours=0))
    # token_expires_at is in the past by the time we call register()
    result = asyncio.run(store.register(reg_token, {}))
    assert result is None


# - authenticate() round-trips the worker token, rejects garbage
def test_authenticate_roundtrips_worker_token_and_rejects_garbage() -> None:
    store = _fresh_store()
    _record, reg_token = asyncio.run(store.create_self_hosted("worker-auth"))
    result = asyncio.run(store.register(reg_token, {}))
    assert result is not None
    worker_record, worker_token = result
    # Correct worker token
    authed = asyncio.run(store.authenticate(worker_token))
    assert authed is not None
    assert authed.id == worker_record.id
    assert authed.registered is True
    # Garbage token
    assert asyncio.run(store.authenticate("not-a-real-token")) is None


# - authenticate() returns None for unregistered workers (no worker_token_hash set)
def test_authenticate_returns_none_for_unregistered() -> None:
    store = _fresh_store()
    _record, _reg_token = asyncio.run(store.create_self_hosted("worker-unreg"))
    # Has no worker token yet - the raw registration token must not authenticate
    assert asyncio.run(store.authenticate(_reg_token)) is None


# - update() flips enabled and priority
def test_update_flips_enabled_priority() -> None:
    store = _fresh_store()
    record, _ = asyncio.run(store.create_self_hosted("worker-upd", priority=5))
    assert record.enabled is True
    assert record.priority == 5
    updated = asyncio.run(store.update(record.id, enabled=False, priority=20))
    assert updated is not None
    assert updated.enabled is False
    assert updated.priority == 20
    # update() with no changes returns the record unchanged
    same = asyncio.run(store.update(record.id))
    assert same is not None
    assert same.enabled is False
    assert same.priority == 20


# - update() unknown id returns None
def test_update_unknown_id_returns_none() -> None:
    store = _fresh_store()
    result = asyncio.run(store.update("not-a-real-id", enabled=True))
    assert result is None


# - delete() removes a self_hosted worker and returns True
def test_delete_self_hosted_returns_true() -> None:
    store = _fresh_store()
    record, _ = asyncio.run(store.create_self_hosted("worker-del"))
    assert asyncio.run(store.delete(record.id)) is True
    assert asyncio.run(store.get(record.id)) is None


# - delete() refuses kind="railway" and returns False
def test_delete_refuses_railway() -> None:
    store = _fresh_store()
    asyncio.run(store.ensure_railway_row())
    workers = asyncio.run(store.list())
    railway = next(w for w in workers if w.kind == "railway")
    assert asyncio.run(store.delete(railway.id)) is False
    # Row still present
    assert asyncio.run(store.get(railway.id)) is not None


# - ensure_railway_row() is idempotent and preserves a flipped enabled=False
def test_ensure_railway_row_idempotent_and_preserves_enabled() -> None:
    store = _fresh_store()
    asyncio.run(store.ensure_railway_row())
    workers = asyncio.run(store.list())
    railway_rows = [w for w in workers if w.kind == "railway"]
    assert len(railway_rows) == 1
    railway = railway_rows[0]
    assert railway.name == "railway"
    assert railway.enabled is True
    assert railway.priority == 100
    assert railway.registered is True  # seeded as registered so list_enabled includes it
    # Flip enabled off
    asyncio.run(store.update(railway.id, enabled=False))
    # Call ensure again - must NOT reset enabled back to True
    asyncio.run(store.ensure_railway_row())
    workers2 = asyncio.run(store.list())
    railway2 = next(w for w in workers2 if w.kind == "railway")
    assert railway2.enabled is False  # preserved
    # Still only one row
    assert len([w for w in workers2 if w.kind == "railway"]) == 1


# - list_enabled() excludes disabled, excludes unregistered self-hosted, includes railway
def test_list_enabled_excludes_disabled_and_unregistered_but_includes_railway() -> None:
    store = _fresh_store()
    # Create registered worker
    record_r, reg_token_r = asyncio.run(store.create_self_hosted("worker-registered", priority=5))
    asyncio.run(store.register(reg_token_r, {}))
    # Create unregistered worker (pending registration)
    _record_u, _reg_token_u = asyncio.run(store.create_self_hosted("worker-unregistered", priority=6))
    # Create disabled worker (registered but disabled)
    record_d, reg_token_d = asyncio.run(store.create_self_hosted("worker-disabled", priority=7))
    asyncio.run(store.register(reg_token_d, {}))
    asyncio.run(store.update(record_d.id, enabled=False))
    # Ensure railway row
    asyncio.run(store.ensure_railway_row())
    enabled = asyncio.run(store.list_enabled())
    names = {w.name for w in enabled}
    # Registered + enabled self-hosted should appear
    assert "worker-registered" in names
    # Railway should appear even though it was "registered" synthetically
    assert "railway" in names
    # Disabled and unregistered must NOT appear
    assert "worker-disabled" not in names
    assert "worker-unregistered" not in names


# - list() order is by priority asc, name asc
def test_list_order_priority_then_name() -> None:
    store = _fresh_store()
    asyncio.run(store.create_self_hosted("b-worker", priority=10))
    asyncio.run(store.create_self_hosted("a-worker", priority=10))
    asyncio.run(store.create_self_hosted("c-worker", priority=5))
    workers = asyncio.run(store.list())
    names = [w.name for w in workers]
    assert names == ["c-worker", "a-worker", "b-worker"]


# - touch_seen() updates last_seen_at without error
def test_touch_seen_updates_last_seen_at() -> None:
    store = _fresh_store()
    record, _ = asyncio.run(store.create_self_hosted("worker-ts"))
    assert asyncio.run(store.get(record.id)).last_seen_at is None  # type: ignore[union-attr]
    asyncio.run(store.touch_seen(record.id))
    updated = asyncio.run(store.get(record.id))
    assert updated is not None
    assert updated.last_seen_at is not None


# - register() stamps the version column from info["agent_version"]
def test_register_stamps_version_from_info() -> None:
    store = _fresh_store()
    _record, reg_token = asyncio.run(store.create_self_hosted("worker-ver"))
    result = asyncio.run(store.register(reg_token, {"agent_version": "1.2.3", "hostname": "box-1"}))
    assert result is not None
    worker_record, _worker_token = result
    assert worker_record.version == "1.2.3"


# - register() leaves version None when info carries no agent_version
def test_register_version_none_without_agent_version() -> None:
    store = _fresh_store()
    _record, reg_token = asyncio.run(store.create_self_hosted("worker-nover"))
    result = asyncio.run(store.register(reg_token, {"hostname": "box-1"}))
    assert result is not None
    worker_record, _worker_token = result
    assert worker_record.version is None


# - touch_seen(version=...) refreshes the version column
def test_touch_seen_updates_version_when_provided() -> None:
    store = _fresh_store()
    record, _ = asyncio.run(store.create_self_hosted("worker-tsv"))
    asyncio.run(store.touch_seen(record.id, version="9.9.9"))
    updated = asyncio.run(store.get(record.id))
    assert updated is not None
    assert updated.version == "9.9.9"


# - touch_seen() without a version leaves a previously-stamped version intact
def test_touch_seen_without_version_preserves_existing_version() -> None:
    store = _fresh_store()
    record, _ = asyncio.run(store.create_self_hosted("worker-tsv2"))
    asyncio.run(store.touch_seen(record.id, version="1.0.0"))
    asyncio.run(store.touch_seen(record.id))  # no version arg
    updated = asyncio.run(store.get(record.id))
    assert updated is not None
    assert updated.version == "1.0.0"


# - ensure_railway_row(version=...) stamps the version on create AND on an
#   already-existing row, without disturbing operator enabled/priority settings
def test_ensure_railway_row_stamps_version_create_and_existing() -> None:
    store = _fresh_store()
    asyncio.run(store.ensure_railway_row(version="0.8.4"))
    railway = next(w for w in asyncio.run(store.list()) if w.kind == "railway")
    assert railway.version == "0.8.4"
    # Operator flips it off; a redeploy at a newer version calls ensure again
    asyncio.run(store.update(railway.id, enabled=False))
    asyncio.run(store.ensure_railway_row(version="0.9.0"))
    railway2 = next(w for w in asyncio.run(store.list()) if w.kind == "railway")
    assert railway2.version == "0.9.0"  # refreshed to the running deploy
    assert railway2.enabled is False  # operator setting preserved


# - touch_wake() updates last_wake_at for each id in the list
def test_touch_wake_updates_last_wake_at() -> None:
    store = _fresh_store()
    r1, _ = asyncio.run(store.create_self_hosted("worker-tw1"))
    r2, _ = asyncio.run(store.create_self_hosted("worker-tw2"))
    asyncio.run(store.touch_wake([r1.id, r2.id]))
    updated1 = asyncio.run(store.get(r1.id))
    updated2 = asyncio.run(store.get(r2.id))
    assert updated1 is not None and updated1.last_wake_at is not None
    assert updated2 is not None and updated2.last_wake_at is not None
