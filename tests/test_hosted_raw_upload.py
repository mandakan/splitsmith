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

BUCKET = "splitsmith-uploads-test"


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

    prior_url = os.environ.get("SPLITSMITH_DATABASE_URL")
    prior_mode = os.environ.get("SPLITSMITH_MODE")
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    try:
        yield url
    finally:
        if prior_url is None:
            os.environ.pop("SPLITSMITH_DATABASE_URL", None)
        else:
            os.environ["SPLITSMITH_DATABASE_URL"] = prior_url
        if prior_mode is None:
            os.environ.pop("SPLITSMITH_MODE", None)
        else:
            os.environ["SPLITSMITH_MODE"] = prior_mode


@pytest.fixture
def hosted_client(hosted_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, S3Storage]]:
    """Boot the FastAPI app in hosted mode against a moto bucket.

    Replaces ``_build_hosted_storage`` so the S3Storage instance is
    constructed with a client bound to the mock S3 backend that's
    already created the bucket. This avoids the chicken-and-egg of
    "the wiring needs a bucket to exist before boto3 will GET it".
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

        def _stub_build_storage(user_id: str) -> S3Storage:
            return S3Storage(bucket=BUCKET, prefix=f"users/{user_id}/", client=s3)

        monkeypatch.setattr(server_mod, "_build_hosted_storage", _stub_build_storage)

        app = server_mod.create_app()
        with TestClient(app) as client:
            yield client, app.state.splitsmith_state.storage


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
