"""Shared fixtures for tests that need a hosted-mode app + auth dance.

Extracted from tests/test_auth_routes.py so test_share_routes.py (and any
future test file) can reuse them without duplication.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import Base, MatchRow, User, create_engine, sessionmaker

PUBLIC_URL = "http://localhost:5174"


class _CapturingSender:
    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []

    async def send_magic_link(self, *, to: str, link: str) -> None:
        self.links.append((to, link))

    def last_token(self) -> str:
        return parse_qs(urlparse(self.links[-1][1]).query)["token"][0]


@pytest.fixture
def hosted_env(tmp_path: Path) -> Iterator[str]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth_routes.sqlite'}"
    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    _env_keys = (
        "SPLITSMITH_DATABASE_URL",
        "SPLITSMITH_MODE",
        "SPLITSMITH_PUBLIC_URL",
        "SPLITSMITH_PROJECTS_DIR",
    )
    prior = {k: os.environ.get(k) for k in _env_keys}
    os.environ["SPLITSMITH_DATABASE_URL"] = url
    os.environ["SPLITSMITH_MODE"] = "hosted"
    os.environ["SPLITSMITH_PUBLIC_URL"] = PUBLIC_URL
    os.environ["SPLITSMITH_PROJECTS_DIR"] = str(tmp_path / "projects")
    try:
        yield url
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def hosted_app(hosted_env: str) -> Iterator[tuple[TestClient, _CapturingSender]]:
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    # Swap the console transport for the capturing double so the test can
    # read the emitted token. auth is a CompositeAuth; the magic-link
    # backend is backends[0] (session cookie tried first).
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender


def login(client: TestClient, sender: _CapturingSender, email: str) -> None:
    """Complete the magic-link login dance for email (begin + callback)."""
    begin = client.post("/api/v1/auth/begin", json={"email": email})
    assert begin.status_code == 200, f"auth/begin failed: {begin.status_code} {begin.text}"
    callback = client.get("/auth/callback", params={"token": sender.last_token()})
    assert callback.status_code == 303, f"auth/callback failed: {callback.status_code} {callback.text}"


def seed_match(db_url: str, user_email: str, match_id: str) -> None:
    """Insert a MatchRow for the user identified by email.

    Looks up user_id by email (must exist - call after login), then inserts
    a MatchRow with storage_prefix=f"matches/{match_id}".
    """
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _insert() -> None:
        async with sf() as s:
            row = (await s.execute(_select(User).where(User.email == user_email))).scalar_one()
            user_id = row.id
        async with sf() as s:
            match_row = MatchRow(
                user_id=user_id,
                match_id=match_id,
                name=f"Test match {match_id}",
                storage_prefix=f"matches/{match_id}",
            )
            s.add(match_row)
            await s.commit()

    asyncio.run(_insert())


# ---------------------------------------------------------------------------
# S3 storage double (moto-backed)
# ---------------------------------------------------------------------------
#
# Lifted out of test_hosted_raw_upload.py (originally file-local as
# `hosted_client`'s inner `_stub_tenant_storage`) so any test file that
# needs a real S3Storage against an in-memory bucket - not just the raw
# upload surface - can reuse it without duplicating the moto plumbing.


@contextmanager
def moto_s3_storage(monkeypatch: pytest.MonkeyPatch, bucket: str) -> Iterator[dict[str, object]]:
    """Stub ``splitsmith.ui.server._tenant_s3_storage`` against a moto bucket.

    Sets the ``SPLITSMITH_S3_*`` env vars hosted-mode wiring needs to
    decide storage is configured, creates ``bucket`` in an in-memory
    moto S3 backend, and monkeypatches ``_tenant_s3_storage`` so every
    per-tenant ``S3Storage`` the app builds is bound to that backend
    instead of a real network client.

    Callers create the app *inside* this context, then drive one
    authenticated request so a tenant resolves - after that,
    ``captured["storage"]`` holds the constructed ``S3Storage`` (every
    request rebuilds an equivalent instance against the same
    bucket/prefix/client, so any one capture is representative).
    """
    import boto3
    from moto import mock_aws

    from splitsmith.storage import S3Storage

    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", bucket)
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "us-east-1")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "secret")

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=bucket)

        from splitsmith.ui import server as server_mod

        captured: dict[str, object] = {}

        def _stub_tenant_storage(client: object, _bucket: object, user_id: str) -> S3Storage:
            storage = S3Storage(bucket=bucket, prefix=f"users/{user_id}/", client=s3)
            captured["storage"] = storage
            return storage

        monkeypatch.setattr(server_mod, "_tenant_s3_storage", _stub_tenant_storage)
        yield captured
