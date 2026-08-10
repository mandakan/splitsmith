"""Tests for the Compare stream fallback (#700 task 2).

``GET /api/match/shooters/{slug}/videos/stream`` serves a registered
video via ``find_video`` (untouched by this change) or, for the
Compare view's own trims, falls back to a logical ref of the shape
``(exports|trimmed)/<name>.mp4``. This file covers that fallback:

  - rejection matrix: absolute path (even when a real file exists at
    that path), ``..`` traversal, non-.mp4 suffix, well-formed-but-
    absent ref - all 404, nothing served
  - local happy path: a real trim file under ``trimmed/`` streams back
  - hosted: trim key present in object storage -> 307 presigned
    redirect; absent -> 404

Local tests use a pre-bound project (``create_app(project_root=...)``,
matching the pattern in test_ui_server.py). Hosted tests use moto S3,
matching the pattern in test_media_presign_serving.py.
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
from tests.conftest import bound_match_id, scaffold_match

# ---------------------------------------------------------------------------
# Local-mode fixture (pre-bound project, no hosted storage)
# ---------------------------------------------------------------------------


@pytest.fixture
def local_client(tmp_path: Path) -> tuple[TestClient, Path, Path, str]:
    """Local app with one shooter ``me`` and no videos assigned, addressed
    through the ``/api/matches/{match_id}/`` alias like every other
    match-level route (bare paths 409 ``no_project`` since doc 10 Tier 1).

    Returns ``(client, match_root, shooter_root, match_id)``.
    """
    from splitsmith.ui.server import create_app

    match_root, shooter_root = scaffold_match(tmp_path, name="Compare Stream Test")
    app = create_app(project_root=match_root, project_name="Compare Stream Test")
    # No ``with`` context manager: local-mode tests elsewhere (e.g.
    # test_ui_server.py's ``_MatchClient``) skip lifespan startup, which
    # would otherwise probe a procrastinate_jobs table this sqlite fixture
    # never creates.
    client = TestClient(app)
    match_id = bound_match_id(app)
    yield client, match_root, shooter_root, match_id


def _local_url(match_id: str, slug: str = "me") -> str:
    return f"/api/matches/{match_id}/match/shooters/{slug}/videos/stream"


# ---------------------------------------------------------------------------
# Rejection matrix (local)
# ---------------------------------------------------------------------------


def test_absolute_path_rejected_even_when_file_exists(
    local_client: tuple[TestClient, Path, Path, str],
) -> None:
    """An absolute path is rejected outright - not merely because the
    file is missing. Prove rejection by creating a real file at that
    absolute path inside exports_dir and asserting 404 anyway."""
    client, _match_root, shooter_root, match_id = local_client
    exports_dir = shooter_root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    real_file = exports_dir / "real.mp4"
    real_file.write_bytes(b"REAL_BYTES")

    resp = client.get(_local_url(match_id), params={"path": str(real_file)})

    assert resp.status_code == 404


def test_traversal_path_rejected(local_client: tuple[TestClient, Path, Path, str]) -> None:
    """``trimmed/../secrets.mp4`` is rejected - the ref grammar has no
    slash inside the filename component, so this never reaches a dir
    derivation at all."""
    client, _match_root, shooter_root, match_id = local_client
    secret = shooter_root / "secrets.mp4"
    secret.write_bytes(b"SECRET_BYTES")

    resp = client.get(_local_url(match_id), params={"path": "trimmed/../secrets.mp4"})

    assert resp.status_code == 404


def test_non_mp4_suffix_rejected(local_client: tuple[TestClient, Path, Path, str]) -> None:
    """A well-formed dir prefix with a non-.mp4 suffix is rejected."""
    client, _match_root, shooter_root, match_id = local_client
    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / "clip.mov").write_bytes(b"MOV_BYTES")

    resp = client.get(_local_url(match_id), params={"path": "trimmed/clip.mov"})

    assert resp.status_code == 404


def test_well_formed_absent_ref_returns_404(
    local_client: tuple[TestClient, Path, Path, str],
) -> None:
    """A ref matching the grammar but with no backing file is 404."""
    client, _match_root, _shooter_root, match_id = local_client

    resp = client.get(_local_url(match_id), params={"path": "exports/nope.mp4"})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Local happy path
# ---------------------------------------------------------------------------


def test_local_happy_path_serves_trim_bytes(
    local_client: tuple[TestClient, Path, Path, str],
) -> None:
    client, _match_root, shooter_root, match_id = local_client
    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    trim = trimmed_dir / "stage1_cam_abc123_trimmed.mp4"
    trim.write_bytes(b"TRIMMED_MP4_BYTES")

    resp = client.get(
        _local_url(match_id), params={"path": "trimmed/stage1_cam_abc123_trimmed.mp4", "kind": "auto"}
    )

    assert resp.status_code == 200
    assert resp.content == b"TRIMMED_MP4_BYTES"


# ---------------------------------------------------------------------------
# Hosted fixture (moto S3)
# ---------------------------------------------------------------------------

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from botocore.config import Config as _BotocoreConfig  # noqa: E402
from moto import mock_aws  # noqa: E402

from splitsmith.storage import S3Storage  # noqa: E402

BUCKET = "splitsmith-compare-stream-ref-test"
MATCH_ID = "compare-stream-ref-test-match"
SLUG = "me"
EMAIL = "compare-stream-ref@example.com"

# {scope}/trimmed/<name> where scope = matches/{MATCH_ID}/shooters/{SLUG}
_TRIM_KEY = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123_trimmed.mp4"


def _seed_session(db_url: str, email: str = EMAIL) -> str:
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
    """Insert a MatchRow + an empty project doc - no video registered,
    since the fallback only cares about the shooter existing."""
    from splitsmith import match_model
    from splitsmith.match_project import MatchProject

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
                    name="Compare Stream Ref Test Match",
                    storage_prefix=f"matches/{match_id}",
                )
            )
            await s.commit()
        store = ProjectStateStore(sf, user_id=user_id)
        match_doc = match_model.Match(
            match_id=match_id,
            name="Compare Stream Ref Test Match",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match_doc.model_dump(mode="json"), expected_version=0)
        project = MatchProject(name="Compare Stream Ref Test Shooter")
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


@pytest.fixture
def hosted_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, S3Storage]]:
    db_path = tmp_path / "compare_stream_ref.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url)

    async def _create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())

    env_keys = (
        "SPLITSMITH_DATABASE_URL",
        "SPLITSMITH_MODE",
        "SPLITSMITH_PUBLIC_URL",
        "SPLITSMITH_PROJECTS_DIR",
        "SPLITSMITH_S3_BUCKET",
        "SPLITSMITH_S3_ENDPOINT_URL",
        "SPLITSMITH_S3_REGION",
        "SPLITSMITH_S3_ACCESS_KEY_ID",
        "SPLITSMITH_S3_SECRET_ACCESS_KEY",
    )
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", db_url)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_PUBLIC_URL", "http://localhost:5174")
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", BUCKET)
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "us-east-1")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "secret")

    with mock_aws():
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            config=_BotocoreConfig(signature_version="s3v4"),
        )
        s3.create_bucket(Bucket=BUCKET)

        from splitsmith.ui import server as server_mod

        captured: dict[str, S3Storage] = {}

        def _stub_tenant_storage(client: object, bucket: object, user_id: str) -> S3Storage:
            storage = S3Storage(bucket=BUCKET, prefix=f"users/{user_id}/", client=s3)
            captured["storage"] = storage
            return storage

        monkeypatch.setattr(server_mod, "_tenant_s3_storage", _stub_tenant_storage)

        app = server_mod.create_app()
        session_secret = _seed_session(db_url)
        _seed_match_and_project(db_url, EMAIL, MATCH_ID, SLUG)

        from splitsmith.db import SESSION_COOKIE_NAME

        with TestClient(app, follow_redirects=False) as client:
            client.cookies.set(SESSION_COOKIE_NAME, session_secret)
            client.get("/api/me/recent-projects")
            storage = captured["storage"]
            yield client, storage


def _hosted_url(slug: str = SLUG, match_id: str = MATCH_ID) -> str:
    return f"/api/matches/{match_id}/match/shooters/{slug}/videos/stream"


def test_hosted_trim_present_returns_307(hosted_client: tuple[TestClient, S3Storage]) -> None:
    client, storage = hosted_client
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")

    resp = client.get(_hosted_url(), params={"path": "trimmed/stage1_cam_abc123_trimmed.mp4", "kind": "auto"})

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "trimmed" in location
    assert "stage1_cam_abc123_trimmed.mp4" in location


def test_hosted_trim_absent_returns_404(hosted_client: tuple[TestClient, S3Storage]) -> None:
    client, _storage = hosted_client

    resp = client.get(_hosted_url(), params={"path": "trimmed/stage1_cam_abc123_trimmed.mp4", "kind": "auto"})

    assert resp.status_code == 404
