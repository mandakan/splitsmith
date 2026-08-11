"""``DeviceAuthStore`` -- the device-flow state machine (#719).

Driven directly against a SQLite state store, no HTTP. The route layer
(tests/test_device_auth_routes.py) exercises the same store wired into
FastAPI; this file pins the behaviour the routes only pass through:

  - authorize mints a hashed device code and a formatted user code
  - poll before approval reports pending, and throttles to slow_down
  - approve -> poll mints exactly one desktop_tokens row, scope 'sync'
  - a SECOND poll after that reports expired, and mints nothing more
  - deny -> poll reports denied
  - an expired authorization reports expired even when approved
  - an unknown device code reports expired (no probing for live codes)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from splitsmith.db import Base, DesktopTokenRow, DeviceAuthorizationRow, User, create_engine, sessionmaker
from splitsmith.db.device_auth import DeviceAuthStore, normalize_user_code


def _factory(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'device.sqlite'}")

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    return sessionmaker(engine)


async def _seed_user(sf, email: str = "owner@example.com") -> str:
    async with sf() as s:
        user = User(email=email)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


def test_normalize_user_code_is_forgiving_about_case_and_hyphens() -> None:
    assert normalize_user_code("abcd2345") == "ABCD-2345"
    assert normalize_user_code("ABCD-2345") == "ABCD-2345"
    assert normalize_user_code(" abcd 2345 ") == "ABCD-2345"


def test_authorize_stores_only_the_hash(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)

    async def _run() -> tuple[str, str]:
        req = await store.authorize("mac studio")
        async with sf() as s:
            row = (
                await s.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.user_code == req.user_code)
                )
            ).scalar_one()
            return req.device_code, row.device_code_hash

    plain, hashed = asyncio.run(_run())
    assert plain
    assert plain != hashed
    assert hashed != ""


def test_poll_before_approval_is_pending_then_slow_down(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=60)

    async def _run() -> tuple[str, str]:
        req = await store.authorize("mac studio")
        first = await store.poll(req.device_code)
        second = await store.poll(req.device_code)
        return first.status, second.status

    assert asyncio.run(_run()) == ("pending", "slow_down")


def test_approve_then_poll_mints_exactly_one_sync_token(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[str, str, int, str, str]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        decided = await store.decide(req.user_code, user_id=uid, approved=True)
        assert decided is True
        first = await store.poll(req.device_code)
        second = await store.poll(req.device_code)
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return first.status, second.status, len(rows), rows[0].scope, rows[0].name

    status, second_status, count, scope, name = asyncio.run(_run())
    assert status == "approved"
    assert second_status == "expired"
    assert count == 1
    assert scope == "sync"
    assert name == "mac studio"


def test_approved_poll_returns_the_token_and_account(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run():
        uid = await _seed_user(sf, "shooter@example.com")
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        return await store.poll(req.device_code)

    result = asyncio.run(_run())
    assert result.token
    assert result.account is not None
    assert result.account.email == "shooter@example.com"
    assert result.device_name == "mac studio"


def test_denied_poll_reports_denied(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> str:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=False)
        return (await store.poll(req.device_code)).status

    assert asyncio.run(_run()) == "denied"


def test_expired_authorization_reports_expired_even_when_approved(tmp_path: Path) -> None:
    """An approval that sat past the window must not mint a token."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[str, int]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        async with sf() as s:
            row = (
                await s.execute(
                    select(DeviceAuthorizationRow).where(DeviceAuthorizationRow.user_code == req.user_code)
                )
            ).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await s.commit()
        result = await store.poll(req.device_code)
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return result.status, len(rows)

    assert asyncio.run(_run()) == ("expired", 0)


def test_unknown_device_code_reports_expired(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)
    assert asyncio.run(store.poll("no-such-device-code")).status == "expired"


def test_pending_returns_none_for_unknown_or_expired_user_code(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, ttl_seconds=0)

    async def _run() -> tuple[object, object]:
        unknown = await store.pending("ZZZZ-9999")
        req = await store.authorize("mac studio")
        stale = await store.pending(req.user_code)
        return unknown, stale

    unknown, stale = asyncio.run(_run())
    assert unknown is None
    assert stale is None


def test_pending_returns_the_live_authorization(tmp_path: Path) -> None:
    """The happy path ``pending`` is meant to serve the approval screen -
    device_name, scope, and an aware created_at read back from
    server_default=func.now()."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)

    async def _run():
        req = await store.authorize("mac studio")
        return await store.pending(req.user_code)

    result = asyncio.run(_run())
    assert result is not None
    assert result.device_name == "mac studio"
    assert result.scope == "sync"
    assert result.created_at.tzinfo is not None


def test_two_concurrent_polls_mint_one_token(tmp_path: Path) -> None:
    """The conditional approved -> consumed update is the only thing
    stopping two in-flight polls from each minting a credential."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[list[str], int]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        results = await asyncio.gather(
            store.poll(req.device_code),
            store.poll(req.device_code),
            return_exceptions=True,
        )
        statuses = [r.status for r in results if not isinstance(r, BaseException)]
        async with sf() as s:
            rows = list((await s.execute(select(DesktopTokenRow))).scalars())
        return sorted(statuses), len(rows)

    statuses, count = asyncio.run(_run())
    assert count == 1
    assert sorted(statuses) == ["approved", "expired"]


def test_revoke_token_revokes_the_matching_row(tmp_path: Path) -> None:
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf, interval_seconds=0)

    async def _run() -> tuple[bool, bool]:
        uid = await _seed_user(sf)
        req = await store.authorize("mac studio")
        await store.decide(req.user_code, user_id=uid, approved=True)
        result = await store.poll(req.device_code)
        assert result.token is not None
        revoked = await store.revoke_token(result.token)
        async with sf() as s:
            row = (await s.execute(select(DesktopTokenRow))).scalar_one()
            return revoked, row.revoked_at is not None

    assert asyncio.run(_run()) == (True, True)


def test_authorize_sweeps_rows_a_day_past_expiry(tmp_path: Path) -> None:
    """#735: the unauthenticated authorize endpoint is the only growth
    source, so sweeping on insert is what bounds the table."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)

    async def _run() -> tuple[list[str], set[str], str]:
        stale = await store.authorize("stale-device")
        async with sf() as s:
            await s.execute(
                update(DeviceAuthorizationRow).values(expires_at=datetime.now(UTC) - timedelta(days=2))
            )
            await s.commit()

        await store.authorize("fresh-device")

        async with sf() as s:
            rows = (await s.execute(select(DeviceAuthorizationRow))).scalars().all()
        return [r.device_name for r in rows], {r.user_code for r in rows}, stale.user_code

    names, codes, stale_code = asyncio.run(_run())
    assert names == ["fresh-device"]
    assert stale_code not in codes


def test_authorize_keeps_recently_expired_rows(tmp_path: Path) -> None:
    """Rows inside the one-day grace stay: an expired-but-recent code
    still answers polls with a proper 'expired' verdict."""
    sf = _factory(tmp_path)
    store = DeviceAuthStore(sf)

    async def _run() -> list[str]:
        await store.authorize("recent-device")
        async with sf() as s:
            await s.execute(
                update(DeviceAuthorizationRow).values(expires_at=datetime.now(UTC) - timedelta(hours=1))
            )
            await s.commit()

        await store.authorize("fresh-device")

        async with sf() as s:
            rows = (await s.execute(select(DeviceAuthorizationRow))).scalars().all()
        return sorted(r.device_name for r in rows)

    assert asyncio.run(_run()) == ["fresh-device", "recent-device"]
