"""Hosted match working folders are per account, and match ids have a shape.

``matches`` is unique per ``(user_id, match_id)``, so two accounts can
each own a match with the same id (a desktop mirror's id comes from the
desktop). The container-side working folder a match's files are mirrored
into is therefore keyed by account too:
``<SPLITSMITH_PROJECTS_DIR>/users/<user_id>/matches/<match_id>``. And a
match id is one path segment of letters, digits, ``-`` and ``_``, checked
where a mirror is adopted and again where a request names one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject
from tests.hosted_helpers import _CapturingSender, login
from tests.mirror_helpers import seed_mirror, sync_docs_url

MID = "shared-id-0001"
SLUG = "alice"


def _adopt(client: TestClient, sender: _CapturingSender, email: str) -> None:
    client.cookies.clear()
    login(client, sender, email)
    seed_mirror(client, MID, "same id")
    roster = match_model.Match(match_id=MID, name="same id", shooters=[SLUG], stages=[]).model_dump(
        mode="json"
    )
    assert (
        client.put(sync_docs_url(MID, "match"), params={"expected_version": 1}, json=roster).status_code
        == 200
    )
    resp = client.put(
        sync_docs_url(MID, f"project/{SLUG}"),
        params={"expected_version": 0},
        json=MatchProject(name="same id").model_dump(mode="json"),
    )
    assert resp.status_code == 200, resp.text


def _work_roots() -> list[Path]:
    base = Path(os.environ["SPLITSMITH_PROJECTS_DIR"])
    return sorted(p for p in base.rglob(MID) if p.is_dir())


def test_two_accounts_with_the_same_match_id_get_separate_folders(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    _adopt(client, sender, "first@example.com")
    assert client.get(f"/api/matches/{MID}/match/shooters").status_code == 200
    roots = _work_roots()
    assert len(roots) == 1
    first_exports = roots[0] / "shooters" / SLUG / "exports"
    first_exports.mkdir(parents=True)
    (first_exports / "stage1.csv").write_text("first account's export\n")
    assert client.get(f"/api/matches/{MID}/shooters/{SLUG}/exports/file/stage1.csv").status_code == 200

    _adopt(client, sender, "second@example.com")
    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/exports/file/stage1.csv")
    assert resp.status_code == 404, resp.text
    assert b"first account" not in resp.content
    assert len(_work_roots()) == 2


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "/abs", "a b", "", "x" * 200, "..\\x"])
def test_sync_refuses_a_match_id_that_is_not_one_plain_segment(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], bad: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    resp = client.post("/api/sync/matches", json={"match_id": bad, "name": "x"})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("good", ["01JMIRRBEEPGATE0000000001", "ssi-12345", "m_seam_test", "27190"])
def test_sync_accepts_real_match_id_shapes(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender], good: str
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post("/api/sync/matches", json={"match_id": good, "name": "x"}).status_code == 200


def test_worker_registry_does_not_reuse_another_accounts_root() -> None:
    from splitsmith.match_registry import MatchRegistry

    owner = {"value": "first"}

    def resolver(match_id: str) -> Path:
        return Path("/work") / owner["value"] / match_id

    registry = MatchRegistry(miss_resolver=resolver, remember_resolved=False)
    assert registry.resolve(MID) == Path("/work/first") / MID
    owner["value"] = "second"
    assert registry.resolve(MID) == Path("/work/second") / MID


def test_a_stored_match_id_outside_the_shape_is_not_served(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """A row that predates the shape check never becomes a folder name."""
    from tests.hosted_helpers import seed_match

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", "..")
    base = Path(os.environ["SPLITSMITH_PROJECTS_DIR"])
    before = {p for p in base.parent.rglob("*") if p.is_dir()}
    resp = client.get("/api/matches/%2E%2E/match/shooters")
    assert resp.status_code == 404, resp.text
    after = {p for p in base.parent.rglob("*") if p.is_dir()}
    assert after <= before | {base}
