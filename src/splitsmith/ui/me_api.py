"""The account-level routes: ``/api/me/...``.

Lifted out of ``server.create_app`` (#680). Handlers read the process state
from ``request.app.state.splitsmith_state``; helpers shared with other route
families are called through the ``server`` module so a test that patches
``server.<helper>`` still reaches them. Those helpers and the request models
move here, or to their own domain modules, as the split continues.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import backup as backup_mod
from .. import match_model, user_config
from ..access import Feature
from ..async_bridge import run_sync
from ..auth import User
from ..display_name import normalize_display_name
from ..match_project import MatchProject, StageStatus
from ..sync.state import load_sync_state
from . import server as _server
from .access_gate import features_of, require_feature
from .context import _hosted_mode_active
from .jobs import Job, JobNotRetryableError
from .match_delete import DeletionSummary, delete_match_cascade
from .server import DesktopTokenInfo, HealthResponse, MultipartPart, RecentProjectDetail
from .state import _RAW_UPLOAD_PART_SIZE, AppState

if TYPE_CHECKING:
    # Hosted-only; a module-level import would break the slim install (no sqlalchemy).
    from ..db.desktop_tokens import DesktopTokenRecord

logger = logging.getLogger(__name__)

router = APIRouter()


class BindRecentProjectRequest(BaseModel):
    """Body for POST /api/user/recent-projects/bind.

    The server binds in-memory only; on next launch the user reopens via
    the same picker. ``name`` is optional -- the server reads the
    on-disk display name from ``project.json`` when available.
    ``create=true`` opts into scaffolding a brand-new project at ``path``
    (mkdir + MatchProject.init); the default of ``false`` keeps the
    existing strict "open only" behaviour so a typo can't silently
    create an empty project under a wrong path.
    """

    path: str
    name: str | None = None
    create: bool = False


class UpdateMeRequest(BaseModel):
    """Body for PATCH /api/me (#867).

    ``display_name`` is required *and* nullable: an explicit ``null``
    clears the name, and a body that omits the field is a 422 rather
    than being read as a clear. With one field on the model the
    distinction would otherwise be invisible.
    """

    display_name: str | None


class DeleteProjectRequest(BaseModel):
    """Body for POST /api/me/recent-projects/delete.

    Replaces the old soft "forget" (which only dropped the picker row and
    orphaned everything else). This is a real teardown of the match and the
    resources it owns. The two opt-in flags are mode-specific and the
    server ignores the one that doesn't apply:

    - ``delete_local_files`` (desktop only): also ``rmtree`` the project
      folder on disk. Ignored in hosted mode (ephemeral container fs).
    - ``delete_raw_uploads`` (hosted only): also delete raw uploads that
      fed this match and no other. Ignored in local mode (no object store).
    """

    path: str
    delete_local_files: bool = False
    delete_raw_uploads: bool = False


class DeletionSummaryModel(BaseModel):
    """Serialisable view of :class:`~splitsmith.ui.match_delete.DeletionSummary`."""

    match_id: str | None
    recent_project_removed: bool
    match_row_removed: bool
    state_docs_removed: int
    comments_removed: int
    desktop_commands_removed: int = 0
    storage_objects_deleted: int
    raw_uploads_deleted: list[str]
    raw_uploads_skipped_shared: list[str]
    jobs_cancelled: int
    local_dir_removed: bool
    errors: list[str]

    @classmethod
    def from_summary(cls, summary: DeletionSummary) -> DeletionSummaryModel:
        return cls(**summary.__dict__)


class MultipartCreateRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/create (#467).

    Only the filename is needed; the server sanitizes it, mints the R2
    multipart upload, and tells the client the part size to chunk with.
    Everything downstream keys off ``filename`` + ``upload_id`` (the
    server re-derives the storage key), so a request body can't point the
    upload outside the user's ``raw/`` prefix.
    """

    filename: str


class MultipartPartUrlRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/part-url (#467)."""

    filename: str
    upload_id: str
    part_number: int


class MultipartCompleteRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/complete (#467)."""

    filename: str
    upload_id: str
    parts: list[MultipartPart]


class MultipartAbortRequest(BaseModel):
    """Body for POST /api/me/raw/upload/multipart/abort (#467)."""

    filename: str
    upload_id: str


def _desktop_token_info(record: DesktopTokenRecord) -> DesktopTokenInfo:
    return DesktopTokenInfo(
        id=record.id,
        name=record.name,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        revoked_at=record.revoked_at,
    )


class DesktopTokenListResponse(BaseModel):
    """Response body for GET /api/me/desktop-tokens (#631)."""

    tokens: list[DesktopTokenInfo]


class DesktopTokenCreateRequest(BaseModel):
    """Body for POST /api/me/desktop-tokens (#631)."""

    name: str


class DesktopTokenCreateResponse(BaseModel):
    """Response body for POST /api/me/desktop-tokens (#631).

    ``token`` is the raw bearer value - it appears here and only here;
    every other response (including this token's own future GET/DELETE
    entries) carries just the ``DesktopTokenInfo`` record."""

    token: str
    record: DesktopTokenInfo


class DesktopTokenRevokeResponse(BaseModel):
    """Response body for DELETE /api/me/desktop-tokens/{token_id} (#631).

    ``revoked`` is False for both an unknown id and one owned by another
    user - the two cases are indistinguishable by design, same as the
    share-token store's revoke()."""

    revoked: bool


class ScoreboardIdentityRequest(BaseModel):
    """Body for PUT /api/user/scoreboard-identity (#75).

    Mirrors :class:`splitsmith.user_config.ScoreboardIdentity`. ``shooter_id``
    is required; everything else is optional so the SPA can save a partial
    identity without forcing the user to fill division / club.
    """

    shooter_id: int
    display_name: str | None = None
    division: str | None = None
    club: str | None = None
    base_url: str | None = None


def _enrich_recent_project(rp: user_config.RecentProject) -> RecentProjectDetail:
    """Read on-disk metadata for one recent-project entry.

    Returns a :class:`RecentProjectDetail` that the redesigned match
    picker (#322) renders directly. Best-effort: anything we can't
    answer leaves the corresponding field at its default. Errors never
    propagate -- a stale entry shows up as ``kind="missing"`` so the
    user can prune it.
    """
    detail = RecentProjectDetail(
        path=rp.path,
        name=rp.name,
        last_opened_at=rp.last_opened_at,
        kind="unknown",
    )
    path = Path(rp.path)
    if not path.exists():
        detail.kind = "missing"
        return detail
    detail.synced = load_sync_state(path).last_synced_at is not None

    try:
        kind, _ = match_model.from_path(path)
    except FileNotFoundError:
        detail.kind = "unknown"
        return detail

    try:
        if kind == "match":
            match = match_model.Match.load(path)
            detail.kind = "match"
            detail.name = match.name or rp.name
            detail.match_id = match.match_id
            detail.shooter_count = len(match.shooters)
            detail.stage_count = len(match.stages)
            detail.match_date = match.match_date.isoformat() if match.match_date else None
            detail.manual = match.scoreboard_match_id is None
            # Audited = average audited-stage count across shooters.
            # "Audited" comes from :func:`stage_audit_status` -- the
            # operator has to hit Save & next at least once on the
            # stage for it to count. Skipped stages don't qualify.
            video_total = 0
            shooter_names: list[str] = []
            per_shooter: list[tuple[str, dict[int, StageStatus]]] = []
            untimed: list[str] = []
            stage_names = {s.stage_number: s.stage_name for s in match.stages}
            for slug in match.shooters:
                shooter_root = match_model.Match.shooter_root(path, slug)
                try:
                    shooter = match.load_shooter(path, slug)
                except (FileNotFoundError, KeyError):
                    shooter_names.append(slug)
                    continue
                try:
                    legacy = MatchProject.load(shooter_root)
                except FileNotFoundError:
                    shooter_names.append(shooter.name or slug)
                    continue
                statuses = legacy.stage_statuses(audit_docs=legacy.audit_docs_from_disk(shooter_root))
                per_shooter.append((slug, statuses))
                if any(st.time_seconds <= 0 and not st.skipped for st in legacy.stages):
                    untimed.append(slug)
                for st_entry in legacy.stages:
                    stage_names.setdefault(st_entry.stage_number, st_entry.stage_name)
                video_total += len(legacy.all_videos())
                shooter_names.append(shooter.name or slug)
            detail.shooter_names = shooter_names
            detail.stages_audited = _server._stages_audited_across(per_shooter)
            detail.video_count = video_total
            detail.next_step = _server._next_step_from_statuses(
                per_shooter, stage_names, video_total, untimed
            )
            metadata_path = path / match_model.MATCH_FILE
        else:
            # Path exists but is not a Match folder -- a pre-Tier-1
            # legacy single-shooter project, or a stale entry. Leave
            # ``kind="unknown"`` and skip the metadata block so the
            # user can prune it from the picker.
            return detail

        if metadata_path.exists():
            mtime = metadata_path.stat().st_mtime
            detail.last_modified_at = datetime.fromtimestamp(mtime, tz=UTC)
    except Exception:  # noqa: BLE001 -- defensive: never break listing
        return detail

    # Derive status. "exported" = every stage audited (heuristic);
    # "archived" = no modification in 180 days;
    # "awaiting_footage" = the operator hasn't attached any footage yet
    # (drives the picker pill + footage-gated menu treatment per #425);
    # "in_progress" otherwise.
    if detail.kind == "match" and _server._nothing_left_to_do(detail.next_step):
        detail.status = "exported"
    elif detail.last_modified_at is not None and (datetime.now(UTC) - detail.last_modified_at).days > 180:
        detail.status = "archived"
    elif detail.kind == "match" and detail.video_count == 0:
        detail.status = "awaiting_footage"
    else:
        detail.status = "in_progress"
    return detail


async def _hosted_picker_details(
    state: AppState, projects: list[user_config.RecentProject]
) -> list[RecentProjectDetail | None]:
    """Hosted picker detail for every recent row, in two queries (#1179).

    The recorded ``path`` is an ephemeral container working root a redeploy
    wipes, so the filesystem enricher reports the match as ``missing`` even
    though its state is safe in ``state_docs``. This loads every match's
    match / project / audit docs in one query
    (:meth:`ProjectStateStore.load_docs_for_matches`) and the recent ids'
    match rows in one more (:meth:`PostgresMatchStore.get_many`), then
    derives each card with :func:`_hosted_detail_from_docs`. It used to be
    3 + shooters round trips per match, in sequence -- 8.9 s for one picker
    load on production.

    Returns one entry per row of ``projects``, in order: the card, or
    ``None`` where the row has no stored ``match_id`` or its match doc is
    gone, which is the caller's cue to use the filesystem enricher. Per row,
    not per match: ``recent_projects`` is unique on path, so two rows can
    name one match and each card must carry its own path and timestamp.
    All ``None`` in local mode (no state store).
    """
    if state.project_state is None:
        return [None] * len(projects)
    match_ids = [rp.match_id for rp in projects if rp.match_id]
    if not match_ids:
        return [None] * len(projects)
    docs = await state.project_state.load_docs_for_matches(match_ids)
    # ``matches_store`` is the tenant's authoritative registry of hosted vs.
    # mirrored ownership (#631 Task 6); a match doc with no row (which
    # shouldn't happen for a real hosted match) reads as "hosted" rather
    # than being mislabelled a mirror. With no registry at all the card
    # keeps its default origin, as the per-match path did.
    origins: dict[str, str] | None = None
    if state.matches_store is not None:
        origins = {row.match_id: row.origin for row in await state.matches_store.get_many(match_ids)}
    details: list[RecentProjectDetail | None] = []
    for rp in projects:
        bucket = docs[rp.match_id] if rp.match_id else None
        if bucket is None or bucket.match is None:
            details.append(None)
            continue
        details.append(
            _server._hosted_detail_from_docs(
                rp,
                bucket.match,
                origin=None if origins is None else origins.get(rp.match_id, "hosted"),
                project_docs=bucket.projects,
                audit_docs=bucket.audits,
            )
        )
    return details


class _HashingReader:
    """Wrap a binary stream so we hash the bytes as they're consumed.

    boto3's ``upload_fileobj`` reads from this in chunks; we forward
    the chunks to the inner hash object so the server can return a
    sha256 without a second pass. Pairs with ``storage.upload_stream``.
    """

    def __init__(self, inner: BinaryIO, digest: Any) -> None:
        self._inner = inner
        self._digest = digest

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        if chunk:
            self._digest.update(chunk)
        return chunk


@router.post("/api/me/projects/import", dependencies=[Depends(require_feature(Feature.create_match))])
async def import_project_endpoint(
    request: Request,
    archive: UploadFile = File(...),
    dest_root: str = Form(...),
    overwrite: bool = Form(False),
    bind: bool = Form(False),
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Restore an archive produced by ``GET /api/project/export``.

    Extracts under ``dest_root`` in local mode. Hosted mode ignores
    ``dest_root`` and extracts under the caller's own projects prefix
    (the same one ``create-manual`` uses), and the import -- including
    an ``overwrite`` replacing an existing folder -- may only ever touch
    a path inside it. When ``bind`` is true, the newly imported project
    is bound and added to the recent-projects list so the SPA can
    navigate straight into it.
    """
    state = request.app.state.splitsmith_state
    within: Path | None = None
    if _hosted_mode_active():
        within = _server._hosted_projects_root(str(user.id))
        dest = within
    else:
        dest = Path(dest_root).expanduser()
    tmp = Path(tempfile.mkdtemp(prefix="splitsmith-import-"))
    staged = tmp / "upload.tar.gz"
    try:
        with staged.open("wb") as out:
            while True:
                chunk = await archive.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        try:
            result = backup_mod.import_project(staged, dest, overwrite=overwrite, within=within)
        except backup_mod.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if bind:
        await _server._register_match_at(state, result.project_root, fallback_name=result.project_name)

    return JSONResponse(
        {
            "project_root": str(result.project_root),
            "project_name": result.project_name,
            "manifest": result.manifest,
        }
    )


@router.post("/api/me/raw/upload", dependencies=[Depends(require_feature(Feature.raw_upload))])
async def upload_raw_video(
    request: Request,
    file: UploadFile = File(...),
    x_content_sha256: str | None = Header(default=None),
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Stream a raw video into hosted-mode object storage.

    Hosted-only: returns 503 when ``state.storage`` is unwired,
    which is the local-mode default. The desktop ``splitsmith ui``
    flow writes raw videos to disk via ``raw/<name>`` symlinks and
    never calls this endpoint -- the existing path-based codepaths
    are unchanged.

    Robustness model (doc 05 calls the full tus answer out as the
    v2 goal; this is the v1 idempotent single-shot):

    - The write is atomic. S3 PUT (or boto3's multipart) only
      publishes the object on completion; a connection drop
      leaves no torn object visible. Aborted multiparts get
      swept by R2's lifecycle rule.
    - Re-uploads are safe. A successful retry overwrites the
      previous object atomically; clients that hit a transient
      error can just POST the same file again.
    - The server computes sha256 during streaming and returns it
      in the response so the client can detect transit corruption
      end-to-end. If the client knows the hash up front it can
      send ``X-Content-SHA256`` -- mismatch rolls the upload back
      (deletes the just-written object) and returns 422 so the
      retry path is "POST again with the corrected bytes".

    Resume-from-byte-N (tus) is deliberately not in this PR; see
    ``docs/saas-readiness/05-uploads-and-streaming.md``.
    """
    state = request.app.state.splitsmith_state
    storage = state.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "raw video upload is hosted-mode only; "
                "local mode writes raw/<name> symlinks via the desktop UI"
            ),
        )

    name = _server._sanitize_raw_filename(file.filename)
    key = f"raw/{name}"

    # Wrap the spooled body so we count bytes + hash in one pass
    # while boto3 streams to S3. UploadFile.file is the underlying
    # sync SpooledTemporaryFile that boto3 can ``.read()`` from
    # directly -- starlette has already spooled the multipart body
    # by the time this handler runs.
    source: BinaryIO = file.file
    source.seek(0)
    digest = hashlib.sha256()
    hashing = _HashingReader(source, digest)

    try:
        size = storage.upload_stream(key, hashing)
    except Exception as exc:
        # boto3's TransferManager aborts the multipart on its
        # own; re-raise as a clean 500 so the client sees a
        # retryable failure instead of a half-typed exception.
        raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc

    sha256_hex = digest.hexdigest()

    if x_content_sha256 and x_content_sha256.lower() != sha256_hex:
        # The bytes that hit S3 don't match what the client said
        # they were sending. Roll the object back so a subsequent
        # GET doesn't serve corrupted content.
        try:
            storage.delete(key)
        except Exception:
            # Best-effort: if delete fails, R2's lifecycle still
            # has the object; surfacing the original mismatch is
            # more useful than the cleanup failure.
            pass
        raise HTTPException(
            status_code=422,
            detail={
                "error": "sha256_mismatch",
                "expected": x_content_sha256,
                "actual": sha256_hex,
            },
        )

    # Kick off preview transcoding so the Ingest UI can fast-scrub the
    # upload without pulling the full-res source. Best-effort: never
    # fails the completed upload (see ``_dispatch_proxy_job``).
    await _server._dispatch_proxy_job(state, key)

    return JSONResponse(
        {
            "path": key,
            "size": size,
            "sha256": sha256_hex,
            "filename": name,
        }
    )


@router.post(
    "/api/me/raw/upload/multipart/create", dependencies=[Depends(require_feature(Feature.raw_upload))]
)
def create_multipart_upload(
    request: Request,
    req: MultipartCreateRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Begin a multipart upload; return the upload id + chunk size.

    The client splits the file into ``part_size``-byte parts, asks
    ``/part-url`` for a presigned URL per part, PUTs each straight to
    R2, then calls ``/complete`` with the parts' ETags.
    """
    state = request.app.state.splitsmith_state
    storage = _server._require_storage(state)
    name = _server._sanitize_raw_filename(req.filename)
    key = f"raw/{name}"
    try:
        upload_id = storage.create_multipart_upload(key)
    except Exception as exc:  # noqa: BLE001 -- surface as a clean 500
        raise HTTPException(status_code=500, detail=f"could not start upload: {exc}") from exc
    return JSONResponse(
        {
            "upload_id": upload_id,
            "filename": name,
            "key": key,
            "part_size": _RAW_UPLOAD_PART_SIZE,
        }
    )


@router.post(
    "/api/me/raw/upload/multipart/part-url", dependencies=[Depends(require_feature(Feature.raw_upload))]
)
def sign_multipart_part(
    request: Request,
    req: MultipartPartUrlRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Return a presigned URL the client PUTs one part to."""
    state = request.app.state.splitsmith_state
    storage = _server._require_storage(state)
    if req.part_number < 1:
        raise HTTPException(status_code=422, detail="part_number must be >= 1")
    key = f"raw/{_server._sanitize_raw_filename(req.filename)}"
    try:
        url = storage.presign_upload_part(key, req.upload_id, req.part_number)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not sign part: {exc}") from exc
    return JSONResponse({"url": url})


@router.post(
    "/api/me/raw/upload/multipart/complete", dependencies=[Depends(require_feature(Feature.raw_upload))]
)
def complete_multipart_upload(
    request: Request,
    req: MultipartCompleteRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Finalize the upload. Returns the same shape the single-shot
    endpoint echoes (minus ``sha256``, which serve never computed)."""
    state = request.app.state.splitsmith_state
    storage = _server._require_storage(state)
    if not req.parts:
        raise HTTPException(status_code=422, detail="parts must not be empty")
    name = _server._sanitize_raw_filename(req.filename)
    key = f"raw/{name}"
    try:
        size = storage.complete_multipart_upload(
            key, req.upload_id, [(p.part_number, p.etag) for p in req.parts]
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not complete upload: {exc}") from exc
    # Same preview kickoff as the single-shot path. This handler is sync
    # (runs on Starlette's threadpool with no event loop), so bridge to
    # the async dispatch via ``run_sync``.
    run_sync(_server._dispatch_proxy_job(state, key))
    return JSONResponse({"path": key, "size": size, "sha256": None, "filename": name})


@router.post(
    "/api/me/raw/upload/multipart/abort", dependencies=[Depends(require_feature(Feature.raw_upload))]
)
def abort_multipart_upload(
    request: Request,
    req: MultipartAbortRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Discard an in-progress upload (client cancelled / failed)."""
    state = request.app.state.splitsmith_state
    storage = _server._require_storage(state)
    key = f"raw/{_server._sanitize_raw_filename(req.filename)}"
    try:
        storage.abort_multipart_upload(key, req.upload_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"could not abort upload: {exc}") from exc
    return JSONResponse({"ok": True})


@router.get("/api/me/raw/list")
def list_raw_uploads(request: Request, user: User = Depends(_server.get_current_user)) -> JSONResponse:
    """List every object the operator has uploaded under their
    ``raw/`` prefix.

    Hosted-only -- 503 in local mode (no storage backend wired;
    local users put videos on disk via the desktop ingest flow).

    Returned shape mirrors what ``POST /api/me/raw/upload`` echoes
    back so the SPA can render an "uploaded files" surface without
    a second lookup:

    - ``filename`` -- the bare leaf name, what the operator picked
      on disk (``_sanitize_raw_filename`` already stripped any
      directory parts at upload time).
    - ``path`` -- the storage key relative to the user's prefix
      (e.g. ``raw/clip.mp4``). The SPA echoes this back to any
      register-into-project endpoint we add later.
    - ``size`` -- bytes.
    - ``last_modified`` -- ISO-8601 UTC, what S3 / MinIO reports
      as the object's mtime. ``null`` when the backend doesn't
      surface one (some test doubles).
    - ``etag`` -- S3 etag when present (useful for the SPA to
      detect a re-upload of the same filename).

    Sorted newest-first so the picker surfaces the most recent
    upload at the top without the SPA having to re-sort.
    """
    state = request.app.state.splitsmith_state
    storage = state.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "raw video list is hosted-mode only; "
                "local mode keeps videos on disk under the project root"
            ),
        )
    # Label each already-claimed object with its owning shooter, so the
    # ingest "available uploads" list can hide/grey what a shooter in
    # this match already owns (#562). First claimant wins, so the label
    # is stable; the attach guard reads the same map.
    owners = _server._raw_video_owners(state)
    attached_by = {path: slugs[0] for path, slugs in owners.items() if slugs}

    entries: list[dict[str, Any]] = []
    for obj in storage.list("raw/"):
        entries.append(
            {
                "filename": obj.path.split("/", 1)[1] if "/" in obj.path else obj.path,
                "path": obj.path,
                "size": obj.size,
                "last_modified": (obj.last_modified.isoformat() if obj.last_modified is not None else None),
                "etag": obj.etag,
                "attached_to": attached_by.get(obj.path),
            }
        )
    # Newest first. ``last_modified`` is None on backends that don't
    # report one; those entries sort last (stable secondary key by
    # filename so the SPA gets a deterministic order).
    entries.sort(
        key=lambda e: (
            e["last_modified"] is not None,
            e["last_modified"] or "",
            e["filename"],
        ),
        reverse=True,
    )
    return JSONResponse({"uploads": entries})


@router.delete("/api/me/raw/{filename:path}")
def delete_raw_upload(
    request: Request, filename: str, user: User = Depends(_server.get_current_user)
) -> JSONResponse:
    """Remove one uploaded file from object storage.

    Hosted-only (503 in local mode). Idempotent -- deleting an
    already-gone object returns 200 with ``{"ok": true}`` so the
    SPA can retry without special-casing.

    The ``filename`` segment is run through ``_sanitize_raw_filename``
    so a malicious caller can't traverse out of their ``raw/`` prefix
    (the underlying ``S3Storage._key`` also rejects ``..`` but we
    belt-and-braces it at the route layer too).
    """
    state = request.app.state.splitsmith_state
    storage = state.storage
    if storage is None:
        raise HTTPException(
            status_code=503,
            detail="raw video delete is hosted-mode only",
        )
    from ..proxy import proxy_key_for

    name = _server._sanitize_raw_filename(filename)
    key = f"raw/{name}"
    try:
        storage.delete(key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc
    # Best-effort cleanup of the corresponding proxy object.
    try:
        storage.delete(proxy_key_for(key))
    except Exception:
        logger.exception("failed to delete proxy for %s", name)
    return JSONResponse({"ok": True, "path": key})


@router.get("/api/me", response_model=User)
def get_me(request: Request, user: User = Depends(_server.get_current_user)) -> User:
    """Return the operator behind this request.

    Local mode always resolves to the ``LoopbackAuth`` sentinel
    user (id ``"local"``); hosted mode will resolve cookies to a
    real database user. The SPA reads this once on mount so the
    same code path works in both modes.

    ``is_admin`` is derived from ``state.admin_emails`` at request
    time; it is never stored on the user record itself.
    """
    state = request.app.state.splitsmith_state
    return user.model_copy(
        update={
            "is_admin": user.email.lower() in state.admin_emails,
            "features": sorted(f.value for f in features_of(request, user)),
        }
    )


@router.patch("/api/me", response_model=User)
async def patch_me(
    request: Request, req: UpdateMeRequest, user: User = Depends(_server.get_current_user)
) -> User:
    """Update the signed-in account's profile. Hosted mode only.

    Local mode 404s: ``LoopbackAuth``'s sentinel user has no
    database row, and the magic-link routes 404 there for the same
    reason. A sync-scoped desktop token never arrives here at all --
    ``_auth_gate`` confines it to ``/api/sync/*``.

    Normalization runs before the store opens a session, so an
    invalid name is a 422 that touches nothing. A blank name stores
    ``None``, which is what keeps #866's fallback invariant true:
    an account with no name publishes a generated handle, never an
    empty string.
    """
    state = request.app.state.splitsmith_state
    store = state.profile
    if store is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        display_name = normalize_display_name(req.display_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_display_name", "message": str(exc)},
        ) from exc
    await store.set_display_name(display_name)
    return user.model_copy(
        update={
            "display_name": display_name,
            "is_admin": user.email.lower() in state.admin_emails,
            "features": sorted(f.value for f in features_of(request, user)),
        }
    )


@router.get("/api/me/jobs", response_model=list[Job])
async def list_jobs(request: Request, user: User = Depends(_server.get_current_user)) -> list[Job]:
    """Snapshot of the retained jobs (active + recently finished).

    This is the SPA's 5-second poll, so it carries only what the poll
    reads (#1182): ``result`` and ``timings`` are nulled on every entry
    -- the keys stay so the wire shape is unchanged -- and a job's result
    is read through ``GET /api/me/jobs/{id}`` (``pollJob``), as the
    Export page already does. Hosted, the backend also bounds the list
    (see ``PostgresJobBackend.list``).
    """
    state = request.app.state.splitsmith_state
    return [job.model_copy(update={"result": None, "timings": None}) for job in await state.jobs.list()]


@router.get("/api/me/jobs/{job_id}", response_model=Job)
async def get_job(request: Request, job_id: str, user: User = Depends(_server.get_current_user)) -> Job:
    """Poll a single job. SPA polls ~1 Hz while a job is active."""
    state = request.app.state.splitsmith_state
    job = await state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.post("/api/me/jobs/acknowledge-failures", response_model=list[Job])
async def acknowledge_all_failures(
    request: Request, user: User = Depends(_server.get_current_user)
) -> list[Job]:
    """Mark every currently-unacknowledged FAILED job as seen (issue #73).

    Used by the JobsPanel "Dismiss all failures" header action. Returns
    the snapshots that actually flipped to acknowledged so the SPA can
    diff against its in-memory list without an extra refetch.
    """
    state = request.app.state.splitsmith_state
    return await state.jobs.acknowledge_all_failures()


@router.post("/api/me/jobs/{job_id}/acknowledge", response_model=Job)
async def acknowledge_job(
    request: Request, job_id: str, user: User = Depends(_server.get_current_user)
) -> Job:
    """Mark a single failed job as seen (issue #73).

    No-op for jobs that aren't failed or are already acknowledged --
    the snapshot is returned unchanged so the SPA can still pin its
    local state to the server response.
    """
    state = request.app.state.splitsmith_state
    job = await state.jobs.acknowledge(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.post("/api/me/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(request: Request, job_id: str, user: User = Depends(_server.get_current_user)) -> Job:
    """Request cooperative cancellation of a running or pending job.

    The registry sets ``cancel_requested=True`` and (for trim jobs)
    terminates the running ffmpeg subprocess. The worker then bails
    out at its next phase boundary, ending the job in
    ``status=cancelled``. Idempotent: cancelling a finished job
    returns the existing snapshot unchanged.
    """
    state = request.app.state.splitsmith_state
    job = await state.jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.post(
    "/api/me/jobs/{job_id}/retry",
    response_model=Job,
    dependencies=[Depends(require_feature(Feature.hosted_compute))],
)
async def retry_job(request: Request, job_id: str, user: User = Depends(_server.get_current_user)) -> Job:
    """Re-enqueue a failed job with its original args; returns the new job.

    409s when the job isn't FAILED, or is FAILED but already
    acknowledged (retried or dismissed) - ``acknowledged`` doubles
    as the atomic claim bit, see :meth:`JobRegistry.retry`. Works on
    the bare path: match context for the resubmit is rebound from the
    job itself (the match it was originally submitted under), not from
    this request's URL - the jobs list is global (cross-match), so a
    retry issued while viewing a different match must not rebind the
    job to that match. See :meth:`JobRegistry.retry` /
    :meth:`PostgresJobBackend.retry`.
    """
    state = request.app.state.splitsmith_state
    try:
        job = await state.jobs.retry(job_id)
    except JobNotRetryableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


@router.get("/api/me/recent-projects")
async def list_recent_projects(
    request: Request,
    detail: bool = Query(False),
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Return the recent-projects list.

    ``detail=false`` (default) returns the raw
    :class:`user_config.RecentProject` entries -- compatible with the
    legacy picker. ``detail=true`` enriches each entry with on-disk
    metadata (kind / shooters / stages / status) for the redesigned
    match picker (#322). The detailed shape is slightly slower; the
    picker route is the only caller that needs it.
    """
    state = request.app.state.splitsmith_state
    projects = await state.recent_projects.list()
    if detail:
        # Hosted: resolve detail from Postgres so a redeploy-wiped
        # working dir doesn't show the match as "missing" -- every
        # match in two queries (#1179). A row the batch has no doc for
        # falls back to the filesystem enricher (local mode, or no
        # stored match_id / a genuinely-gone match).
        hosted = await _hosted_picker_details(state, projects)
        enriched = [
            card if card is not None else _enrich_recent_project(p)
            for p, card in zip(projects, hosted, strict=True)
        ]
        return JSONResponse({"projects": [p.model_dump(mode="json") for p in enriched]})
    return JSONResponse({"projects": [p.model_dump(mode="json") for p in projects]})


@router.post("/api/me/recent-projects/delete")
async def delete_recent_project(
    request: Request,
    req: DeleteProjectRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    """Delete a project/match and clean up every resource it owns.

    Resolves ``match_id`` (and, hosted, the storage prefix) from the
    picker row, then runs the cascade in :mod:`splitsmith.ui.match_delete`.
    Returns the deletion summary plus the refreshed picker list. Always
    200 -- partial failures are reported in ``summary.errors`` so the SPA
    can show "removed X, N errors" rather than a bare 500. (POST, not a
    body-carrying DELETE, so proxies that strip DELETE payloads don't eat
    the opt-in flags.)
    """
    state = request.app.state.splitsmith_state
    resolved = str(Path(req.path).expanduser().resolve())
    entry = next(
        (p for p in await state.recent_projects.list() if p.path == resolved),
        None,
    )
    match_id = entry.match_id if entry is not None else None
    storage_prefix: str | None = None
    if match_id and state.matches_store is not None:
        row = await state.matches_store.get(match_id)
        storage_prefix = row.storage_prefix if row is not None else f"matches/{match_id}"

    summary = await delete_match_cascade(
        state,
        path=req.path,
        match_id=match_id,
        storage_prefix=storage_prefix,
        delete_local_files=req.delete_local_files,
        delete_raw_uploads=req.delete_raw_uploads,
    )
    projects = await state.recent_projects.list()
    return JSONResponse(
        {
            "summary": DeletionSummaryModel.from_summary(summary).model_dump(mode="json"),
            "projects": [p.model_dump(mode="json") for p in projects],
        }
    )


@router.post("/api/me/recent-projects/bind")
async def bind_recent_project(
    request: Request,
    req: BindRecentProjectRequest,
    user: User = Depends(_server.get_current_user),
) -> HealthResponse:
    """Switch the in-memory project. Used by the SPA picker route.

    Accepts a path that already exists on disk (a previously-opened
    project) or a fresh path -- ``MatchProject.init`` is idempotent
    and will scaffold the subdirs if missing. The ``last_opened_at``
    timestamp is bumped so the picker re-orders to the top.
    """
    state = request.app.state.splitsmith_state
    target = Path(req.path).expanduser()
    resolved_str = str(target.resolve())

    # Hosted: resolve the match through Postgres, not the filesystem.
    # The recorded path is an ephemeral container working root that a
    # redeploy wipes, so requiring it to exist (below) 404'd every
    # reopen of an existing match. If the recent-projects row carries a
    # match_id and the tenant still owns it, bind succeeds regardless of
    # local disk -- the alias middleware re-establishes the working root
    # on the next /api/matches/{id}/ request.
    if state.matches_store is not None:
        entry = next(
            (p for p in await state.recent_projects.list() if p.path == resolved_str),
            None,
        )
        if entry is not None and entry.match_id:
            if await state.matches_store.get(entry.match_id) is None:
                # Unknown / not owned -> same 404 an unknown id gets.
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "match_not_found",
                        "message": f"unknown match_id {entry.match_id!r}",
                    },
                )
            name = entry.name or req.name or target.name or "match"
            await state.recent_projects.record_open(
                target, name, kind=entry.kind or "match", match_id=entry.match_id
            )
            return _server._register_response(state, target, name, entry.match_id)
        # Hosted never binds by path: ``path`` is only a key into the
        # caller's own picker rows. Scaffolding or loading a folder the
        # caller names would read and write the shared container's disk.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "project_path_missing",
                "message": f"Project path does not exist: {target}",
            },
        )

    if not target.exists():
        if not req.create:
            # Conservative default: don't silently scaffold a brand-new
            # project on a typo. The "Create new project" flow on the
            # picker route passes ``create=true``.
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "project_path_missing",
                    "message": f"Project path does not exist: {target}",
                },
            )
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        recorded, match_id = await _server._register_match_at(
            state,
            target,
            fallback_name=req.name or target.name or "match",
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _server._register_response(state, target.resolve(), recorded, match_id)


@router.get("/api/me/scoreboard-identity")
async def get_scoreboard_identity(
    request: Request, user: User = Depends(_server.get_current_user)
) -> JSONResponse:
    # "Not pinned yet" is a normal state, not an error -- return a
    # 200 with a null body so the SPA doesn't have to catch a 404
    # on every page load and DevTools doesn't log a failed request.
    state = request.app.state.splitsmith_state
    identity = await state.scoreboard_identity.load()
    if identity is None:
        return JSONResponse(None)
    return JSONResponse(identity.model_dump(mode="json"))


@router.put("/api/me/scoreboard-identity")
async def put_scoreboard_identity(
    request: Request,
    req: ScoreboardIdentityRequest,
    user: User = Depends(_server.get_current_user),
) -> JSONResponse:
    state = request.app.state.splitsmith_state
    identity = user_config.ScoreboardIdentity(
        shooter_id=req.shooter_id,
        display_name=req.display_name,
        division=req.division,
        club=req.club,
        base_url=req.base_url,
    )
    await state.scoreboard_identity.save(identity)
    return JSONResponse(identity.model_dump(mode="json"))


@router.delete("/api/me/scoreboard-identity")
async def delete_scoreboard_identity(
    request: Request, user: User = Depends(_server.get_current_user)
) -> JSONResponse:
    state = request.app.state.splitsmith_state
    await state.scoreboard_identity.clear()
    return JSONResponse({"ok": True})


@router.get("/api/me/desktop-presence")
async def get_desktop_presence(
    request: Request, user: User = Depends(_server.get_current_user)
) -> JSONResponse:
    """Whether a linked desktop is around to pick up a request."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(await _server._desktop_presence(state))


@router.get("/api/me/desktop-tokens", response_model=DesktopTokenListResponse)
async def list_desktop_tokens(
    request: Request,
    user: User = Depends(_server.get_current_user),
) -> DesktopTokenListResponse:
    """List this user's desktop tokens. Never includes the hash or raw value."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    store = state.desktop_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="desktop token store unavailable")
    records = await store.list()
    return DesktopTokenListResponse(tokens=[_desktop_token_info(r) for r in records])


@router.post("/api/me/desktop-tokens", response_model=DesktopTokenCreateResponse, status_code=201)
async def create_desktop_token(
    request: Request,
    req: DesktopTokenCreateRequest,
    user: User = Depends(_server.get_current_user),
) -> DesktopTokenCreateResponse:
    """Mint a new desktop token. The raw value is returned here, once."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    store = state.desktop_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="desktop token store unavailable")
    record, raw = await store.create(req.name)
    return DesktopTokenCreateResponse(token=raw, record=_desktop_token_info(record))


@router.delete("/api/me/desktop-tokens/{token_id}", response_model=DesktopTokenRevokeResponse)
async def revoke_desktop_token(
    request: Request,
    token_id: str,
    user: User = Depends(_server.get_current_user),
) -> DesktopTokenRevokeResponse:
    """Revoke a desktop token. ``revoked`` is False for an unknown id or
    one owned by another user - the two are indistinguishable to the
    caller by design (mirrors the share-token revoke route's ownership
    handling)."""
    state = request.app.state.splitsmith_state
    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")
    store = state.desktop_tokens
    if store is None:
        raise HTTPException(status_code=500, detail="desktop token store unavailable")
    revoked = await store.revoke(token_id)
    return DesktopTokenRevokeResponse(revoked=revoked)
