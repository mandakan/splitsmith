"""The hosted shooter book and account profile (spec 2026-10-08): per-user
isolation for every method, files in the tenant's own storage, the one-time
fill. SQLite in-memory via aiosqlite, the harness the other store tests use;
``FilesystemStorage`` rooted per user stands in for the tenant's ``users/<id>/``
prefix."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from splitsmith.account_profile import AccountProfile, resolve_brand
from splitsmith.db import (
    Base,
    PostgresAccountProfileStore,
    PostgresShooterBookStore,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.identity import ShooterIdentity
from splitsmith.look_brand import BrandError
from splitsmith.looks import LookBrand
from splitsmith.shooter_book import BackfillCandidate, ShooterBookEntry, ShooterBookStore, save_identity
from splitsmith.storage import FilesystemStorage

from .test_shooter_book import _png


def run(coro):
    return asyncio.run(coro)


def _users(tmp_path: Path, *, backfill_source=None):
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            a, b = User(email="a@example.com"), User(email="b@example.com")
            s.add_all([a, b])
            await s.commit()
            await s.refresh(a)
            await s.refresh(b)
            return a.id, b.id

    a_id, b_id = run(_setup())
    stores = {}
    for uid in (a_id, b_id):
        storage = FilesystemStorage(tmp_path / "bucket" / "users" / uid)
        stores[uid] = (
            PostgresShooterBookStore(
                sf,
                user_id=uid,
                storage=storage,
                cache_dir=tmp_path / "cache",
                backfill_source=backfill_source,
            ),
            PostgresAccountProfileStore(sf, user_id=uid, storage=storage, cache_dir=tmp_path / "cache"),
        )
    return stores[a_id], stores[b_id], (a_id, b_id)


def _entry(sid: int, **identity: str) -> ShooterBookEntry:
    return ShooterBookEntry(shooter_id=sid, identity=ShooterIdentity(**identity), label="Anna")


def test_satisfies_the_protocol(tmp_path: Path) -> None:
    (book, _), _, _ = _users(tmp_path)
    typed: ShooterBookStore = book
    assert typed is book


@pytest.mark.parametrize("bad", ["", None, 0])
def test_construction_rejects_an_empty_user_id(tmp_path: Path, bad) -> None:
    sf = sessionmaker(create_engine("sqlite+aiosqlite:///:memory:"))
    with pytest.raises(ValueError):
        PostgresShooterBookStore(sf, user_id=bad, storage=None, cache_dir=tmp_path)
    with pytest.raises(ValueError):
        PostgresAccountProfileStore(sf, user_id=bad, storage=None, cache_dir=tmp_path)


def test_book_round_trip_and_replace(tmp_path: Path) -> None:
    (book, _), _, _ = _users(tmp_path)
    run(book.put(_entry(42, accent="#aa0000")))
    run(book.put(_entry(42, club="Bromma")))
    run(book.put(_entry(7, club="X")))
    assert [e.shooter_id for e in run(book.list())] == [7, 42]
    assert run(book.get(42)).identity == ShooterIdentity(club="Bromma")
    run(book.delete(42))
    assert run(book.get(42)) is None


def test_every_book_method_is_confined_to_its_user(tmp_path: Path) -> None:
    (a, _), (b, _), _ = _users(tmp_path)
    run(a.put(_entry(42, accent="#aa0000")))
    assert run(b.get(42)) is None
    assert run(b.list()) == []
    assert run(b.snapshot()).entries == {}
    run(b.delete(42))
    assert run(a.get(42)) is not None, "another user's delete reaches nothing"
    run(b.put(_entry(42, accent="#00aa00")))
    assert run(a.get(42)).identity.accent == "#aa0000"


def test_logos_live_in_the_users_own_storage_and_mirror_to_the_cache(tmp_path: Path) -> None:
    (a, _), (b, _), (a_id, _b_id) = _users(tmp_path)
    name = run(a.put_logo(_png()))
    assert (tmp_path / "bucket" / "users" / a_id / "account" / "files" / name).read_bytes() == _png()
    path = run(a.logo_file(name))
    assert path is not None and path.read_bytes() == _png()
    assert run(b.logo_file(name)) is None, "another user's storage has no such file"
    for bad in ("../" + name, "", ".hidden", "a/b.png"):
        assert run(a.logo_file(bad)) is None
    with pytest.raises(ValueError):
        run(a.put_logo(b"<svg/>"))


def test_a_snapshot_draws_the_logo_and_leaves_out_one_that_is_gone(tmp_path: Path) -> None:
    (a, _), _, (a_id, _) = _users(tmp_path)
    name = run(a.put_logo(_png()))
    run(a.put(_entry(42, logo=name)))
    snap = run(a.snapshot())
    assert snap.logo_path(snap.get(42)) is not None
    (tmp_path / "bucket" / "users" / a_id / "account" / "files" / name).unlink()
    for cached in (tmp_path / "cache").rglob(name):
        cached.unlink()
    snap = run(a.snapshot())
    assert snap.get(42) is not None and snap.logo_path(snap.get(42)) is None


def test_no_file_store_refuses_a_logo(tmp_path: Path) -> None:
    sf = sessionmaker(create_engine("sqlite+aiosqlite:///:memory:"))
    with pytest.raises(ValueError, match="no file store"):
        run(PostgresShooterBookStore(sf, user_id="u", storage=None, cache_dir=tmp_path).put_logo(_png()))
    with pytest.raises(BrandError):
        run(
            PostgresAccountProfileStore(sf, user_id="u", storage=None, cache_dir=tmp_path).put_brand_logo(
                _png()
            )
        )


# --- the profile ----------------------------------------------------------------


def test_profile_round_trip_and_brand_isolation(tmp_path: Path) -> None:
    (_, a), (_, b), _ = _users(tmp_path)
    assert run(a.load()) == AccountProfile()
    name = run(a.put_brand_logo(_png()))
    run(a.save(AccountProfile(brand=LookBrand(logo=name, line="Team Axell"))))
    brand = run(resolve_brand(a))
    assert brand is not None and brand.line == "Team Axell" and brand.logo_path.read_bytes() == _png()
    assert run(b.load()) == AccountProfile()
    assert run(b.brand_file(name)) is None
    assert run(a.brand_file("../" + name)) is None


# --- the one-time fill ------------------------------------------------------------


def _candidate(sid: int, club: str, minutes: int, logo: str | None = None, data: bytes | None = None):
    return BackfillCandidate(
        shooter_id=sid,
        identity=ShooterIdentity(club=club, logo=logo),
        label="X",
        updated_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        read_logo=lambda: data,
    )


def test_the_fill_runs_once_and_the_most_recent_match_wins(tmp_path: Path) -> None:
    from splitsmith.identity import logo_name

    calls: list[int] = []
    logo = logo_name(_png(), "png")

    async def source():
        calls.append(1)
        return [
            _candidate(42, "Old club", 1),
            _candidate(42, "New club", 5, logo=logo, data=_png()),
            _candidate(7, "Stockholm", 2),
        ]

    (a, _), (b, _), _ = _users(tmp_path, backfill_source=source)
    entries = {e.shooter_id: e for e in run(a.list())}
    assert entries[42].identity.club == "New club" and entries[42].identity.logo == logo
    assert run(a.logo_file(logo)) is not None
    assert entries[7].identity.club == "Stockholm"
    run(a.delete(7))
    run(a.list())
    assert 7 not in {e.shooter_id for e in run(a.list())}, "the fill never runs again"
    # The other user's fill is their own, and leaves an entry they already had.
    assert len(calls) == 1
    run(b.put(_entry(42, club="Mine")))
    run(b.list())
    assert run(b.get(42)).identity.club == "Mine"
    assert len(calls) == 2


def test_a_fill_that_fails_is_tried_again(tmp_path: Path) -> None:
    attempts: list[int] = []

    async def source():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("db hiccup")
        return [_candidate(42, "Club", 1)]

    (a, _), _, _ = _users(tmp_path, backfill_source=source)
    assert run(a.list()) == []
    assert [e.shooter_id for e in run(a.list())] == [42]


def test_save_identity_through_the_hosted_store_copies_the_logo(tmp_path: Path) -> None:
    from splitsmith.identity import logo_name

    (a, _), _, _ = _users(tmp_path)
    name = logo_name(_png(), "png")
    run(save_identity(a, shooter_id=42, identity=ShooterIdentity(logo=name), label="A", logo_bytes=_png()))
    assert run(a.get(42)).identity.logo == name
    assert run(a.logo_file(name)) is not None
