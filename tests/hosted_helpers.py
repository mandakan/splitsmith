"""Shared fixtures for tests that need a hosted-mode app + auth dance.

Extracted from tests/test_auth_routes.py so test_share_routes.py (and any
future test file) can reuse them without duplication.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
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
    # read the emitted token.
    app.state.splitsmith_state.auth._email = sender
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
