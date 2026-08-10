"""HTTP-surface tests for the /api/sync/* routes (#631).

The hosted receiving end of the desktop-to-hosted sync MVP: a desktop
client (Tasks 5+) pushes a match plus its state docs as a read-only
mirror. This file exercises the router in isolation - match adopt/create,
per-doc-kind upserts (match / project / audit), the not_a_mirror /
match_exists_hosted 409s, 422 schema validation, 404s (unknown match,
cross-tenant), and local-mode 404 - via real HTTP requests, both under a
session cookie and under a desktop bearer token (the shape a real desktop
client actually uses).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject
from tests.hosted_helpers import _CapturingSender, login, seed_match

CREATE_URL = "/api/sync/matches"


def _docs_url(match_id: str, kind: str) -> str:
    return f"/api/sync/matches/{match_id}/docs/{kind}"


def _put_doc(
    client: TestClient,
    match_id: str,
    kind: str,
    *,
    body: dict,
    expected_version: int,
):
    """PUT one doc, always sending ``expected_version`` - the param is
    required, no compat path for an unversioned call."""
    return client.put(
        _docs_url(match_id, kind),
        params={"expected_version": expected_version},
        json=body,
    )


def _bearer_for(client: TestClient) -> dict[str, str]:
    """Mint a desktop token for the currently-logged-in session, then
    clear the session cookie so subsequent requests authenticate purely
    via the bearer - the real desktop client never holds a cookie."""
    raw = client.post("/api/me/desktop-tokens", json={"name": "test rig"}).json()["token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {raw}"}


# local mode: the whole surface 404s


def test_local_mode_404() -> None:
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 404
        put_resp = client.put(_docs_url("m1", "match"), params={"expected_version": 0}, json={"name": "x"})
        assert put_resp.status_code == 404


# anonymous requests are rejected


def test_anonymous_create_rejected(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    resp = client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"})
    assert resp.status_code == 401


# create-or-adopt


def test_create_is_idempotent(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    first = client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"})
    assert first.status_code == 200, first.text
    assert first.json() == {"match_id": "m1", "origin": "desktop"}

    second = client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1 renamed"})
    assert second.status_code == 200, second.text
    assert second.json() == {"match_id": "m1", "origin": "desktop"}


def test_create_via_bearer_only(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    """The realistic desktop path: no session cookie, only the bearer."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    headers = _bearer_for(client)

    resp = client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"match_id": "m1", "origin": "desktop"}


def test_create_conflicts_with_native_hosted_match(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(_db_url_for(client), "owner@example.com", "native1")

    resp = client.post(CREATE_URL, json={"match_id": "native1", "name": "whatever"})
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "match_exists_hosted"}


def _db_url_for(client: TestClient) -> str:
    import os

    return os.environ["SPLITSMITH_DATABASE_URL"]


# recent-projects registration (#794)


