"""Tests for kind=proxy streaming with transparent source fallback.

Two required cases:
  (a) proxy object present in storage -> serve proxy bytes
  (b) proxy object absent -> fall through to source bytes, not 404

Uses a hosted-mode app wired to FilesystemStorage (same Protocol as
S3Storage, no moto dependency). The _tenant_s3_storage factory is
monkeypatched so every request's tenant context gets the test backing
store. Session auth is seeded directly to skip the magic-link dance.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import Base, MatchRow, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.storage import FilesystemStorage

MATCH_ID = "proxy-stream-test-match"
SLUG = "me"
EMAIL = "proxy-stream@example.com"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _seed_session(db_url: str, email: str = EMAIL) -> str:
    """Insert a user + session row and return the raw session secret."""
    from splitsmith.db import SessionRow, new_ulid

    secret = secrets.token_urlsafe(32)

    async def _insert() -> None:
        factory = sessionmaker(create_engine(db_url))
        async with factory() as s:
            uid = new_ulid()
            s.add(User(id=uid, email=email))
            s.add(
                SessionRow(
                    token_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                    user_id=uid,
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                )
            )
            await s.commit()

    asyncio.run(_insert())
    return secret


def _seed_match_and_project(db_url: str, email: str, match_id: str, slug: str) -> None:
    """Insert a MatchRow and project state doc with raw/clip.mp4 registered.

    The video is placed in unassigned_videos so stream_video can find it
    via find_video() without needing a full scoreboard import.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject, StageVideo

    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == email))).scalar_one()
            user_id = row.id
        async with sf() as s:
            s.add(
                MatchRow(
                    user_id=user_id,
                    match_id=match_id,
                    name="Proxy Stream Test Match",
                    storage_prefix=f"matches/{match_id}",
                )
            )
            await s.commit()
        store = ProjectStateStore(sf, user_id=user_id)
        match_doc = match_model.Match(
            match_id=match_id,
            name="Proxy Stream Test Match",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match_doc.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Proxy Test Shooter",
            unassigned_videos=[StageVideo(path=Path("raw/clip.mp4"), role="primary")],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FilesystemStorage]]:
    """Hosted-mode app with FilesystemStorage wired as the tenant store.

    Monkeypatches _tenant_s3_storage so every request resolves to the
    same FilesystemStorage backed by tmp_path/storage. The session cookie
    is seeded directly so there is no magic-link dance.
    """
    db_path = tmp_path / "proxy_stream.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_engine(db_url)

    async def _create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())

    backing = tmp_path / "storage"
    backing.mkdir()
    storage = FilesystemStorage(backing)

    env_keys = (
        "SPLITSMITH_DATABASE_URL",
        "SPLITSMITH_MODE",
        "SPLITSMITH_PUBLIC_URL",
        "SPLITSMITH_PROJECTS_DIR",
    )
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", db_url)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_PUBLIC_URL", "http://localhost:5174")
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(tmp_path / "projects"))

    from splitsmith.ui import server as server_mod

    def _stub_tenant_storage(client: object, bucket: object, user_id: str) -> FilesystemStorage:
        return storage

    monkeypatch.setattr(server_mod, "_tenant_s3_storage", _stub_tenant_storage)

    app = server_mod.create_app()
    session_secret = _seed_session(db_url)
    _seed_match_and_project(db_url, EMAIL, MATCH_ID, SLUG)

    from splitsmith.db import SESSION_COOKIE_NAME

    with TestClient(app, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE_NAME, session_secret)
        yield client, storage


def _stream_url(slug: str, match_id: str = MATCH_ID) -> str:
    return f"/api/matches/{match_id}/shooters/{slug}/videos/stream"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stream_proxy_serves_proxy_when_present(
    proxy_client: tuple[TestClient, FilesystemStorage],
) -> None:
    """kind=proxy returns proxy bytes when raw_proxy/clip.mp4 is in storage."""
    from splitsmith.proxy import proxy_key_for

    client, storage = proxy_client
    storage.write_bytes("raw/clip.mp4", b"SOURCEBYTES")
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"PROXYBYTES")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "proxy"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"PROXYBYTES"


def test_stream_proxy_falls_back_to_source_when_absent(
    proxy_client: tuple[TestClient, FilesystemStorage],
) -> None:
    """kind=proxy with no proxy object returns 200 with source bytes - not 404."""
    client, storage = proxy_client
    storage.write_bytes("raw/clip.mp4", b"SOURCEBYTES")
    # no proxy object written

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "proxy"})
    assert resp.status_code == 200, resp.text
    assert resp.content == b"SOURCEBYTES"
