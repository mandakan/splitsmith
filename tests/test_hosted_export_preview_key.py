"""The export-preview cache is scoped by account in hosted mode.

The preview cache (``runtime().cache_dir / "export-preview"``) is one
folder per process. Its content key covers the shooter slug, the
project's ``updated_at`` and the audit, none of which names an account,
and slugs repeat across accounts. Hosted therefore adds the account (and
the match) to the key; local mode keeps its key as it was, so an existing
local cache stays valid.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import export_preview as ep
from splitsmith.db import User, create_engine, sessionmaker
from splitsmith.ui import export_preview_api
from tests.hosted_helpers import _CapturingSender, login


def test_preview_key_takes_an_owner_and_local_keys_are_unchanged() -> None:
    spec = ep.PreviewSpec(card="title", stage_number=1)
    common = {"slug": "me", "project_updated_at": "t1", "audit": "a1"}
    local = ep.preview_key(spec, **common)
    assert ep.preview_key(spec, **common, owner=None) == local
    first = ep.preview_key(spec, **common, owner="user-1/match-a")
    second = ep.preview_key(spec, **common, owner="user-2/match-a")
    assert len({local, first, second}) == 3


def _user_id(db_url: str, email: str) -> str:
    sf = sessionmaker(create_engine(db_url))

    async def _get() -> str:
        async with sf() as s:
            return str((await s.execute(_select(User).where(User.email == email))).scalar_one().id)

    return asyncio.run(_get())


def test_hosted_preview_key_names_the_account_and_match(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from splitsmith import runtime as runtime_module

    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    seen: list[dict] = []
    real = export_preview_api.preview_key

    def _spy(spec, **kwargs):
        seen.append(kwargs)
        return real(spec, **kwargs)

    monkeypatch.setattr(export_preview_api, "preview_key", _spy)

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": "Preview key",
            "stages": [{"stage_number": 1, "stage_name": "S1"}],
            "primary_shooter": {"name": "Me"},
        },
    )
    assert resp.status_code == 200, resp.text
    mid, slug = resp.json()["match_id"], resp.json()["default_shooter_slug"]
    client.post(
        f"/api/matches/{mid}/shooters/{slug}/export-preview", json={"card": "frame", "stage_number": 1}
    )
    runtime_module._clear_runtime_cache()

    assert seen, "the preview never computed a cache key"
    assert seen[-1]["owner"] == f"{_user_id(hosted_env, 'owner@example.com')}/{mid}"