def test_ensure_match_registers_recent_project(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A push/ensure to a fresh match must register a recents row for
    the owner - otherwise the hosted picker can never show a
    sync-only match."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    resp = client.post(CREATE_URL, json={"match_id": "synced1", "name": "HFO Masters"})
    assert resp.status_code == 200, resp.text

    projects = client.get("/api/me/recent-projects").json()["projects"]
    matches = [p for p in projects if p["match_id"] == "synced1"]
    assert len(matches) == 1, projects
    assert matches[0]["name"] == "HFO Masters"


def test_second_ensure_does_not_duplicate_or_bump_last_opened_at(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """A repeat push is idempotent - one row, name refreshed, but the
    picker ordering must not move just because a background sync
    happened to land (a push is not an open)."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    assert client.post(CREATE_URL, json={"match_id": "synced2", "name": "First Name"}).status_code == 200
    first = next(
        p for p in client.get("/api/me/recent-projects").json()["projects"] if p["match_id"] == "synced2"
    )

    resp = client.post(CREATE_URL, json={"match_id": "synced2", "name": "Renamed"})
    assert resp.status_code == 200, resp.text
    projects = client.get("/api/me/recent-projects").json()["projects"]
    matches = [p for p in projects if p["match_id"] == "synced2"]
    assert len(matches) == 1, projects
    assert matches[0]["name"] == "Renamed"
    assert matches[0]["last_opened_at"] == first["last_opened_at"]


def test_other_users_recent_projects_stay_empty(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The row lands under the pushing tenant only - a second user's
    picker must stay empty."""
    client, sender = hosted_app
    login(client, sender, "usera@example.com")
    assert client.post(CREATE_URL, json={"match_id": "synced3", "name": "A's match"}).status_code == 200

    client.cookies.clear()
    login(client, sender, "userb@example.com")
    projects = client.get("/api/me/recent-projects").json()["projects"]
    assert projects == []


# match-doc upserts


def test_put_match_doc_versions_increment_and_422_on_garbage(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    doc = match_model.Match(name="Match 1").model_dump(mode="json")

    first = _put_doc(client, "m1", "match", body=doc, expected_version=0)
    assert first.status_code == 200, first.text
    assert first.json() == {"version": 1}

    second = _put_doc(client, "m1", "match", body=doc, expected_version=1)
    assert second.status_code == 200, second.text
    assert second.json() == {"version": 2}

    garbage = _put_doc(client, "m1", "match", body={"totally": "not a match doc"}, expected_version=2)
    assert garbage.status_code == 422, garbage.text


def test_put_match_doc_unknown_match_404(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")

    doc = match_model.Match(name="Ghost").model_dump(mode="json")
    resp = _put_doc(client, "ghost", "match", body=doc, expected_version=0)
    assert resp.status_code == 404, resp.text


def test_put_match_doc_against_native_match_is_not_a_mirror(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(_db_url_for(client), "owner@example.com", "native2")

    doc = match_model.Match(name="Native").model_dump(mode="json")
    resp = _put_doc(client, "native2", "match", body=doc, expected_version=0)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "not_a_mirror"}


def test_second_user_cannot_touch_first_users_mirror(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "usera@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "A's match"}).status_code == 200

    client.cookies.clear()
    login(client, sender, "userb@example.com")

    doc = match_model.Match(name="A's match").model_dump(mode="json")
    resp = _put_doc(client, "m1", "match", body=doc, expected_version=0)
    assert resp.status_code == 404, resp.text


# project-doc upserts


def test_put_project_doc_round_trips_and_422_on_garbage(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    doc = MatchProject(name="Shooter A").model_dump(mode="json")
    ok = _put_doc(client, "m1", "project/shooter-a", body=doc, expected_version=0)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"version": 1}

    garbage = _put_doc(client, "m1", "project/shooter-a", body={"nope": True}, expected_version=1)
    assert garbage.status_code == 422, garbage.text


# audit-doc upserts (schemaless)


def test_put_audit_doc_is_schemaless_and_versions_increment(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    first = _put_doc(client, "m1", "audit/shooter-a/1", body={"shots": [0.5, 1.1]}, expected_version=0)
    assert first.status_code == 200, first.text
    assert first.json() == {"version": 1}

    second = _put_doc(client, "m1", "audit/shooter-a/1", body={"shots": [0.5, 1.1, 1.9]}, expected_version=1)
    assert second.status_code == 200, second.text
    assert second.json() == {"version": 2}


# doc manifest + per-doc GET routes


def test_doc_manifest_lists_versions(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    match_doc = match_model.Match(name="Match 1").model_dump(mode="json")
    project_doc = MatchProject(name="Anna").model_dump(mode="json")
    assert _put_doc(client, "m1", "match", body=match_doc, expected_version=0).status_code == 200
    assert _put_doc(client, "m1", "project/anna", body=project_doc, expected_version=0).status_code == 200

    resp = client.get("/api/sync/matches/m1/docs")
    assert resp.status_code == 200, resp.text
    docs = resp.json()["docs"]
    by_key = {(d["doc_kind"], d["slug"], d["stage_number"]): d for d in docs}
    assert by_key[("match", None, None)]["version"] == 1
    assert by_key[("project", "anna", None)]["version"] == 1
    assert "updated_at" in by_key[("match", None, None)]


def test_get_doc_roundtrip_and_404(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    match_doc = match_model.Match(name="Match 1").model_dump(mode="json")
    assert _put_doc(client, "m1", "match", body=match_doc, expected_version=0).status_code == 200

    resp = client.get("/api/sync/matches/m1/docs/match")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"doc": match_doc, "version": 1}

    resp = client.get("/api/sync/matches/m1/docs/audit/anna/9")
    assert resp.status_code == 404, resp.text


def test_put_doc_requires_expected_version(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    match_doc = match_model.Match(name="Match 1").model_dump(mode="json")
    resp = client.put(_docs_url("m1", "match"), json=match_doc)  # no expected_version query param
    assert resp.status_code == 422, resp.text


def test_put_doc_version_conflict_409(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    """A stale UPDATE (not a duplicate INSERT) - a second ``expected_version
    == 0`` save would also conflict on Postgres's coalesce unique index,
    but SQLite's plain unique index doesn't catch a NULL-slug duplicate
    (see ``test_insert_conflict_when_already_exists`` in
    ``test_project_state_store.py``), so this drives the conflict through
    the UPDATE path instead - the same one every real re-push race hits.
    """
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    assert client.post(CREATE_URL, json={"match_id": "m1", "name": "Match 1"}).status_code == 200

    match_doc = match_model.Match(name="Match 1").model_dump(mode="json")
    assert _put_doc(client, "m1", "match", body=match_doc, expected_version=0).status_code == 200
    assert _put_doc(client, "m1", "match", body=match_doc, expected_version=1).status_code == 200

    resp = client.put(
        _docs_url("m1", "match"),
        params={"expected_version": 1},  # stale: row is at version 2 now
        json=match_doc,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "version_conflict"
