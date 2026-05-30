"""End-to-end tests for ``POST /api/me/raw/upload``.

Hosted-only endpoint that streams an UploadFile into ``state.storage``
under ``raw/<name>``. Local mode never wires storage, so the route
must return 503 there.

These tests drive the FastAPI app directly via :class:`TestClient`
against a ``moto`` S3 mock so we exercise the same boto3 codepath
production hits. They prove the Path B robustness contract:

- atomic / idempotent overwrites (re-upload same path is safe),
- server-computed sha256 returned to the client,
- ``X-Content-SHA256`` mismatch rolls the object back + 422s,
- filename sanitization rejects path traversal at the route layer
  before the storage guard sees it.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

moto = pytest.importorskip("moto")
import boto3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402

from splitsmith.db import Base, create_engine  # noqa: E402
from splitsmith.storage import S3Storage  # noqa: E402
from splitsmith.ui.project import MatchProject  # noqa: E402

BUCKET = "splitsmith-uploads-test"


def _seed_session(url: str, *, email: str = "raw-tester@example.com") -> str:
    """Insert a user + session row directly and return the raw session
    secret, so a hosted TestClient can authenticate by carrying it in the
    ``splitsmith_session`` cookie -- ``MagicLinkAuth`` now 401s anonymous
    requests, and this avoids driving the full e-mail dance in every test."""
    import hashlib
    import secrets
    from datetime import UTC, datetime, timedelta

    from splitsmith.db import SessionRow, new_ulid, sessionmaker
    from splitsmith.db import User as UserRow

    secret = secrets.token_urlsafe(32)

    async def _insert() -> None:
        factory = sessionmaker(create_engine(url))
        async with factory() as s:
            uid = new_ulid()
            s.add(UserRow(id=uid, email=email))
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


def _authed(client: TestClient, url: str) -> TestClient:
    """Attach a freshly-seeded session cookie to ``client`` so its requests
    resolve to a real user under MagicLinkAuth."""
    from splitsmith.db import SESSION_COOKIE_NAME

    client.cookies.set(SESSION_COOKIE_NAME, _seed_session(url))
    return client


@pytest.fixture
def hosted_db(tmp_path: Path) -> Iterator[str]:
    """SQLite-backed hosted DB so ``create_app`` doesn't need Postgres.

    Mirrors the fixture in ``test_hosted_mode_boot.py``. File-backed
    so workers on a thread pool see the same schema.
    """
    db_path = tmp_path / "raw_upload.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    prior = {
        k: os.environ.get(k) for k in ("SPLITSMITH_DATABASE_URL", "SPLITSMITH_MODE", "SPLITSMITH_PUBLIC_URL")
    }
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    os.environ["SPLITSMITH_PUBLIC_URL"] = "http://localhost:5174"
    try:
        yield url
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def hosted_client(hosted_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, S3Storage]]:
    """Boot the FastAPI app in hosted mode against a moto bucket.

    Replaces ``_tenant_s3_storage`` (the per-request wrapper) so each
    tenant ``S3Storage`` is built with a client bound to the mock S3
    backend that already created the bucket -- avoiding the chicken-and-
    egg of "the wiring needs a bucket to exist before boto3 will GET it".
    Storage is now resolved per request via ``current_tenant`` rather than
    held on ``AppState``, so the stub captures the constructed instance for
    the test to assert against (every request rebuilds an equivalent one
    against the same bucket/prefix/client).
    """
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", BUCKET)
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://moto")
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "us-east-1")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "secret")

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        from splitsmith.ui import server as server_mod

        captured: dict[str, S3Storage] = {}

        def _stub_tenant_storage(client: object, bucket: object, user_id: str) -> S3Storage:
            storage = S3Storage(bucket=BUCKET, prefix=f"users/{user_id}/", client=s3)
            captured["storage"] = storage
            return storage

        monkeypatch.setattr(server_mod, "_tenant_s3_storage", _stub_tenant_storage)

        app = server_mod.create_app()
        with TestClient(app) as client:
            _authed(client, hosted_db)
            # Drive one request so the per-request tenant builds a storage
            # the test can read back; ``/api/me/recent-projects`` resolves
            # the tenant without needing a project. Any captured instance is
            # equivalent.
            client.get("/api/me/recent-projects")
            yield client, captured["storage"]


def test_upload_returns_path_size_sha256(hosted_client) -> None:
    client, storage = hosted_client
    payload = b"hello raw video " * 1000  # 16 KB
    digest = hashlib.sha256(payload).hexdigest()

    resp = client.post(
        "/api/me/raw/upload",
        files={"file": ("GH010023.mp4", io.BytesIO(payload), "video/mp4")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "path": "raw/GH010023.mp4",
        "size": len(payload),
        "sha256": digest,
        "filename": "GH010023.mp4",
    }
    # The bytes landed under the per-user tenant prefix.
    assert storage.read_bytes("raw/GH010023.mp4") == payload


def test_upload_idempotent_overwrite(hosted_client) -> None:
    """A retry with the same filename must overwrite cleanly. This is
    the v1 robustness story (resume-from-byte-N is doc 05's tus
    follow-up; safe re-upload is what we ship today)."""
    client, storage = hosted_client
    first = b"first attempt; client dropped at 90%"
    second = b"second attempt; complete payload" * 4

    client.post(
        "/api/me/raw/upload",
        files={"file": ("clip.mp4", io.BytesIO(first), "video/mp4")},
    )
    resp = client.post(
        "/api/me/raw/upload",
        files={"file": ("clip.mp4", io.BytesIO(second), "video/mp4")},
    )

    assert resp.status_code == 200
    assert storage.read_bytes("raw/clip.mp4") == second


def test_upload_sha256_match_passes(hosted_client) -> None:
    client, _ = hosted_client
    payload = b"\x00\x01\x02" * 4096
    digest = hashlib.sha256(payload).hexdigest()

    resp = client.post(
        "/api/me/raw/upload",
        files={"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")},
        headers={"X-Content-SHA256": digest},
    )

    assert resp.status_code == 200


def test_upload_sha256_mismatch_rolls_back(hosted_client) -> None:
    """A declared sha256 that doesn't match the streamed bytes must
    422 *and* delete the just-written object so a later GET can't
    serve corrupted content."""
    client, storage = hosted_client
    payload = b"real bytes"
    bogus = "0" * 64

    resp = client.post(
        "/api/me/raw/upload",
        files={"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")},
        headers={"X-Content-SHA256": bogus},
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "sha256_mismatch"
    assert detail["expected"] == bogus
    # Rollback: nothing visible at the key.
    assert not storage.exists("raw/clip.mp4")


@pytest.mark.parametrize(
    "name",
    [
        "../escape.mp4",
        "/etc/passwd",
        "..\\windows\\hosts",
        ".",
        "..",
    ],
)
def test_upload_rejects_unsafe_filenames(hosted_client, name: str) -> None:
    client, _ = hosted_client

    resp = client.post(
        "/api/me/raw/upload",
        files={"file": (name, io.BytesIO(b"x"), "video/mp4")},
    )

    assert resp.status_code == 400


def test_upload_503_when_storage_unwired(
    hosted_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``SPLITSMITH_S3_BUCKET`` the boot leaves
    ``state.storage`` as None -- the endpoint must refuse cleanly."""
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)

    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        _authed(client, hosted_db)
        resp = client.post(
            "/api/me/raw/upload",
            files={"file": ("clip.mp4", io.BytesIO(b"x"), "video/mp4")},
        )

    assert resp.status_code == 503
    assert "hosted-mode only" in resp.json()["detail"]


