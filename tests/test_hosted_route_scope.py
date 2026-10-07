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
from typing import Annotated, Any

import pytest
from fastapi import Body, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from splitsmith.ui import route_scope
from tests.hosted_helpers import _CapturingSender, login

#: The local-only routes, spelled out here rather than read from the
#: table, so dropping one from the table fails this file.
EXPECTED_LOCAL_ONLY: set[tuple[str, str]] = {
    ("GET", "/api/looks/{name}/fonts"),
    ("POST", "/api/looks/{name}/fonts"),
    ("GET", "/api/looks/{name}/fonts/{file}"),
    ("GET", "/api/shooters/{slug}/fs/list"),
    ("GET", "/api/fs/list-dirs"),
    ("GET", "/api/shooters/{slug}/fs/probe"),
    ("GET", "/api/shooters/{slug}/thumbnails/{cache_key}.jpg"),
    ("POST", "/api/shooters/{slug}/videos/scan"),
    ("POST", "/api/shooters/{slug}/videos/relink/scan"),
    ("POST", "/api/shooters/{slug}/videos/relink/apply"),
    ("POST", "/api/shooters/{slug}/project/settings"),
    ("POST", "/api/files/reveal"),
    # The template editor (#1265): local only until the sandbox (#1266).
    ("GET", "/api/looks/{name}/templates"),
    ("PUT", "/api/looks/{name}/templates"),
    ("GET", "/api/looks/{name}/samples"),
    ("POST", "/api/looks/{name}/check"),
    ("POST", "/api/looks/{name}/reveal"),
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


def _param_names(route: APIRoute) -> tuple[set[str], set[str]]:
    """Every parameter name a route takes, and its bodies of no declared shape.

    Names come from path and query parameters, body parameters, and every
    field of every model reachable from a body annotation: through
    ``Optional`` / unions, ``list`` / ``dict`` / other generics, and nested
    models. A body parameter (or a model field) typed ``dict`` / ``Any``
    has no field names to check, so it is reported separately and must be
    classified on its own.
    """
    import typing

    from pydantic import BaseModel

    names: set[str] = set()
    untyped: set[str] = set()
    seen: set[type] = set()

    def is_untyped(ann: object) -> bool:
        origin = typing.get_origin(ann) or ann
        return (
            ann is typing.Any
            or ann is object
            or origin in (dict, typing.Mapping)
            or (isinstance(origin, type) and issubclass(origin, dict))
        )

    def visit(label: str, ann: object) -> None:
        if is_untyped(ann):
            untyped.add(label)
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            if ann in seen:
                return
            seen.add(ann)
            for field_name, field in ann.model_fields.items():
                names.add(field_name)
                visit(f"{label}.{field_name}", field.annotation)
            return
        for arg in typing.get_args(ann):
            visit(label, arg)

    def walk(dep) -> None:
        for p in [*dep.path_params, *dep.query_params]:
            names.add(p.name)
        for p in dep.body_params:
            names.add(p.name)
            visit(p.name, p.field_info.annotation)
        for sub in dep.dependencies:
            walk(sub)

    walk(route.dependant)
    return names, untyped


def _api_routes() -> list[APIRoute]:
    from splitsmith.ui.server import create_app

    return [r for r in create_app().routes if isinstance(r, APIRoute)]


def test_local_only_table_is_the_expected_set() -> None:
    assert set(route_scope.LOCAL_ONLY_ROUTES) == EXPECTED_LOCAL_ONLY


def _unclassified(
    routes: list[APIRoute],
    local_only: frozenset[tuple[str, str]] | set[tuple[str, str]],
    confined: dict[tuple[str, str], str],
    opaque: dict[tuple[str, str], str],
) -> list[str]:
    """Routes with a path-like parameter (or a body of no declared shape)
    that no table accounts for."""
    out = []
    for route in routes:
        if not route.path.startswith("/api/"):
            continue
        names, untyped = _param_names(route)
        path_like = sorted(n for n in names if _PATH_LIKE.search(n))
        for method in sorted(route.methods):
            key = (method, route.path)
            if key in local_only:
                continue
            if path_like and key not in confined:
                out.append(f"{method} {route.path} {path_like}")
            if untyped and key not in confined and key not in opaque:
                out.append(f"{method} {route.path} untyped body {sorted(untyped)}")
    return out


class _Paths(BaseModel):
    source_dir: str


class _Outer(BaseModel):
    opts: _Paths


def test_the_classifier_sees_every_body_shape() -> None:
    """Optional, nested and list-of models, and an untyped body, are all
    seen; before, only a bare model annotation was expanded."""

    app = FastAPI()

    @app.post("/api/optional")
    def optional(req: _Paths | None = None) -> None: ...

    @app.post("/api/nested")
    def nested(req: _Outer) -> None: ...

    @app.post("/api/listed")
    def listed(req: list[_Paths]) -> None: ...

    @app.put("/api/untyped")
    def untyped(body: Annotated[dict[str, Any], Body()]) -> None: ...

    @app.put("/api/any")
    def anything(body: Annotated[Any, Body()]) -> None: ...

    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    found = _unclassified(routes, set(), {}, {})
    for path in ("/api/optional", "/api/nested", "/api/listed", "/api/untyped", "/api/any"):
        assert any(f" {path} " in line for line in found), (path, found)


def test_every_path_taking_route_is_classified() -> None:
    unclassified = _unclassified(
        _api_routes(),
        route_scope.LOCAL_ONLY_ROUTES,
        route_scope.HOSTED_CONFINED_ROUTES,
        route_scope.UNTYPED_BODY_ROUTES,
    )
    assert unclassified == [], (
        "routes taking a path-like parameter must be in route_scope.LOCAL_ONLY_ROUTES or "
        "HOSTED_CONFINED_ROUTES (with the reason they are safe in hosted mode):\n" + "\n".join(unclassified)
    )


def test_table_entries_name_real_routes_and_are_enforced() -> None:
    routes = {(m, r.path): r for r in _api_routes() for m in r.methods}
    for key in [
        *route_scope.LOCAL_ONLY_ROUTES,
        *route_scope.HOSTED_CONFINED_ROUTES,
        *route_scope.UNTYPED_BODY_ROUTES,
    ]:
        assert key in routes, f"stale route_scope entry: {key}"
    for key in route_scope.LOCAL_ONLY_ROUTES:
        assert getattr(routes[key].app, "_local_only_guard", False), f"not enforced: {key}"
    for key, reason in [
        *route_scope.HOSTED_CONFINED_ROUTES.items(),
        *route_scope.UNTYPED_BODY_ROUTES.items(),
    ]:
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
