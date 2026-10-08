"""The shooter book and account profile stores (spec 2026-10-08)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.account_profile import (
    AccountProfile,
    EmptyAccountProfileStore,
    JsonAccountProfileStore,
    load_brand,
)
from splitsmith.identity import ShooterIdentity
from splitsmith.look_brand import BrandError
from splitsmith.looks import LookBrand
from splitsmith.shooter_book import (
    EMPTY_BOOK,
    EmptyShooterBookStore,
    JsonShooterBookStore,
    ShooterBookEntry,
    account_dir,
    is_set,
    load_snapshot,
)


def _png(color=(200, 30, 30), size=(32, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def run(coro):
    return asyncio.run(coro)


def test_put_then_get_round_trips_an_entry(tmp_path: Path) -> None:
    store = JsonShooterBookStore(tmp_path)
    entry = ShooterBookEntry(
        shooter_id=42, identity=ShooterIdentity(accent="#AA0000", club="Bromma"), label="Anna"
    )
    run(store.put(entry))
    back = run(JsonShooterBookStore(tmp_path).get(42))
    assert back is not None and back.identity.accent == "#aa0000" and back.identity.club == "Bromma"
    assert [e.shooter_id for e in run(store.list())] == [42]
    run(store.delete(42))
    assert run(store.get(42)) is None


def test_a_logo_is_content_named_and_sniffed(tmp_path: Path) -> None:
    store = JsonShooterBookStore(tmp_path)
    name = run(store.put_logo(_png()))
    assert name.startswith("logo-") and name.endswith(".png")
    assert run(store.logo_file(name)) == tmp_path / "files" / name
    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        run(store.put_logo(b"<svg xmlns='http://www.w3.org/2000/svg'/>"))


def test_a_symlinked_logo_or_book_is_never_read(tmp_path: Path) -> None:
    store = JsonShooterBookStore(tmp_path)
    secret = tmp_path / "secret.png"
    secret.write_bytes(_png())
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "logo-000000000000.png").symlink_to(secret)
    assert run(store.logo_file("logo-000000000000.png")) is None
    real = tmp_path / "real.json"
    real.write_text('{"entries": [{"shooter_id": 1}]}')
    (tmp_path / "shooter_book.json").symlink_to(real)
    assert run(store.list()) == []


def test_missing_account_dir_reads_empty_and_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "nowhere"
    store = JsonShooterBookStore(root)
    assert run(store.list()) == []
    assert run(store.snapshot()) == EMPTY_BOOK
    assert run(JsonAccountProfileStore(root).load()) == AccountProfile()
    assert not root.exists()


def test_a_disabled_user_config_has_no_account_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_DISABLE_USER_CONFIG", "1")
    assert account_dir() is None
    assert run(JsonShooterBookStore().list()) == []


def test_snapshot_leaves_out_a_logo_that_is_gone(tmp_path: Path) -> None:
    store = JsonShooterBookStore(tmp_path)
    name = run(store.put_logo(_png()))
    run(store.put(ShooterBookEntry(shooter_id=7, identity=ShooterIdentity(logo=name))))
    snap = load_snapshot(store)
    assert snap.logo_path(snap.get(7)) == tmp_path / "files" / name
    (tmp_path / "files" / name).unlink()
    snap = load_snapshot(store)
    assert snap.get(7) is not None and snap.logo_path(snap.get(7)) is None


def test_one_malformed_entry_does_not_lose_the_book(tmp_path: Path) -> None:
    (tmp_path / "shooter_book.json").write_text(
        '{"entries": [{"shooter_id": 1, "identity": {"accent": "red"}}, {"shooter_id": 2}]}'
    )
    assert [e.shooter_id for e in run(JsonShooterBookStore(tmp_path).list())] == [2]


def test_a_failing_store_is_an_empty_book() -> None:
    class Broken(EmptyShooterBookStore):
        async def snapshot(self):
            raise RuntimeError("db down")

    assert load_snapshot(Broken()) == EMPTY_BOOK
    assert load_snapshot(None) == EMPTY_BOOK


def test_is_set_treats_an_all_none_record_as_unset() -> None:
    assert not is_set(None) and not is_set(ShooterIdentity())
    assert is_set(ShooterIdentity(club="X"))


# --- the account profile ---------------------------------------------------------


def test_the_profile_round_trips_and_resolves_a_brand(tmp_path: Path) -> None:
    store = JsonAccountProfileStore(tmp_path)
    assert load_brand(store) is None
    name = run(store.put_brand_logo(_png()))
    run(store.save(AccountProfile(brand=LookBrand(logo=name, line="Team Axell"))))
    brand = load_brand(JsonAccountProfileStore(tmp_path))
    assert brand is not None and brand.logo_path == tmp_path / "brand" / name and brand.line == "Team Axell"


def test_a_brand_logo_gone_from_disk_keeps_the_line(tmp_path: Path) -> None:
    store = JsonAccountProfileStore(tmp_path)
    name = run(store.put_brand_logo(_png()))
    run(store.save(AccountProfile(brand=LookBrand(logo=name, line="Team"))))
    (tmp_path / "brand" / name).unlink()
    brand = load_brand(store)
    assert brand is not None and brand.logo_path is None and brand.line == "Team"
    run(store.save(AccountProfile(brand=LookBrand(logo=name))))
    assert load_brand(store) is None


def test_a_brand_logo_is_checked_like_a_looks(tmp_path: Path) -> None:
    with pytest.raises(BrandError):
        run(JsonAccountProfileStore(tmp_path).put_brand_logo(b"GIF89a"))
    with pytest.raises(BrandError):
        run(EmptyAccountProfileStore().put_brand_logo(_png()))
    assert load_brand(EmptyAccountProfileStore()) is None