def test_local_mode_endpoint_still_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity-check the desktop case: with ``SPLITSMITH_MODE`` unset
    the route still exists but returns 503, proving local-mode
    behaviour is untouched (the desktop UI doesn't call this route at
    all -- raw videos live behind ``raw/<name>`` symlinks)."""
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)

    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/me/raw/upload",
            files={"file": ("clip.mp4", io.BytesIO(b"x"), "video/mp4")},
        )

    assert resp.status_code == 503


# --- GET /api/me/raw/list / DELETE /api/me/raw/{filename} ---------------
#
# The list + delete endpoints round out the v1 upload surface so the
# SPA can drive an "uploaded files" panel against object storage
# without inventing its own state (an upload that never lands is
# invisible; a successful upload is observable; a mistake is
# prunable). The actual project-attach is a follow-up; today the SPA
# uses the list endpoint as the "what have I uploaded?" view.


def test_list_returns_uploaded_files_newest_first(hosted_client) -> None:
    """The list endpoint surfaces every object under the user's
    ``raw/`` prefix with the metadata the SPA needs to render an
    uploaded-files row."""
    client, _ = hosted_client

    # Upload two files; the second is the newer one so it should
    # sort first.
    client.post(
        "/api/me/raw/upload",
        files={"file": ("first.mp4", io.BytesIO(b"first " * 100), "video/mp4")},
    )
    client.post(
        "/api/me/raw/upload",
        files={"file": ("second.mp4", io.BytesIO(b"second " * 200), "video/mp4")},
    )

    resp = client.get("/api/me/raw/list")
    assert resp.status_code == 200, resp.text
    uploads = resp.json()["uploads"]
    assert [u["filename"] for u in uploads] == ["second.mp4", "first.mp4"]
    # Each row carries the fields the SPA needs.
    assert uploads[0]["path"] == "raw/second.mp4"
    assert uploads[0]["size"] == len(b"second " * 200)
    assert uploads[0]["last_modified"] is not None
    assert uploads[0]["etag"] is not None


def test_list_empty_when_no_uploads(hosted_client) -> None:
    """Fresh tenant -- empty list, not 404. The SPA's empty state
    keys off ``uploads.length === 0`` so this needs to round-trip
    cleanly."""
    client, _ = hosted_client
    resp = client.get("/api/me/raw/list")
    assert resp.status_code == 200
    assert resp.json() == {"uploads": []}


def test_list_503_when_storage_unwired(hosted_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same hosted-mode-only contract as the upload endpoint -- no
    storage backend, no list. Local users keep videos on disk and
    don't call this route."""
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)

    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        _authed(client, hosted_db)
        resp = client.get("/api/me/raw/list")
    assert resp.status_code == 503


