"""Which routes hosted mode serves.

The local app runs on the operator's own machine and some of its routes
exist for exactly that: they browse folders, probe or reveal files, scan
a directory the caller names, write project folders where the caller
says, or stop the process. None of that means anything on a shared
hosted container, so those routes are listed once here and
:func:`enforce_local_only` makes each of them answer hosted requests with
the same 404 as an unknown route, before the handler runs.

``HOSTED_CONFINED_ROUTES`` is the other half: routes that take a
path-like parameter and *are* served hosted, each with the reason the
parameter cannot reach outside the caller's own data there.
``tests/test_hosted_route_scope.py`` fails when a route with a path-like
parameter is in neither table, so a new one gets a decision instead of
shipping by default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

#: (method, route path template) pairs served in local mode only.
LOCAL_ONLY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Folder picker: browses and probes the server's filesystem.
        ("GET", "/api/shooters/{slug}/fs/list"),
        ("GET", "/api/fs/list-dirs"),
        ("GET", "/api/shooters/{slug}/fs/probe"),
        ("GET", "/api/shooters/{slug}/thumbnails/{cache_key}.jpg"),
        # Footage registration from a folder on disk, and relinking moved originals.
        ("POST", "/api/shooters/{slug}/videos/scan"),
        ("POST", "/api/shooters/{slug}/videos/relink/scan"),
        ("POST", "/api/shooters/{slug}/videos/relink/apply"),
        # Per-project cache directory overrides (raw_dir, trimmed_dir, ...).
        ("POST", "/api/shooters/{slug}/project/settings"),
        # Opens the OS file manager.
        ("POST", "/api/files/reveal"),
        # The template editor (#1265): a template is code; hosted runs none
        # of an account's. Custom templates are desktop only, by decision.
        ("GET", "/api/looks/{name}/templates"),
        ("PUT", "/api/looks/{name}/templates"),
        ("GET", "/api/looks/{name}/samples"),
        ("POST", "/api/looks/{name}/check"),
        ("POST", "/api/looks/{name}/reveal"),
        # A Look's own font files (#1272) and brand logo: no hosted account assets yet.
        ("POST", "/api/looks/{name}/brand-logo"),
        ("GET", "/api/looks/{name}/brand/{file}"),
        ("GET", "/api/looks/{name}/fonts"),
        ("POST", "/api/looks/{name}/fonts"),
        ("GET", "/api/looks/{name}/fonts/{file}"),
        ("POST", "/api/shooters/{slug}/videos/reveal"),
        # Merges legacy project folders named by path.
        ("POST", "/api/match/merge/plan"),
        ("POST", "/api/match/merge/execute"),
        # Fixture lab and developer tools: read and write files by path.
        ("GET", "/api/fixture/audit"),
        ("PUT", "/api/fixture/audit"),
        ("GET", "/api/fixture/peaks"),
        ("GET", "/api/fixture/audio"),
        ("GET", "/api/fixture/video"),
        ("GET", "/api/dev/model"),
        ("GET", "/api/dev/review-queue"),
        ("POST", "/api/dev/review-queue/{slug}/confirm"),
        # Ends the embedded sidecar process.
        ("POST", "/api/shutdown"),
        # Footage sort (spec 2026-10-01): scans a folder on disk.
        ("POST", "/api/match/footage-sort/scan"),
        ("GET", "/api/match/footage-sort"),
        ("DELETE", "/api/match/footage-sort/{scan_id}"),
        ("GET", "/api/match/footage-sort/{scan_id}"),
        ("PUT", "/api/match/footage-sort/{scan_id}/decisions"),
        ("GET", "/api/match/footage-sort/{scan_id}/thumbs/{index}.jpg"),
        ("GET", "/api/match/footage-sort/{scan_id}/thumbs/{index}/strip.jpg"),
        ("GET", "/api/match/footage-sort/{scan_id}/clips/{index}/video"),
        ("POST", "/api/match/footage-sort/{scan_id}/import"),
        # Installs a browser on the server.
        ("GET", "/api/system/chromium"),
        ("POST", "/api/system/chromium/install"),
    }
)

#: Routes served in hosted mode although they take a path-like parameter,
#: with the reason that parameter stays inside the caller's own data.
_RAW_KEY = "filename goes through _sanitize_raw_filename (basename only) into the caller's raw/ storage key"
_REGISTERED = (
    "names a video already registered with the project; file work happens under the project's own "
    "dirs, which MatchProject confines to the shooter root when loaded from the state store"
)
_STREAM = (
    "path must match a registered video; MatchProject refuses to resolve an absolute or '..' path "
    "when loaded from the state store, so only the match's own working tree or storage is read"
)
_EXPORTS = (
    "filename is resolved inside the project's exports dir (confined like every project dir) "
    "and 400s outside it"
)

HOSTED_CONFINED_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/me/projects/import"): (
        "hosted ignores dest_root and imports under <PROJECTS_DIR>/users/<uid>/projects, bounded by `within`"
    ),
    ("POST", "/api/match/create-manual"): "hosted ignores project_folder (_resolve_create_target)",
    ("POST", "/api/match/create-from-scoreboard"): "hosted ignores project_folder (_resolve_create_target)",
    ("POST", "/api/me/recent-projects/bind"): (
        "hosted treats path as a key into the caller's own picker rows and never touches the disk"
    ),
    ("POST", "/api/me/recent-projects/delete"): (
        "hosted treats path as a key into the caller's own picker rows; the cascade deletes storage and "
        "rows only, never a folder"
    ),
    ("POST", "/api/me/raw/upload"): "uploaded file name goes through _sanitize_raw_filename",
    ("POST", "/api/match/branding/event-logo"): (
        "the upload's bytes are sniffed (PNG / JPEG / WEBP) and stored under a content-derived name in the "
        "bound match's identity/ folder; the client's filename is never read"
    ),
    ("POST", "/api/shooters/{slug}/identity/logo"): (
        "the upload's bytes are sniffed (PNG / JPEG / WEBP) and stored under a content-derived name; "
        "the client's filename is never read"
    ),
    ("POST", "/api/me/profile/brand-logo"): (
        "the account's own store (an empty, write-refusing one hosted until it has a file store); the bytes "
        "are sniffed and stored under a content-derived name, the client's filename is never read"
    ),
    ("POST", "/api/me/shooter-book/{shooter_id}/logo"): (
        "the account's own book (an empty, write-refusing one hosted until it has a file store); the bytes "
        "are sniffed and stored under a content-derived name, the client's filename is never read"
    ),
    ("POST", "/api/me/raw/upload/multipart/create"): _RAW_KEY,
    ("POST", "/api/me/raw/upload/multipart/part-url"): _RAW_KEY,
    ("POST", "/api/me/raw/upload/multipart/complete"): _RAW_KEY,
    ("POST", "/api/me/raw/upload/multipart/abort"): _RAW_KEY,
    ("DELETE", "/api/me/raw/{filename:path}"): _RAW_KEY,
    ("POST", "/api/shooters/{slug}/raw-videos/attach"): _RAW_KEY,
    ("PATCH", "/api/shooters/{slug}/raw-videos/coverage"): "filename selects a registered raw video",
    ("POST", "/api/shooters/{slug}/raw-videos/repair"): "filename selects a registered raw video",
    ("GET", "/api/shooters/{slug}/raw-videos/overview"): "filename selects a registered raw video",
    ("GET", "/api/shooters/{slug}/raw-videos/peaks"): _RAW_KEY,
    ("POST", "/api/shooters/{slug}/videos/suggest-coverage"): "path is only read in local mode",
    ("GET", "/api/shooters/{slug}/videos/stream"): _STREAM,
    ("GET", "/api/match/shooters/{slug}/videos/stream"): _STREAM,
    ("POST", "/api/shooters/{slug}/videos/remove"): _REGISTERED,
    ("POST", "/api/shooters/{slug}/assignments/move"): _REGISTERED,
    ("POST", "/api/shooters/{slug}/assignments/swap-primary"): _REGISTERED,
    ("POST", "/api/match/videos/move-shooter"): _REGISTERED,
    ("GET", "/api/shooters/{slug}/exports/file/{filename:path}"): _EXPORTS,
    ("GET", "/api/match/exports/file/{filename:path}"): (
        "filename is resolved inside the match's exports dir and 400s outside it"
    ),
    ("POST", "/api/shooters/{slug}/exports/youtube-upload"): "confine_export_filename keeps it in exports/",
    ("POST", "/api/shooters/{slug}/export/match"): "hosted refuses intro_path / outro_path (400)",
    ("GET", "/api/looks/{name}/preview/{file}"): (
        "name must be an installed Look and file a bare <slot>-<variant>.png|webp inside that Look's "
        "preview/ directory; anything else is 404 (looks_api)"
    ),
}


def _hosted() -> bool:
    from .server import _hosted_mode_active

    return _hosted_mode_active()


def _guard(inner: Callable[..., Any]) -> Callable[..., Any]:
    async def guarded(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and _hosted():
            await JSONResponse(status_code=404, content={"detail": "not found"})(scope, receive, send)
            return
        await inner(scope, receive, send)

    guarded._local_only_guard = True  # type: ignore[attr-defined]
    return guarded


def enforce_local_only(app: FastAPI) -> None:
    """Wrap every route in :data:`LOCAL_ONLY_ROUTES` with the hosted 404.

    Runs once, after every router is included. The wrapper sits on the
    route's own ASGI app, so it applies after the auth gate and the match
    alias have run and before any request body is read.
    """
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if any((method, route.path) in LOCAL_ONLY_ROUTES for method in route.methods):
            if not getattr(route.app, "_local_only_guard", False):
                route.app = _guard(route.app)


_STORED_DOC = (
    "stored as a state doc; a project doc's paths are never resolved hosted "
    "(MatchProject.confine_paths on every store-backed load)"
)
_CAMERAS = "maps a shooter to a camera choice, resolved against that shooter's registered videos only"

#: Routes whose body (or a field of it) is typed dict / Any: there are no
#: field names to check, so each is classified by hand with the reason its
#: content never names a file the server reads or writes.
UNTYPED_BODY_ROUTES: dict[tuple[str, str], str] = {
    ("PUT", "/api/sync/matches/{match_id}/docs/match"): _STORED_DOC,
    ("PUT", "/api/sync/matches/{match_id}/docs/project/{slug}"): _STORED_DOC,
    ("PUT", "/api/sync/matches/{match_id}/docs/audit/{slug}/{stage_number}"): _STORED_DOC,
    (
        "PUT",
        "/api/shooters/{slug}/stages/{stage_number}/audit",
    ): "audit doc (shots and events), stored, no paths",
    ("PATCH", "/api/match/shares/{share_id}/cameras"): _CAMERAS,
    ("POST", "/api/match/compare-export"): _CAMERAS,
    ("POST", "/api/shooters/{slug}/scoreboard/import"): "scoreboard JSON, parsed into scoreboard models",
    ("POST", "/api/shooters/{slug}/scoreboard/upload"): "scoreboard JSON, parsed into scoreboard models",
    ("POST", "/api/match/desktop-commands"): (
        "stored for the caller's own desktop to claim; the kind is checked against COMMAND_KINDS"
    ),
    ("POST", "/api/sync/commands/{command_id}/complete"): "a finished command's result, stored for display",
    ("POST", "/api/workers/register"): "worker metadata, stored; the route also needs a worker token",
    ("POST", "/api/shooters/{slug}/export-preview"): (
        "the draft is a StoredLookBody (colour triples and card styles, validated per field) written as "
        "look.json into a temporary folder beside copies of the saved Look's own templates; hosted Looks "
        "own none, so no account-supplied file is read or run"
    ),
    ("PUT", "/api/looks/{name}"): (
        "colour triples and card styles, validated per field (look_store.StoredLookBody); hosted, a "
        "user_looks row materialized as look.json under the account's cache folder, the name checked "
        "against LOOK_NAME_RE before any path is formed"
    ),
}
