"""Tests for direct-R2 media serving (presigned GET redirects).

Covers the behavior contract from docs/superpowers/specs/2026-07-07-direct-r2-media-serving-design.md:

  - S3-backed stream endpoint returns 307 with a presigned URL for kind=source
  - kind=proxy + proxy present -> 307 to the proxy key
  - kind=proxy + proxy absent -> 425 preview_generating (no silent fallback to source)
  - kind=trim + trim absent -> 404
  - kind=trim + trim present -> 307 to the trim key (note below)
  - alias endpoint mirrors the 425 contract
  - serve_media TTL: owner request -> 6 h (21600 s), share request -> 15 min (900 s)
  - serve_media falls back to FileResponse when storage is not presign-capable

Skip cleanly when moto is absent (CI without optional S3 deps).
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from botocore.config import Config as _BotocoreConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402
from sqlalchemy import select as _select  # noqa: E402

from splitsmith.db import Base, MatchRow, ProjectStateStore, User, create_engine, sessionmaker  # noqa: E402
from splitsmith.storage import S3Storage  # noqa: E402

BUCKET = "splitsmith-media-presign-test"
MATCH_ID = "s3-stream-test-match"
SLUG = "me"
EMAIL = "s3-stream@example.com"

# Precomputed: hashlib.blake2s("raw/clip.mp4#1".encode(), digest_size=6).hexdigest()
# This is the video_id for path="raw/clip.mp4", stage_number=1.
_VIDEO_ID = hashlib.blake2s(b"raw/clip.mp4#1", digest_size=6).hexdigest()

# Trim storage key (relative to storage prefix) for stage 1 / raw/clip.mp4.
# Formula: {scope}/trimmed/stage{n}_cam_{video_id}_trimmed.mp4
# where scope = matches/{MATCH_ID}/shooters/{SLUG}.
_TRIM_KEY = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_{_VIDEO_ID}_trimmed.mp4"

# Audit WAV storage key for the same stage/video.
# Formula: {scope}/audio/stage{n}_cam_{video_id}_audit.wav
_AUDIT_WAV_KEY = f"matches/{MATCH_ID}/shooters/{SLUG}/audio/stage1_cam_{_VIDEO_ID}_audit.wav"


def _tiny_wav_bytes() -> bytes:
    """A minimal valid mono WAV (0.1 s of silence) that ensure_peaks can parse."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00" * 4800)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# DB helpers (mirror the pattern in test_stream_proxy.py / test_proxy_ready.py)
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
    """Insert a MatchRow and project state doc with raw/clip.mp4 in stage 1.

    The video is placed in stage 1 (not unassigned_videos) so that
    find_video returns (stage, video) with stage != None - required for
    the trim and source code paths to exercise the hosted redirect branch
    correctly.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

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
                    name="Presign Test Match",
                    storage_prefix=f"matches/{match_id}",
                )
            )
            await s.commit()
        store = ProjectStateStore(sf, user_id=user_id)
        match_doc = match_model.Match(
            match_id=match_id,
            name="Presign Test Match",
            shooters=[slug],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(match_id, match_doc.model_dump(mode="json"), expected_version=0)
        video = StageVideo(path=Path("raw/clip.mp4"), role="primary")
        project = MatchProject(
            name="Presign Test Shooter",
            stages=[StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=0.0, videos=[video])],
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def s3_stream_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, S3Storage]]:
    """S3-backed hosted app with raw/clip.mp4 registered in stage 1.

    Monkeypatches _tenant_s3_storage so every request resolves to an
    S3Storage backed by a moto bucket. Session auth is seeded directly
    to skip the magic-link dance.

    Yields (client, storage) where storage has supports_presigned_get=True.
    """
    db_path = tmp_path / "presign_stream.sqlite"
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
        # Use s3v4 so generate_presigned_url produces X-Amz-Expires (relative TTL)
        # rather than the V2 absolute Expires timestamp. R2 also requires V4 in
        # production, so this matches the real signing path.
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
            # Drive one request to trigger _stub_tenant_storage so captured["storage"]
            # is available before tests start writing objects.
            client.get("/api/me/recent-projects")
            storage = captured["storage"]
            yield client, storage


# ---------------------------------------------------------------------------
# URL helpers (same shape as test_stream_proxy.py)
# ---------------------------------------------------------------------------


def _stream_url(slug: str, match_id: str = MATCH_ID) -> str:
    """Primary stream endpoint, routed via the match-id alias middleware."""
    return f"/api/matches/{match_id}/shooters/{slug}/videos/stream"


def _alias_stream_url(slug: str, match_id: str = MATCH_ID) -> str:
    """stream_shooter_video endpoint, accessed via the alias middleware."""
    return f"/api/matches/{match_id}/match/shooters/{slug}/videos/stream"


# ---------------------------------------------------------------------------
# Tests: presigned redirect (kind=source)
# ---------------------------------------------------------------------------


def test_source_kind_returns_307_with_owner_ttl(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """kind=source returns 307; Location is a presigned URL for the raw key
    with X-Amz-Expires=21600 (6 h owner TTL, not the 15 min share TTL)."""
    client, _storage = s3_stream_client

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "source"})

    assert resp.status_code == 307
    location = resp.headers["location"]
    # The presigned URL encodes the raw key in its path component.
    assert "raw" in location and "clip.mp4" in location
    qs = parse_qs(urlparse(location).query)
    assert qs.get("X-Amz-Expires") == ["21600"], f"expected 6 h TTL; got {qs}"


# ---------------------------------------------------------------------------
# Tests: presigned redirect (kind=proxy)
# ---------------------------------------------------------------------------


def test_proxy_kind_redirects_when_proxy_present(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """kind=proxy + proxy object in storage -> 307 to the raw_proxy key."""
    from splitsmith.proxy import proxy_key_for

    client, storage = s3_stream_client
    proxy_key = proxy_key_for("raw/clip.mp4")  # "raw_proxy/clip.mp4"
    storage.write_bytes(proxy_key, b"PROXYBYTES")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "proxy"})

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "raw_proxy" in location
    assert "clip.mp4" in location


def test_proxy_kind_absent_returns_425_not_source(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """kind=proxy + no proxy object -> 425 preview_generating, never source bytes.

    This is the no-silent-fallback guarantee: hosted mode must not serve the
    full-resolution source when the proxy is still being generated. The SPA
    renders an explicit "preview still processing" state instead.
    """
    client, storage = s3_stream_client
    # Write source but not the proxy - simulates "upload done, proxy not yet ready".
    storage.write_bytes("raw/clip.mp4", b"SOURCEBYTES")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "proxy"})

    assert resp.status_code == 425
    detail = resp.json()["detail"]
    assert detail["code"] == "preview_generating"
    # Not a redirect and not source bytes - confirm both guarantees.
    assert "location" not in {k.lower() for k in resp.headers}


# ---------------------------------------------------------------------------
# Tests: trim (kind=trim)
# ---------------------------------------------------------------------------


def test_trim_kind_absent_returns_404(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """kind=trim + trim not yet built -> 404 (not a redirect, not source)."""
    client, _storage = s3_stream_client
    # No trim object written - the video is registered in stage 1 so
    # find_video returns (stage, video) with stage != None, enabling the
    # trim branch in the server.

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "trim"})

    assert resp.status_code == 404


def test_trim_kind_present_returns_307(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """kind=trim + trim object present -> 307 to the trimmed key.

    The trim storage key formula is:
      {scope}/trimmed/stage{n}_cam_{video_id}_trimmed.mp4
    where scope = matches/{match_id}/shooters/{slug} and video_id is
    computed from hashlib.blake2s("raw/clip.mp4#1", digest_size=6).
    """
    client, storage = s3_stream_client
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")

    resp = client.get(_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "trim"})

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "trimmed" in location
    assert _VIDEO_ID in location


# ---------------------------------------------------------------------------
# Tests: audit /peaks + /audio must not mirror the full source (#592)
# ---------------------------------------------------------------------------


def test_peaks_trimmed_path_does_not_mirror_source(
    s3_stream_client: tuple[TestClient, S3Storage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /peaks request for a stage whose trim + audit WAV are already in
    storage must NOT resolve (and therefore mirror) the full source video.

    Regression for #592: the endpoint used to resolve the source eagerly as
    a call argument, downloading hundreds of MB from R2 before returning a
    tiny peaks payload. The trimmed path only needs the small trim + WAV.
    """
    from splitsmith.ui.project import MatchProject

    client, storage = s3_stream_client
    # Seed the trim + audit WAV, but NOT the source object. If the code
    # reaches for the source at all we catch it via the spy below.
    storage.write_bytes(_TRIM_KEY, b"TRIMDATA")
    storage.write_bytes(_AUDIT_WAV_KEY, _tiny_wav_bytes())

    resolved: list[Path] = []
    original = MatchProject.resolve_video_path

    def spy(self: MatchProject, root: Path, video_path: Path) -> Path:
        resolved.append(video_path)
        return original(self, root, video_path)

    monkeypatch.setattr(MatchProject, "resolve_video_path", spy)

    resp = client.get(f"/api/matches/{MATCH_ID}/shooters/{SLUG}/stages/1/peaks")

    assert resp.status_code == 200
    assert resp.json()["trimmed"] is True
    assert resolved == [], f"source was mirrored on the trimmed path: {resolved}"


