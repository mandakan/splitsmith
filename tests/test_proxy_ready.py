"""Tests for proxy_ready flag in the project API payload and proxy cleanup on delete.

Three cases:
  (a) proxy object present -> proxy_ready True in the project payload
  (b) proxy object absent -> proxy_ready False
  (c) deleting a raw video also removes its raw_proxy/... object from storage
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

MATCH_ID = "proxy-ready-test-match"
SLUG = "me"
EMAIL = "proxy-ready@example.com"


# ---------------------------------------------------------------------------
# DB / project helpers
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
    """Insert a MatchRow and a project doc with raw/clip.mp4 in unassigned_videos."""
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
                    name="Proxy Ready Test Match",
                    storage_prefix=f"matches/{match_id}",
                )
            )
            await s.commit()
        store = ProjectStateStore(sf, user_id=user_id)
        match_doc = match_model.Match(
            match_id=match_id,
            name="Proxy Ready Test Match",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match_doc.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Proxy Ready Shooter",
            unassigned_videos=[StageVideo(path=Path("raw/clip.mp4"), role="primary")],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_ready_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FilesystemStorage, str, str]]:
    """Hosted-mode app with FilesystemStorage wired as the tenant store.

    Monkeypatches _tenant_s3_storage so every request resolves to the same
    FilesystemStorage backed by tmp_path/storage. Session auth is seeded
    directly to skip the magic-link dance.

    Yields (client, storage, match_id, slug).
    """
    db_path = tmp_path / "proxy_ready.sqlite"
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
        yield client, storage, MATCH_ID, SLUG


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_project(client: TestClient, match_id: str, slug: str) -> dict:
    resp = client.get(f"/api/matches/{match_id}/shooters/{slug}/project")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _all_videos(project: dict) -> list[dict]:
    """Collect all video dicts from stages and unassigned_videos."""
    vids: list[dict] = list(project.get("unassigned_videos", []))
    for stage in project.get("stages", []):
        vids.extend(stage.get("videos", []))
    return vids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_proxy_ready_true_when_proxy_exists(proxy_ready_client) -> None:
    """Video gets proxy_ready=True when its raw_proxy/... object exists in storage."""
    from splitsmith.proxy import proxy_key_for

    client, storage, match_id, slug = proxy_ready_client
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"x")

    proj = _get_project(client, match_id, slug)
    vids = _all_videos(proj)
    v = next(v for v in vids if v["path"] == "raw/clip.mp4")
    assert v["proxy_ready"] is True


def test_proxy_ready_false_when_absent(proxy_ready_client) -> None:
    """Video gets proxy_ready=False when no proxy object exists in storage."""
    client, _storage, match_id, slug = proxy_ready_client
    # No proxy written - raw file present without a generated proxy.

    proj = _get_project(client, match_id, slug)
    vids = _all_videos(proj)
    v = next(v for v in vids if v["path"] == "raw/clip.mp4")
    assert v["proxy_ready"] is False


def test_delete_raw_also_deletes_proxy(proxy_ready_client) -> None:
    """DELETE /api/me/raw/<name> removes the proxy object alongside the raw file."""
    from splitsmith.proxy import proxy_key_for

    client, storage, match_id, slug = proxy_ready_client
    storage.write_bytes("raw/clip.mp4", b"x")
    storage.write_bytes(proxy_key_for("raw/clip.mp4"), b"y")

    resp = client.delete("/api/me/raw/clip.mp4")
    assert resp.status_code == 200, resp.text

    assert not storage.exists(proxy_key_for("raw/clip.mp4"))
