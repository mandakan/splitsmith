"""What's new (``splitsmith.whats_new``): the shipped entries, their style,
who has seen what, and the routes, local and hosted.

The style tests are the enforcement half of ``.claude/skills/whats-new``:
an entry that breaks a rule there fails here, so the sheet reads the same
release after release.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import user_config, whats_new
from splitsmith.db import Base, PostgresWhatsNewStore, User, create_engine, sessionmaker
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _match_create_app, _MatchClient

ENTRIES = whats_new.load_entries()

# --- the shipped entries and their style ------------------------------------------------

SLOP = (
    "delve",
    "leverage",
    "seamless",
    "robust",
    "paradigm",
    "holistic",
    "cutting-edge",
    "game-changer",
    "synergy",
    "ultimately",
    "unlock",
    "harness",
    "state-of-the-art",
    "powerful",
    "exciting",
    "we're thrilled",
)


def test_there_are_entries_with_unique_ids_newest_first() -> None:
    assert ENTRIES
    ids = [e.id for e in ENTRIES]
    assert len(set(ids)) == len(ids)
    dates = [e.date for e in ENTRIES]
    assert dates == sorted(dates, reverse=True)
    assert all(e.date <= date.today() for e in ENTRIES)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.id)
def test_each_entry_reads_like_the_others(entry: whats_new.WhatsNewEntry) -> None:
    text = f"{entry.title}\n{entry.body}"
    assert len(entry.title) <= whats_new.MAX_TITLE, "title: one short line"
    assert len(entry.body) <= whats_new.MAX_BODY, "body: two sentences at most"
    assert not entry.title.endswith("."), "a title is a phrase, not a sentence"
    assert entry.body.endswith("."), "the body is whole sentences"
    assert text.isascii(), "ASCII only: no curly quotes, ellipses or long dashes"
    assert " -- " not in text and " - " not in text, "no dash as punctuation"
    assert not re.search(r"#\d", text), "no issue numbers in the UI"
    assert "!" not in text, "no exclamation marks"
    lowered = text.lower()
    assert not [w for w in SLOP if w in lowered], "no slop or hype"
    assert not re.search(r"\b(we|our|us)\b", lowered), "address the user, not the team"


def test_a_chip_key_is_a_slug_and_used_once() -> None:
    chips = [e.chip for e in ENTRIES if e.chip]
    assert len(set(chips)) == len(chips)
    assert all(re.fullmatch(r"[a-z0-9-]+", c) for c in chips)


def test_a_bad_entries_file_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "whats_new.json"
    bad.write_text('{"entries": [{"id": "Not A Slug", "date": "2026-10-07", "title": "x", "body": "y."}]}')
    with pytest.raises(ValueError):
        whats_new.load_entries(bad)


# --- who has seen what -------------------------------------------------------------------


def test_a_user_with_no_matches_starts_with_everything_seen() -> None:
    assert whats_new.first_seen(ENTRIES, has_matches=False) == [e.id for e in ENTRIES]
    assert whats_new.first_seen(ENTRIES, has_matches=True) == []


def test_the_local_store_keeps_its_set_in_the_global_prefs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    store = whats_new.PrefsWhatsNewStore()
    assert asyncio.run(store.get()) is None
    assert asyncio.run(store.add(["b", "a"])) == ["a", "b"]
    assert asyncio.run(store.add(["a", "c"])) == ["a", "b", "c"]
    assert user_config.load_global_prefs().whats_new_seen == ["a", "b", "c"]


def _two_users() -> tuple[PostgresWhatsNewStore, PostgresWhatsNewStore]:
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

    a_id, b_id = asyncio.run(_setup())
    return PostgresWhatsNewStore(sf, user_id=a_id), PostgresWhatsNewStore(sf, user_id=b_id)


def test_the_hosted_store_is_per_user() -> None:
    a, b = _two_users()
    assert asyncio.run(a.get()) is None
    assert asyncio.run(a.add(["x"])) == ["x"]
    assert asyncio.run(b.get()) is None
    assert asyncio.run(b.add(["y"])) == ["y"]
    assert asyncio.run(a.get()) == ["x"]


@pytest.mark.parametrize("bad", ["", None])
def test_the_hosted_store_needs_a_user(bad) -> None:
    with pytest.raises(ValueError):
        PostgresWhatsNewStore(sessionmaker(create_engine("sqlite+aiosqlite:///:memory:")), user_id=bad)


# --- the routes ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    app = _match_create_app(project_root=tmp_path / "match", project_name="News")
    return _MatchClient(app)


def test_get_lists_the_entries_and_marking_seen_sticks(client) -> None:
    first = client.get("/api/whats-new").json()
    assert [e["id"] for e in first["entries"]] == [e.id for e in ENTRIES]
    unseen = [i for i in (e["id"] for e in first["entries"]) if i not in first["seen"]]
    r = client.post("/api/whats-new/seen", json={"ids": unseen[:1]})
    assert r.status_code == 200
    assert unseen[0] in client.get("/api/whats-new").json()["seen"]


def test_unknown_ids_are_refused(client) -> None:
    assert client.post("/api/whats-new/seen", json={"ids": ["no-such-entry"]}).status_code == 422
    assert client.post("/api/whats-new/seen", json={"ids": ["chip:no-such-chip"]}).status_code == 422


def test_hosted_a_new_account_starts_with_nothing_new_and_its_seen_set_is_its_own(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    first = client.get("/api/whats-new").json()
    assert sorted(first["seen"]) == sorted(e.id for e in ENTRIES)
    chip = next((e.chip for e in ENTRIES if e.chip), None)
    if chip is not None:
        assert client.post("/api/whats-new/seen", json={"ids": [f"chip:{chip}"]}).status_code == 200
        other = TestClient(client.app, follow_redirects=False)
        login(other, sender, "b@example.com")
        assert f"chip:{chip}" not in other.get("/api/whats-new").json()["seen"]
        assert f"chip:{chip}" in client.get("/api/whats-new").json()["seen"]


def test_hosted_needs_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.get("/api/whats-new").status_code in (401, 403)