# ---------------------------------------------------------------------------
# Tests: alias endpoint
# ---------------------------------------------------------------------------


def test_alias_endpoint_proxy_absent_returns_425(
    s3_stream_client: tuple[TestClient, S3Storage],
) -> None:
    """Alias endpoint /api/match/shooters/{slug}/videos/stream mirrors the
    primary endpoint's 425 contract: proxy absent -> 425 preview_generating."""
    client, storage = s3_stream_client
    storage.write_bytes("raw/clip.mp4", b"SOURCEBYTES")
    # Proxy not written.

    resp = client.get(_alias_stream_url(SLUG), params={"path": "raw/clip.mp4", "kind": "proxy"})

    assert resp.status_code == 425
    assert resp.json()["detail"]["code"] == "preview_generating"


# ---------------------------------------------------------------------------
# Tests: serve_media unit tests (TTL branching + FileResponse fallback)
# ---------------------------------------------------------------------------


class _StubStorage:
    """Minimal stub satisfying the Storage presign interface.

    Records the last expires_in value passed to presign_get_url so tests can
    assert which TTL branch was taken.
    """

    def __init__(self, *, presign_capable: bool = True) -> None:
        self.supports_presigned_get: bool = presign_capable
        self.recorded_expires_in: int | None = None

    def presign_get_url(
        self,
        path: str,
        *,
        expires_in: int,
        content_type: str | None = None,
        disposition: str = "inline",
    ) -> str:
        self.recorded_expires_in = expires_in
        return f"https://stub.example.com/{path}?expires={expires_in}"


