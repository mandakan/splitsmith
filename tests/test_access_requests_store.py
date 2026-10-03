from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.access_requests import (
    AccessRequestStore,
    AccountAccessStore,
    AlreadyDecidedError,
    NotFoundError,
)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 3, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[object]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    yield sessionmaker(engine)


def run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_record_creates_once_and_dedupes(factory) -> None:  # noqa: ANN001
    clock = Clock()
    store = AccessRequestStore(factory, now=clock)
    assert run(store.record("A@X.se ", source="login")) is True
    clock.t += timedelta(hours=1)
    assert run(store.record("a@x.se", source="form", note="Erik, Bromma")) is False
    [row] = run(store.list())
    assert row.email == "a@x.se"
    assert row.source == "login"  # the first source sticks
    assert row.note == "Erik, Bromma"  # an empty note is filled
    assert row.last_requested_at > row.requested_at


def test_note_is_not_overwritten_and_is_capped(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form", note="first"))
    run(store.record("a@x.se", source="form", note="second"))
    assert run(store.list())[0].note == "first"
    run(store.record("b@x.se", source="form", note="x" * 900))
    assert len(next(r for r in run(store.list()) if r.email == "b@x.se").note) == 500


def test_existing_account_records_nothing(factory) -> None:  # noqa: ANN001
    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="me@x.se", access_tier="full"))
            await s.commit()

    run(seed())
    store = AccessRequestStore(factory, now=Clock())
    assert run(store.record("me@x.se", source="login")) is False
    assert run(store.list()) == []


def test_approve_creates_the_user_with_the_tier(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    view = run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))
    assert (view.status, view.tier_granted, view.decided_by) == ("approved", "sharing", "boss@x.se")

    async def tier() -> tuple[str, object]:
        async with factory() as s:
            row = (await s.execute(select(User).where(User.email == "a@x.se"))).scalar_one()
            return row.access_tier, row.email_verified_at

    assert run(tier()) == ("sharing", None)


def test_approve_existing_account_sets_tier(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id

    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="a@x.se", access_tier="full"))
            await s.commit()

    run(seed())  # they got in through the env allowlist meanwhile
    run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))

    async def users() -> list[tuple[str, str]]:
        async with factory() as s:
            return [(u.email, u.access_tier) for u in (await s.execute(select(User))).scalars()]

    assert run(users()) == [("a@x.se", "sharing")]


def test_double_decide_is_refused(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))
    with pytest.raises(AlreadyDecidedError):
        run(store.approve(rid, tier="full", admin_email="other@x.se"))
    with pytest.raises(AlreadyDecidedError):
        run(store.decline(rid, admin_email="other@x.se"))


def test_declined_stays_declined_on_repeat_but_can_be_approved(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.decline(rid, admin_email="boss@x.se"))
    assert run(store.record("a@x.se", source="login")) is False
    assert run(store.list())[0].status == "declined"
    assert run(store.approve(rid, tier="sharing", admin_email="boss@x.se")).status == "approved"


def test_list_puts_pending_first(factory) -> None:  # noqa: ANN001
    clock = Clock()
    store = AccessRequestStore(factory, now=clock)
    run(store.record("old@x.se", source="form"))
    clock.t += timedelta(minutes=1)
    run(store.record("new@x.se", source="form"))
    run(store.decline(run(store.list())[-1].id, admin_email="boss@x.se"))
    assert [r.status for r in run(store.list())][0] == "pending"
    assert [r.email for r in run(store.list(status="declined"))] == ["old@x.se"]


def test_account_store_sets_tier_and_404s(factory) -> None:  # noqa: ANN001
    async def seed() -> str:
        async with factory() as s:
            u = User(email="a@x.se", access_tier="full")
            s.add(u)
            await s.commit()
            return u.id

    uid = run(seed())
    accounts = AccountAccessStore(factory)
    assert run(accounts.set_tier(uid, "sharing")).access_tier == "sharing"
    with pytest.raises(NotFoundError):
        run(accounts.set_tier("nope", "full"))


def test_empty_note_does_not_block_a_later_note(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form", note=""))
    run(store.record("a@x.se", source="form", note="real"))
    assert run(store.list())[0].note == "real"


def test_whitespace_only_note_does_not_block_a_later_note(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form", note="   "))
    run(store.record("a@x.se", source="form", note="real"))
    assert run(store.list())[0].note == "real"


def test_set_tier_404s_on_soft_deleted_user(factory) -> None:  # noqa: ANN001
    async def seed() -> str:
        async with factory() as s:
            u = User(email="a@x.se", access_tier="full", deleted_at=datetime(2026, 10, 1, tzinfo=UTC))
            s.add(u)
            await s.commit()
            return u.id

    uid = run(seed())
    accounts = AccountAccessStore(factory)
    with pytest.raises(NotFoundError):
        run(accounts.set_tier(uid, "sharing"))


def test_account_store_list_excludes_soft_deleted(factory) -> None:  # noqa: ANN001
    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="a@x.se", access_tier="full"))
            s.add(User(email="b@x.se", access_tier="full", deleted_at=datetime(2026, 10, 1, tzinfo=UTC)))
            await s.commit()

    run(seed())
    accounts = AccountAccessStore(factory)
    assert [a.email for a in run(accounts.list())] == ["a@x.se"]


def test_approve_refuses_soft_deleted_account_and_leaves_request_unchanged(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id

    async def seed() -> None:
        async with factory() as s:
            s.add(User(email="a@x.se", access_tier="full", deleted_at=datetime(2026, 10, 1, tzinfo=UTC)))
            await s.commit()

    run(seed())
    with pytest.raises(NotFoundError):
        run(store.approve(rid, tier="sharing", admin_email="boss@x.se"))

    view = run(store.get(rid))
    assert view.status == "pending"
    assert view.tier_granted is None
    assert view.decided_by is None


def test_approve_decline_get_404_on_unknown_id(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    with pytest.raises(NotFoundError):
        run(store.approve("nope", tier="full", admin_email="boss@x.se"))
    with pytest.raises(NotFoundError):
        run(store.decline("nope", admin_email="boss@x.se"))
    with pytest.raises(NotFoundError):
        run(store.get("nope"))


def test_mark_email_sent_sets_timestamp_and_404s(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.mark_email_sent(rid))
    assert run(store.get(rid)).email_sent_at is not None
    with pytest.raises(NotFoundError):
        run(store.mark_email_sent("nope"))


def test_decline_of_already_declined_request_is_refused(factory) -> None:  # noqa: ANN001
    store = AccessRequestStore(factory, now=Clock())
    run(store.record("a@x.se", source="form"))
    rid = run(store.list())[0].id
    run(store.decline(rid, admin_email="boss@x.se"))
    with pytest.raises(AlreadyDecidedError):
        run(store.decline(rid, admin_email="boss@x.se"))
