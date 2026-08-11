"""Hosted-only ``/api/sync/*`` routes: desktop match adopt + state-doc
mirror upserts (desktop-to-hosted sync MVP, doc 2026-08-07, #631).

A desktop client (Tasks 5+) authenticates with a bearer token (Task 2/3's
``DesktopTokenAuth``, which resolves to a normal tenant exactly like a
session) and pushes one match as a read-only mirror: it adopts (or
re-adopts, idempotently) the match row via ``POST /matches``, then
upserts the match doc, each shooter's project doc, and each stage's
audit doc via the three ``PUT .../docs/...`` routes below, and pushes
trimmed clip / audit media direct to object storage via the presigned
multipart ``.../media/...`` routes (Task 5). This module does not run
detection - the doc routes write rows through
``PostgresMatchStore``/``ProjectStateStore``, the same stores the hosted
UI itself reads, and the media routes only mint/consume presigned URLs
against ``state.storage``; no media bytes pass through this process.

A mirrored row's ``origin`` is "desktop" for as long as it lives; a
sync push can never touch a natively-created hosted match (``origin ==
"hosted"``) - every doc route 409s ``not_a_mirror`` against one, and the
create route 409s ``match_exists_hosted`` rather than silently adopting
it. This is a one-way push: the hosted side never writes back to the
desktop client.

Local mode (no ``SPLITSMITH_MODE=hosted``) has no meaning for a sync
push - a local install has no durable per-user storage to mirror into -
so every route 404s there, same guard idiom as the desktop-token
management routes and share links.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .. import match_model
from ..match_project import MatchProject
from ..storage import Storage

if TYPE_CHECKING:
    from ..db.matches import PostgresMatchStore
    from ..db.project_state import ProjectStateStore
    from ..db.recent_projects import PostgresRecentProjectsStore

router = APIRouter(prefix="/api/sync")

# Containment boundary for the media presign routes below: a desktop
# mirror push may only touch its own match's trimmed clip / audit
# artifacts, never anything else in the tenant's bucket. The client-
# supplied key is tenant-relative - the storage layer applies the
# users/<uid>/ prefix, never the client - so this regex plus the
# match_id equality check in _validate_media_key is the entire guard;
# there is no filesystem boundary to fall back on the way local mode has.
#
# Per-subdir extension sets (#821): trimmed/ holds .mp4 clips plus their
# .params.json sidecars; beep_review/ holds .m4a snippets plus their
# .peaks.json. The cross-product (trimmed/*.m4a, beep_review/*.mp4) is
# not a thing the desktop push ever writes, so the gate rejects it.
_SYNC_MEDIA_KEY_RE = re.compile(
    r"^matches/(?P<match_id>[A-Za-z0-9._-]+)/shooters/[A-Za-z0-9_-]+/"
    r"(?:trimmed/[A-Za-z0-9._-]+\.(?:mp4|json)"
    r"|beep_review/[A-Za-z0-9._-]+\.(?:m4a|json))$"
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SyncMatchCreate(BaseModel):
    """Body for ``POST /api/sync/matches``."""

    match_id: str
    name: str


class SyncMatchCreateResponse(BaseModel):
    """Response for ``POST /api/sync/matches``."""

    match_id: str
    origin: str


class SyncDocVersionResponse(BaseModel):
    """Response shared by all three doc-upsert routes."""

    version: int


class SyncDocMeta(BaseModel):
    """One manifest row: doc identity + version."""

    doc_kind: str
    slug: str | None = None
    stage_number: int | None = None
    version: int
    updated_at: datetime


class SyncDocManifestResponse(BaseModel):
    """Response for ``GET /api/sync/matches/{match_id}/docs``."""

    docs: list[SyncDocMeta]


class SyncDocResponse(BaseModel):
    """Response for the three per-doc GET routes."""

    doc: dict[str, Any]
    version: int


class SyncMediaCreate(BaseModel):
    """Body for ``POST /api/sync/matches/{match_id}/media/create``."""

    key: str


class SyncMediaCreateResponse(BaseModel):
    """Response for ``POST /api/sync/matches/{match_id}/media/create``."""

    upload_id: str
    key: str
    part_size: int


class SyncMediaPartUrl(BaseModel):
    """Body for ``POST /api/sync/matches/{match_id}/media/part-url``."""

    key: str
    upload_id: str
    part_number: int


class SyncMediaPartUrlResponse(BaseModel):
    """Response for ``POST /api/sync/matches/{match_id}/media/part-url``."""

    url: str


class SyncMediaPart(BaseModel):
    """One finished part: its 1-based number + the ETag storage returned."""

    part_number: int
    etag: str


class SyncMediaComplete(BaseModel):
    """Body for ``POST /api/sync/matches/{match_id}/media/complete``."""

    key: str
    upload_id: str
    parts: list[SyncMediaPart]


class SyncMediaCompleteResponse(BaseModel):
    """Response for ``POST /api/sync/matches/{match_id}/media/complete``."""

    size: int


class SyncMediaAbort(BaseModel):
    """Body for ``POST /api/sync/matches/{match_id}/media/abort``."""

    key: str
    upload_id: str


class SyncMediaAbortResponse(BaseModel):
    """Empty response for ``POST /api/sync/matches/{match_id}/media/abort``."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode.

    Imported lazily so that importing this module never requires the
    hosted-only db deps (sqlalchemy et al) - the local-slim install
    still imports and registers this router; every route just 404s at
    request time. Same idiom as the desktop-token management routes.
    """
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _current_user(request: Request) -> Any:
    """Read the user the outer ``_auth_gate`` middleware already resolved.

    Every ``/api/sync/*`` route sits behind that gate (it matches on the
    ``/api/`` prefix), so an unauthenticated request never reaches this
    dependency - the 401 already happened there. Kept as an explicit
    dependency for parity with the rest of the ``/api/me/*`` surface.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def _matches_store(request: Request) -> PostgresMatchStore:
    state = request.app.state.splitsmith_state
    store = state.matches_store
    if store is None:
        raise HTTPException(status_code=500, detail="match store unavailable")
    return store


def _project_state(request: Request) -> ProjectStateStore:
    state = request.app.state.splitsmith_state
    store = state.project_state
    if store is None:
        raise HTTPException(status_code=500, detail="project state store unavailable")
    return store


def _recent_projects_store(request: Request) -> PostgresRecentProjectsStore:
    """The tenant-scoped recents store for the current sync request.

    ``AppState.recent_projects`` types as the ``RecentProjectsStore``
    Protocol (local mode's Json-backed store fits it too), but every
    ``/api/sync/*`` route already ran through ``_hosted_gate()`` -
    hosted mode's ``recent_projects`` always resolves to a
    :class:`PostgresRecentProjectsStore` bound to the sync caller's
    own tenant (``current_tenant`` is pinned by the outer ``_auth_gate``
    from the desktop bearer's resolved user, exactly like a session -
    see ``server.py``). Cast rather than import-and-``isinstance``, so
    this module keeps deferring the hosted-only db import (module
    docstring).
    """
    state = request.app.state.splitsmith_state
    return cast("PostgresRecentProjectsStore", state.recent_projects)


async def _register_recent_project(request: Request, match_id: str, name: str) -> None:
    """Upsert the owner's picker row for a synced match (#794).

    Reuses the exact path shape hosted-native match creation builds
    (``server._resolve_create_target``: ``<SPLITSMITH_PROJECTS_DIR>/
    users/<user_id>/projects/<slug>``) so a synced match's row is
    indistinguishable in shape from one a browser-created match would
    get - one path-building implementation, not a second. Imported
    lazily, same reason as ``_hosted_gate``.
    """
    from .server import _resolve_create_target

    state = request.app.state.splitsmith_state
    path = _resolve_create_target(state, project_folder=None, name=name)
    await _recent_projects_store(request).record_sync_push(match_id, name, path)


async def _resolve_mirror(request: Request, match_id: str) -> Any:
    """Load the match row, enforcing the shared mirror contract.

    404 when the match is unknown to this tenant (also the outcome for a
    match owned by a different user - the two are indistinguishable by
    design, same as every other per-user lookup in this codebase). 409
    ``not_a_mirror`` when the row exists but was never adopted by a sync
    push - a sync can never touch a native hosted match.
    """
    row = await _matches_store(request).get(match_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.origin != "desktop":
        raise HTTPException(status_code=409, detail="not_a_mirror")
    return row


def _require_storage(request: Request) -> Storage:
    """503 when hosted object storage isn't wired.

    Same idiom as server.py's ``create_multipart_upload`` et al.
    (``_require_storage()`` there) - a desktop install with no
    ``SPLITSMITH_S3_BUCKET`` configured refuses cleanly rather than
    hitting an ``AttributeError`` on a ``None`` storage backend.
    """
    state = request.app.state.splitsmith_state
    storage = state.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="media sync is hosted-mode only; storage is not configured",
        )
    return storage


def _validate_media_key(key: str, match_id: str) -> None:
    """422 unless ``key`` is a well-formed trimmed-media path scoped to
    ``match_id``.

    ``_SYNC_MEDIA_KEY_RE`` is the containment boundary (see its
    docstring); this also rejects a syntactically valid key that names
    a *different* match than the one in the URL, so a mirror push for
    match A can never plant an object under match B's prefix.
    """
    # fullmatch, not match: the pattern's $ would tolerate one trailing
    # newline and mint a key with a literal \n in the object name.
    m = _SYNC_MEDIA_KEY_RE.fullmatch(key)
    if m is None or m["match_id"] != match_id:
        raise HTTPException(status_code=422, detail="invalid media key")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/matches", response_model=SyncMatchCreateResponse)
async def create_or_adopt_match(
    body: SyncMatchCreate,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMatchCreateResponse:
    """Adopt ``match_id`` as a desktop mirror, or refresh its name.

    Idempotent: re-posting the same ``match_id`` just updates the name
    (``PostgresMatchStore.upsert`` never changes ``origin`` on an
    existing row). 409 ``match_exists_hosted`` when the row already
    exists as a natively-created hosted match - a sync push must never
    silently reclassify one.

    Also upserts the owner's ``recent_projects`` row (#794) so a match
    that only ever arrived via sync shows up in the hosted picker
    instead of being reachable only by typing its URL by hand.
    """
    _hosted_gate()
    store = _matches_store(request)
    existing = await store.get(body.match_id)
    if existing is not None and existing.origin == "hosted":
        raise HTTPException(status_code=409, detail="match_exists_hosted")
    record = await store.upsert(body.match_id, body.name, f"matches/{body.match_id}", origin="desktop")
    await _register_recent_project(request, body.match_id, body.name)
    return SyncMatchCreateResponse(match_id=record.match_id, origin=record.origin)


@router.get("/matches/{match_id}/docs", response_model=SyncDocManifestResponse)
async def get_doc_manifest(
    match_id: str,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncDocManifestResponse:
    """Identity + version of every state doc in this mirror.

    The pull side of the bidirectional sync: desktop diffs these
    versions against the ones recorded at its last sync and GETs only
    the docs that moved. Doc bodies never ride in the manifest.
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    store = _project_state(request)
    meta = await store.list_doc_meta(match_id)
    return SyncDocManifestResponse(
        docs=[
            SyncDocMeta(
                doc_kind=m.doc_kind,
                slug=m.slug,
                stage_number=m.stage_number,
                version=m.version,
                updated_at=m.updated_at,
            )
            for m in meta
        ]
    )


@router.get("/matches/{match_id}/docs/match", response_model=SyncDocResponse)
async def get_match_doc(
    match_id: str, request: Request, user: Any = Depends(_current_user)
) -> SyncDocResponse:
    """Fetch the match doc + its version. 404 when it does not exist."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_match(match_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)


@router.get("/matches/{match_id}/docs/project/{slug}", response_model=SyncDocResponse)
async def get_project_doc(
    match_id: str, slug: str, request: Request, user: Any = Depends(_current_user)
) -> SyncDocResponse:
    """Fetch one shooter's project doc + version. 404 when absent."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_project(match_id, slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)


@router.get("/matches/{match_id}/docs/audit/{slug}/{stage_number}", response_model=SyncDocResponse)
async def get_audit_doc(
    match_id: str,
    slug: str,
    stage_number: int,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncDocResponse:
    """Fetch one stage's audit doc + version. 404 when absent."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    doc, version = await _project_state(request).load_audit(match_id, slug, stage_number)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return SyncDocResponse(doc=doc, version=version)


@router.put("/matches/{match_id}/docs/match", response_model=SyncDocVersionResponse)
async def put_match_doc(
    match_id: str,
    expected_version: int,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert the match doc at ``expected_version`` (0 = create).

    409 ``version_conflict`` when the row moved on - the desktop client
    re-pulls, re-merges, and retries; the hosted side never resolves the
    race itself (that was the pre-pull ``_mirror_save`` clobber, deleted
    with the bidirectional slice).
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    try:
        match_model.Match.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    store = _project_state(request)
    version = await store.save_match(match_id, body, expected_version=expected_version)
    return SyncDocVersionResponse(version=version)


@router.put("/matches/{match_id}/docs/project/{slug}", response_model=SyncDocVersionResponse)
async def put_project_doc(
    match_id: str,
    slug: str,
    expected_version: int,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert one shooter's project doc at ``expected_version``.

    Same optimistic-lock contract as ``put_match_doc``.
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    try:
        MatchProject.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    store = _project_state(request)
    version = await store.save_project(match_id, slug, body, expected_version=expected_version)
    return SyncDocVersionResponse(version=version)


@router.put("/matches/{match_id}/docs/audit/{slug}/{stage_number}", response_model=SyncDocVersionResponse)
async def put_audit_doc(
    match_id: str,
    slug: str,
    stage_number: int,
    expected_version: int,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: Any = Depends(_current_user),
) -> SyncDocVersionResponse:
    """Upsert one stage's audit doc at ``expected_version``. Schemaless -
    stored as-is, no model. Same optimistic-lock contract as
    ``put_match_doc``.
    """
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    store = _project_state(request)
    version = await store.save_audit(match_id, slug, stage_number, body, expected_version=expected_version)
    return SyncDocVersionResponse(version=version)


# ---------------------------------------------------------------------------
# Media routes: presigned multipart push for trimmed clips / audit media
# ---------------------------------------------------------------------------
#
# Mirrors server.py's ``/api/me/raw/upload/multipart/*`` (#467) but scoped
# to one mirror's ``trimmed/`` media rather than the shared ``raw/`` pool,
# and keyed by the client-supplied (regex-validated) path rather than a
# server-sanitized filename - a desktop client already knows the exact
# tenant-relative key its trimmed output belongs at. No bytes pass through
# this process: the desktop client PUTs parts straight to storage via the
# presigned URLs this mints.


@router.post("/matches/{match_id}/media/create", response_model=SyncMediaCreateResponse)
async def create_media_upload(
    match_id: str,
    body: SyncMediaCreate,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMediaCreateResponse:
    """Begin a presigned multipart upload for one trimmed-media object."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    _validate_media_key(body.key, match_id)
    storage = _require_storage(request)
    from .server import _RAW_UPLOAD_PART_SIZE

    try:
        upload_id = storage.create_multipart_upload(body.key)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 500
        raise HTTPException(status_code=500, detail=f"could not start upload: {exc}") from exc
    return SyncMediaCreateResponse(upload_id=upload_id, key=body.key, part_size=_RAW_UPLOAD_PART_SIZE)


@router.post("/matches/{match_id}/media/part-url", response_model=SyncMediaPartUrlResponse)
async def sign_media_part(
    match_id: str,
    body: SyncMediaPartUrl,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMediaPartUrlResponse:
    """Return a presigned URL the desktop client PUTs one part to."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    _validate_media_key(body.key, match_id)
    if body.part_number < 1:
        raise HTTPException(status_code=422, detail="part_number must be >= 1")
    storage = _require_storage(request)
    try:
        url = storage.presign_upload_part(body.key, body.upload_id, body.part_number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not sign part: {exc}") from exc
    return SyncMediaPartUrlResponse(url=url)


@router.post("/matches/{match_id}/media/complete", response_model=SyncMediaCompleteResponse)
async def complete_media_upload(
    match_id: str,
    body: SyncMediaComplete,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMediaCompleteResponse:
    """Finalize the upload once every part has landed."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    _validate_media_key(body.key, match_id)
    if not body.parts:
        raise HTTPException(status_code=422, detail="parts must not be empty")
    storage = _require_storage(request)
    try:
        size = storage.complete_multipart_upload(
            body.key, body.upload_id, [(p.part_number, p.etag) for p in body.parts]
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not complete upload: {exc}") from exc
    return SyncMediaCompleteResponse(size=size)


@router.post("/matches/{match_id}/media/abort", response_model=SyncMediaAbortResponse)
async def abort_media_upload(
    match_id: str,
    body: SyncMediaAbort,
    request: Request,
    user: Any = Depends(_current_user),
) -> SyncMediaAbortResponse:
    """Discard an in-progress upload (desktop client cancelled or failed)."""
    _hosted_gate()
    await _resolve_mirror(request, match_id)
    _validate_media_key(body.key, match_id)
    storage = _require_storage(request)
    try:
        storage.abort_multipart_upload(body.key, body.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not abort upload: {exc}") from exc
    return SyncMediaAbortResponse()
