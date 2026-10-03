"""Which routes hosted mode serves, decided in one table.

``ui/route_scope.py`` lists the routes that only make sense on the
operator's own machine (they browse, probe, reveal or write paths the
caller names, or touch the process itself). ``enforce_local_only`` wraps
each of them once at app build, so in hosted mode they answer the same
404 as an unknown route before the handler runs.

The meta-test below is what keeps that table honest: every route whose
handler takes a path-like parameter must be either in the local-only
table or in the confined-in-hosted allowlist with a reason, so the next
such route fails here instead of shipping.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from splitsmith.ui import route_scope
from tests.hosted_helpers import _CapturingSender, login

#: The local-only routes, spelled out here rather than read from the
#: table, so dropping one from the table fails this file.
EXPECTED_LOCAL_ONLY: set[tuple[str, str]] = {
    ("GET", "/api/shooters/{slug}/fs/list"),
    ("GET", "/api/fs/list-dirs"),
    ("GET", "/api/shooters/{slug}/fs/probe"),
    ("GET", "/api/shooters/{slug}/thumbnails/{cache_key}.jpg"),
    ("POST", "/api/shooters/{slug}/videos/scan"),
    ("POST", "/api/shooters/{slug}/videos/relink/scan"),
    ("POST", "/api/shooters/{slug}/videos/relink/apply"),
    ("POST", "/api/shooters/{slug}/project/settings"),
    ("POST", "/api/files/reveal"),
    ("POST", "/api/shooters/{slug}/videos/reveal"),
    ("POST", "/api/match/merge/plan"),
    ("POST", "/api/match/merge/execute"),
    ("GET", "/api/fixture/audit"),
    ("PUT", "/api/fixture/audit"),
    ("GET", "/api/fixture/peaks"),
    ("GET", "/api/fixture/audio"),
    ("GET", "/api/fixture/video"),
    ("GET", "/api/dev/model"),
    ("GET", "/api/dev/review-queue"),
    ("POST", "/api/dev/review-queue/{slug}/confirm"),
    ("POST", "/api/shutdown"),
    ("POST", "/api/match/footage-sort/scan"),
    ("GET", "/api/match/footage-sort"),
    ("DELETE", "/api/match/footage-sort/{scan_id}"),
    ("GET", "/api/match/footage-sort/{scan_id}"),
    ("PUT", "/api/match/footage-sort/{scan_id}/decisions"),
    ("GET", "/api/match/footage-sort/{scan_id}/thumbs/{index}.jpg"),
    ("GET", "/api/match/footage-sort/{scan_id}/thumbs/{index}/strip.jpg"),
    ("GET", "/api/match/footage-sort/{scan_id}/clips/{index}/video"),
    ("POST", "/api/match/footage-sort/{scan_id}/import"),
    ("GET", "/api/system/chromium"),
    ("POST", "/api/system/chromium/install"),
}

#: A parameter whose name says it carries a filesystem location.
_PATH_LIKE = re.compile(
    r"^(path|paths|dir|folder|root|dest|file|filename|inputs|output)$"
    r"|_(path|paths|dir|dirs|folder|root)$"
    r"|^dest_"
)


def _param_names(route: APIRoute) -> set[str]:
    from pydantic import BaseModel

    names: set[str] = set()

    def walk(dep) -> None:
        for p in [*dep.path_params, *dep.query_params]:
            names.add(p.name)
        for p in dep.body_params:
            names.add(p.name)
            ann = p.field_info.annotation
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                names.update(ann.model_fields)
        for sub in dep.dependencies:
            walk(sub)

    walk(route.dependant)
    return names


def _api_routes() -> list[APIRoute]:
    from splitsmith.ui.server import create_app

    return [r for r in create_app().routes if isinstance(r, APIRoute)]


def test_local_only_table_is_the_expected_set() -> None:
    assert set(route_scope.LOCAL_ONLY_ROUTES) == EXPECTED_LOCAL_ONLY


def test_every_path_taking_route_is_classified() -> None:
    unclassified = []
    for route in _api_routes():
        if not route.path.startswith("/api/"):
            continue
        path_like = sorted(n for n in _param_names(route) if _PATH_LIKE.search(n))
        if not path_like:
            continue
        for method in route.methods:
            key = (method, route.path)
            if key in route_scope.LOCAL_ONLY_ROUTES or key in route_scope.HOSTED_CONFINED_ROUTES:
                continue
            unclassified.append(f"{method} {route.path} {path_like}")
    assert unclassified == [], (
        "routes taking a path-like parameter must be in route_scope.LOCAL_ONLY_ROUTES or "
        "HOSTED_CONFINED_ROUTES (with the reason they are safe in hosted mode):\n" + "\n".join(unclassified)
    )


def test_table_entries_name_real_routes_and_are_enforced() -> None:
    routes = {(m, r.path): r for r in _api_routes() for m in r.methods}
    for key in [*route_scope.LOCAL_ONLY_ROUTES, *route_scope.HOSTED_CONFINED_ROUTES]:
        assert key in routes, f"stale route_scope entry: {key}"
    for key in route_scope.LOCAL_ONLY_ROUTES:
        assert getattr(routes[key].app, "_local_only_guard", False), f"not enforced: {key}"
    for key, reason in route_scope.HOSTED_CONFINED_ROUTES.items():
        assert reason.strip(), f"no reason given for {key}"


def _fill(path: str, slug: str) -> str:
    return (
        path.replace("{slug}", slug)
        .replace("{cache_key}", "c72e2952421c98ff")
        .replace("{scan_id}", "abc123")
        .replace("{index}", "0")
    )


@pytest.fixture
def signed_in(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> tuple[TestClient, str, str]:
    """A hosted-native match (full capabilities, unlike a desktop mirror,
    whose read-only gate would answer writes with its own 403 first)."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    resp = client.post(
        "/api/match/create-manual",
        json={
            "name": "Route scope",
            "stages": [{"stage_number": 1, "stage_name": "S1"}],
            "primary_shooter": {"name": "Me"},
        },
    )
    assert resp.status_code == 200, resp.text
    return client, resp.json()["match_id"], resp.json()["default_shooter_slug"]


@pytest.mark.parametrize("method,path", sorted(EXPECTED_LOCAL_ONLY))
def test_local_only_routes_are_not_served_hosted(
    signed_in: tuple[TestClient, str, str], tmp_path, method: str, path: str
) -> None:
    client, match_id, slug = signed_in
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "clip.mp4").write_bytes(b"\x00" * 64)
    url = _fill(path, slug)
    if url.startswith(("/api/shooters/", "/api/match/")):
        url = f"/api/matches/{match_id}/{url[len('/api/'):]}"
    body = {
        "source_dir": str(outside),
        "source_paths": [str(outside / "clip.mp4")],
        "search_root": str(outside),
        "decisions": {},
        "raw_dir": str(outside),
        "path": str(outside / "clip.mp4"),
        "inputs": [str(outside), str(outside)],
        "output": str(outside / "merged"),
    }
    resp = client.request(
        method,
        url,
        params={"path": str(outside / "clip.mp4")},
        json=body if method in ("POST", "PUT") else None,
    )
    assert resp.status_code == 404, (method, url, resp.status_code, resp.text)
    assert resp.json() == {"detail": "not found"}
    assert not (outside / "merged").exists()