def test_delete_removes_object(hosted_client) -> None:
    """Round-trip: upload, list, delete, list again -- the object
    disappears from list and the underlying storage."""
    client, storage = hosted_client

    client.post(
        "/api/me/raw/upload",
        files={"file": ("doomed.mp4", io.BytesIO(b"bytes"), "video/mp4")},
    )
    assert storage.exists("raw/doomed.mp4")

    resp = client.delete("/api/me/raw/doomed.mp4")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "path": "raw/doomed.mp4"}
    assert not storage.exists("raw/doomed.mp4")

    listing = client.get("/api/me/raw/list").json()
    assert listing == {"uploads": []}


def test_delete_idempotent(hosted_client) -> None:
    """Deleting an already-gone object is a 200 no-op, so the SPA
    can retry without special-casing 404. (R2's lifecycle rule is the
    backstop; we don't want the SPA falsely reporting an error when
    the actual end state -- "object is gone" -- matches the intent.)"""
    client, _ = hosted_client
    resp = client.delete("/api/me/raw/never-existed.mp4")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_delete_503_when_storage_unwired(hosted_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)

    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        _authed(client, hosted_db)
        resp = client.delete("/api/me/raw/clip.mp4")
    assert resp.status_code == 503


def test_delete_rejects_path_traversal(hosted_client) -> None:
    """The route applies the same ``_sanitize_raw_filename`` guard the
    upload route uses, so a malicious caller can't escape the
    ``raw/`` prefix via ``..``. The storage backend's own
    ``_validate_relative_key`` is a second line of defence."""
    client, _ = hosted_client
    resp = client.delete("/api/me/raw/..%2Fevil.mp4")
    # _sanitize_raw_filename raises a 400; both that and the storage
    # guard fail closed.
    assert resp.status_code in (400, 404)


# --- POST /api/shooters/{slug}/raw-videos/attach -----------------------
#
# The attach endpoint is the bridge from "file lives in S3" to
# "project knows the file exists". It populates ``raw_videos[]`` on
# match.json per doc 05 and optionally creates StageVideo entries on
# the stages the recording covers, so the next step (PR 4) can run
# detection against an S3-backed source.