def test_serve_media_owner_ttl(tmp_path: Path) -> None:
    """Owner request (current_share_request default False) -> 6 h TTL."""
    from splitsmith.ui.server import _OWNER_MEDIA_TTL, serve_media

    stub = _StubStorage(presign_capable=True)
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"x")

    # Default: current_share_request is False.
    result = serve_media(stub, "some/key", local_file, content_type="video/mp4")

    from starlette.responses import RedirectResponse

    assert isinstance(result, RedirectResponse)
    assert stub.recorded_expires_in == _OWNER_MEDIA_TTL
    assert stub.recorded_expires_in == 6 * 3600


def test_serve_media_share_ttl(tmp_path: Path) -> None:
    """Share request (current_share_request=True) -> 15 min TTL."""
    from splitsmith.ui.server import _SHARE_MEDIA_TTL, current_share_request, serve_media

    stub = _StubStorage(presign_capable=True)
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"x")

    token = current_share_request.set(True)
    try:
        result = serve_media(stub, "some/key", local_file, content_type="video/mp4")
    finally:
        current_share_request.reset(token)

    from starlette.responses import RedirectResponse

    assert isinstance(result, RedirectResponse)
    assert stub.recorded_expires_in == _SHARE_MEDIA_TTL
    assert stub.recorded_expires_in == 15 * 60


def test_serve_media_no_storage_returns_file_response(tmp_path: Path) -> None:
    """storage=None -> FileResponse (local mode path)."""
    from starlette.responses import FileResponse

    from splitsmith.ui.server import serve_media

    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"local bytes")

    result = serve_media(None, None, local_file, content_type="video/mp4")

    assert isinstance(result, FileResponse)


def test_serve_media_non_presign_storage_returns_file_response(tmp_path: Path) -> None:
    """storage with supports_presigned_get=False -> FileResponse (FilesystemStorage path)."""
    from starlette.responses import FileResponse

    from splitsmith.ui.server import serve_media

    stub = _StubStorage(presign_capable=False)
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"local bytes")

    result = serve_media(stub, "some/key", local_file, content_type="video/mp4")

    assert isinstance(result, FileResponse)
