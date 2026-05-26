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