@pytest.fixture
def hosted_client_with_match(hosted_client, tmp_path: Path) -> Iterator[tuple[TestClient, S3Storage, str]]:
    """Extend ``hosted_client`` with a scaffolded Match + one shooter
    so attach-endpoint tests have something to attach raw videos to.

    Yields ``(client, storage, match_id)``. The match has two stages
    so tests can exercise the multi-stage ``covers_stages`` path.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import StageEntry

    client, storage = hosted_client

    match_root = tmp_path / "matches" / "attach-test"
    match = match_model.Match.init(match_root, name="Attach Test")
    match.stages = [
        match_model.MatchStageDefinition(stage_number=1, stage_name="One"),
        match_model.MatchStageDefinition(stage_number=2, stage_name="Two"),
    ]
    match.save(match_root)
    match.add_shooter(match_root, match_model.Shooter(slug="me", name="Me"))
    sroot = match_model.Match.shooter_root(match_root, "me")
    project = MatchProject.init(sroot, name="Attach Test")
    project.stages = [
        StageEntry(stage_number=1, stage_name="One", time_seconds=10.0),
        StageEntry(stage_number=2, stage_name="Two", time_seconds=15.0),
    ]
    project.save(sroot)

    resp = client.post(
        "/api/me/recent-projects/bind",
        json={"path": str(match_root.resolve())},
    )
    assert resp.status_code == 200, resp.text

    ids = client.app.state.splitsmith_state.matches.known_ids()
    assert len(ids) == 1
    yield client, storage, ids[0]


def _attach_url(match_id: str, slug: str = "me") -> str:
    # The alias middleware rewrites ``/api/matches/{id}/<rest>`` to
    # ``/api/<rest>`` -- so the prefix here is ``shooters/...``, not
    # ``api/shooters/...``. Matches the _MatchClient rewrite pattern.
    return f"/api/matches/{match_id}/shooters/{slug}/raw-videos/attach"


def _seed_upload(client: TestClient, filename: str, payload: bytes) -> dict:
    """Push a file through the upload endpoint so the attach test
    can reference a real key. Returns the JSON the SPA would echo
    back into the attach request body."""
    resp = client.post(
        "/api/me/raw/upload",
        files={"file": (filename, io.BytesIO(payload), "video/mp4")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_attach_registers_raw_video_on_project(hosted_client_with_match) -> None:
    """An upload + attach round-trip lands a RawVideo entry on the
    project's ``raw_videos[]`` with the storage_path the SPA can
    reference back to."""
    from splitsmith import match_model

    client, _, match_id = hosted_client_with_match
    upload = _seed_upload(client, "GH010023.mp4", b"video bytes " * 1024)

    resp = client.post(
        _attach_url(match_id),
        json={
            "filename": upload["filename"],
            "sha256": upload["sha256"],
            "size_bytes": upload["size"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["original_filename"] == "GH010023.mp4"
    assert body["storage_path"] == "raw/GH010023.mp4"
    assert body["sha256"] == upload["sha256"]
    assert body["size_bytes"] == upload["size"]
    assert body["covers_stages"] == []

    # Persisted to the project on disk.
    match_root = Path(client.app.state.splitsmith_state.matches.resolve(match_id))
    sroot = match_model.Match.shooter_root(match_root, "me")
    project = MatchProject.load(sroot)
    assert len(project.raw_videos) == 1
    assert project.raw_videos[0].storage_path == "raw/GH010023.mp4"
    # Without covers_stages, the file lands in unassigned_videos so the
    # ingest tray UI surfaces it -- same shape as local-mode scan.
    assert len(project.unassigned_videos) == 1
    assert str(project.unassigned_videos[0].path) == "raw/GH010023.mp4"


def test_attach_with_covers_stages_creates_stagevideos(hosted_client_with_match) -> None:
    """``covers_stages`` pre-declares the per-stage references so the
    worker has something to detect against without a separate
    auto-match call. The first stage gets primary; subsequent get
    secondary (since they already have a primary on the shared raw)."""
    from splitsmith import match_model

    client, _, match_id = hosted_client_with_match
    upload = _seed_upload(client, "headcam.mp4", b"shared " * 4096)

    resp = client.post(
        _attach_url(match_id),
        json={
            "filename": upload["filename"],
            "sha256": upload["sha256"],
            "size_bytes": upload["size"],
            "covers_stages": [1, 2],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["covers_stages"] == [1, 2]

    match_root = Path(client.app.state.splitsmith_state.matches.resolve(match_id))
    sroot = match_model.Match.shooter_root(match_root, "me")
    project = MatchProject.load(sroot)
    stage_one = project.stage(1)
    stage_two = project.stage(2)
    assert len(stage_one.videos) == 1
    assert len(stage_two.videos) == 1
    assert str(stage_one.videos[0].path) == "raw/headcam.mp4"
    assert str(stage_two.videos[0].path) == "raw/headcam.mp4"
    assert stage_one.videos[0].role == "primary"
    assert stage_two.videos[0].role == "primary"


def test_attach_is_idempotent_merges_covers_stages(hosted_client_with_match) -> None:
    """Repeat attaches with the same storage_path merge covers_stages
    rather than appending duplicate raw_videos entries (the same
    contract ``MatchProject.attach_raw_video`` enforces in unit
    tests)."""
    from splitsmith import match_model

    client, _, match_id = hosted_client_with_match
    upload = _seed_upload(client, "shared.mp4", b"x" * 1024)

    client.post(
        _attach_url(match_id),
        json={
            "filename": upload["filename"],
            "sha256": upload["sha256"],
            "covers_stages": [1],
        },
    )
    resp = client.post(
        _attach_url(match_id),
        json={
            "filename": upload["filename"],
            "sha256": upload["sha256"],
            "covers_stages": [2],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["covers_stages"] == [1, 2]

    match_root = Path(client.app.state.splitsmith_state.matches.resolve(match_id))
    sroot = match_model.Match.shooter_root(match_root, "me")
    project = MatchProject.load(sroot)
    assert len(project.raw_videos) == 1
    # Stage 2 was added by the second attach without disturbing
    # stage 1's existing StageVideo.
    assert len(project.stage(1).videos) == 1
    assert len(project.stage(2).videos) == 1


def test_attach_404_when_upload_missing(hosted_client_with_match) -> None:
    """Attach must refuse to register a key the storage backend
    doesn't actually have -- otherwise the manifest would point at
    a non-existent object and the worker would 500 on first
    detection."""
    client, _, match_id = hosted_client_with_match
    resp = client.post(
        _attach_url(match_id),
        json={"filename": "never-uploaded.mp4"},
    )
    assert resp.status_code == 404
    assert "no upload" in resp.json()["detail"]


def test_attach_422_when_covers_stages_unknown(hosted_client_with_match) -> None:
    """An unknown stage_number in ``covers_stages`` is a client bug --
    fail loudly rather than silently dropping the bogus number and
    leaving the manifest claiming coverage that doesn't exist."""
    client, _, match_id = hosted_client_with_match
    upload = _seed_upload(client, "clip.mp4", b"x" * 8)

    resp = client.post(
        _attach_url(match_id),
        json={
            "filename": upload["filename"],
            "covers_stages": [1, 99],
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_stage_numbers"
    assert detail["stage_numbers"] == [99]


def test_attach_400_on_unsafe_filename(hosted_client_with_match) -> None:
    """``_sanitize_raw_filename`` runs at the route layer so a
    traversal attempt 400s before the storage backend's own guard
    sees it."""
    client, _, match_id = hosted_client_with_match
    resp = client.post(
        _attach_url(match_id),
        json={"filename": "../escape.mp4"},
    )
    assert resp.status_code == 400


def test_attach_503_when_storage_unwired(
    hosted_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same hosted-only contract as the upload endpoint -- a desktop
    install with no storage backend refuses cleanly."""
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)

    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app) as client:
        _authed(client, hosted_db)
        resp = client.post(
            "/api/matches/anything/shooters/me/raw-videos/attach",
            json={"filename": "clip.mp4"},
        )
    assert resp.status_code in (503, 404)
    # 503 is the storage-off contract; 404 is the alias middleware
    # rejecting an unknown match_id. Either is "fails closed".
