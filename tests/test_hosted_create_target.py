"""Hosted mode decides where a created or imported match lands on disk.

``POST /api/me/projects/import`` takes a ``dest_root`` and the two
create-match routes take a ``project_folder``; both exist for the local
SPA, where the user picks a folder on their own disk. In hosted mode the
server owns the layout: every match lands under the caller's
``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/projects/`` prefix whatever
the request says, and a target whose resolved path leaves that prefix
is refused rather than written (an import with ``overwrite`` deletes the
existing target first).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith.db import User, create_engine, sessionmaker
from tests.hosted_helpers import _CapturingSender, login

EMAIL = "owner@example.com"


def _archive(top: str, *, marker: bytes = b"from-archive\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (
            (f"{top}/project.json", json.dumps({"name": "imported"}).encode()),
            (f"{top}/marker.txt", marker),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _user_id(db_url: str, email: str) -> str:
    sf = sessionmaker(create_engine(db_url))

    async def _get() -> str:
        async with sf() as s:
            return str((await s.execute(_select(User).where(User.email == email))).scalar_one().id)

    return asyncio.run(_get())


def _tenant_projects(db_url: str) -> Path:
    return Path(os.environ["SPLITSMITH_PROJECTS_DIR"]) / "users" / _user_id(db_url, EMAIL) / "projects"


def _import(client: TestClient, archive: bytes, dest_root: str, *, overwrite: bool = False):
    return client.post(
        "/api/me/projects/import",
        files={"archive": ("p.tar.gz", archive, "application/gzip")},
        data={"dest_root": dest_root, "overwrite": "true" if overwrite else "false", "bind": "false"},
    )


@pytest.fixture
def signed_in(hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]) -> TestClient:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    return client


def test_hosted_import_lands_under_the_tenant_prefix(
    signed_in: TestClient, hosted_env: str, tmp_path: Path
) -> None:
    chosen = tmp_path / "chosen-dest"
    resp = _import(signed_in, _archive("myproj"), str(chosen))
    assert resp.status_code == 200, resp.text
    root = Path(resp.json()["project_root"])
    assert root == _tenant_projects(hosted_env).resolve() / "myproj"
    assert (root / "marker.txt").read_bytes() == b"from-archive\n"
    assert not chosen.exists()


def test_hosted_import_overwrite_never_touches_the_requested_dest(
    signed_in: TestClient, hosted_env: str, tmp_path: Path
) -> None:
    chosen = tmp_path / "chosen-dest"
    existing = chosen / "myproj"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("keep\n")

    resp = _import(signed_in, _archive("myproj"), str(chosen), overwrite=True)
    assert resp.status_code == 200, resp.text
    assert (existing / "keep.txt").read_text() == "keep\n"
    assert not (existing / "marker.txt").exists()


def test_hosted_import_refuses_a_target_that_resolves_outside_the_prefix(
    signed_in: TestClient, hosted_env: str, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep\n")
    tenant = _tenant_projects(hosted_env)
    tenant.mkdir(parents=True)
    (tenant / "myproj").symlink_to(elsewhere, target_is_directory=True)

    resp = _import(signed_in, _archive("myproj"), "/ignored", overwrite=True)
    assert resp.status_code == 400, resp.text
    # The refusal is the bound itself, not a later failure on the link.
    assert "outside the allowed folder" in resp.json()["detail"]
    assert (elsewhere / "keep.txt").read_text() == "keep\n"
    assert not (elsewhere / "marker.txt").exists()


def test_hosted_create_manual_ignores_the_requested_folder(
    signed_in: TestClient, hosted_env: str, tmp_path: Path
) -> None:
    chosen = tmp_path / "manual-dest"
    resp = signed_in.post(
        "/api/match/create-manual",
        json={
            "name": "Manual Match",
            "project_folder": str(chosen),
            "stages": [{"stage_number": 1, "stage_name": "S1"}],
            "primary_shooter": {"name": "Me"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert not chosen.exists()
    assert Path(resp.json()["project_root"]).resolve().is_relative_to(_tenant_projects(hosted_env).resolve())


def test_hosted_create_target_is_shared_by_both_create_routes(
    signed_in: TestClient, hosted_env: str, tmp_path: Path
) -> None:
    """``create-from-scoreboard`` resolves its folder through the same
    function as ``create-manual``; pin the function itself so that route
    cannot drift without reaching the upstream scoreboard in a test."""
    from splitsmith.ui import server

    state = signed_in.app.state.splitsmith_state
    token = server.current_tenant.set(state.build_tenant(_user_id(hosted_env, EMAIL)))
    try:
        target = server._resolve_create_target(state, project_folder=str(tmp_path / "x"), name="My Match")
        assert target == _tenant_projects(hosted_env).resolve() / "my-match"

        tenant = _tenant_projects(hosted_env)
        tenant.mkdir(parents=True, exist_ok=True)
        (tenant / "linked").symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(HTTPException) as exc:
            server._resolve_create_target(state, project_folder=None, name="Linked")
        assert exc.value.status_code == 400
    finally:
        server.current_tenant.reset(token)


def test_local_create_target_still_honours_the_requested_folder(tmp_path: Path) -> None:
    from splitsmith.ui import server

    chosen = tmp_path / "picked"
    target = server._resolve_create_target(None, project_folder=str(chosen), name="Whatever")  # type: ignore[arg-type]
    assert target == chosen
