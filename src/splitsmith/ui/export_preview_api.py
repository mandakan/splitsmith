"""``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15 s3).

Takes the card fields the export body already carries (unknown fields
are ignored, so the SPA can send its mapper output as is) plus ``card``,
``stage_number`` and ``width``, and answers a PNG. A sync ``def`` route:
FastAPI runs it on the threadpool, which is what the sync Playwright
rasterizer needs (the share cards go through ``asyncio.to_thread`` for
the same reason).

Cache under ``runtime().cache_dir / "export-preview"``, keyed by
:func:`export_preview.preview_key`; best-effort. The rasterizer is
launched only on a miss and only for a card with text (``frame`` needs
none). Reads the project and the audit doc; writes nothing to either.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import logo_placeholder
from ..account_profile import brand_digest, load_brand
from ..composition import BrandMark
from ..division import competitor_division
from ..export_preview import (
    PreviewCard,
    PreviewError,
    PreviewSpec,
    audit_digest,
    match_summary_for,
    preview_key,
    render_preview,
    summary_digest,
)
from ..identity import ResolvedIdentity
from ..logo_placeholder import placeholder_logo
from ..look_store import LookStoreError, StoredLookBody, TemplateEdit, draft_look
from ..looks import Look, load_look, look_fingerprint
from ..overlay_hud import OverlayStyleFields
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..runtime import runtime
from ..shooter_book import identity_digest, load_snapshot
from . import render_bound
from .exports_api import installed_look
from .identity_media import ensure_local_event_logo, identity_source, resolved_identity_for

logger = logging.getLogger(__name__)

router = APIRouter()

#: Swapped by tests. Called only on a cache miss, never for ``frame``.
rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]] = ChromiumRasterizer


class ExportPreviewRequest(OverlayStyleFields, BaseModel):
    model_config = ConfigDict(extra="ignore")

    card: PreviewCard
    stage_number: int
    width: int = Field(default=960, ge=160, le=1920)
    title_info: str | None = None
    title_division: bool = True
    #: The export's stage selection, in its order: the match summary card
    #: summarises these, as the video will. ``None`` is every stage.
    stage_numbers: list[int] | None = None
    #: "Made with splitsmith" on the closing card.
    made_with: bool = True
    #: Your account's brand on the title page and the closing card.
    account_brand: bool = True
    head_pad_seconds: float = Field(default=5.0, ge=0)
    tail_pad_seconds: float = Field(default=5.0, ge=0)
    #: The bundle name, as the match export's ``project_name``.
    project_name: str | None = None
    #: The Look and the card's template variant (#1246).
    look: str = "splitsmith"
    variant: str = "default"
    #: The Look editor (#1264): an unsaved draft of ``look`` drawn in its
    #: place, and a time into the template instead of its poster.
    draft: StoredLookBody | None = None
    at: float | None = Field(default=None, ge=0, le=60)
    #: An animated template as a looping WebP of its frames (#1249).
    motion: bool = False
    #: The template editor's unsaved text (#1265); local only.
    templates: list[TemplateEdit] = Field(default_factory=list, max_length=32)
    #: The Look editor's backdrop switch: this stage's footage or the demo scene.
    backdrop: Literal["footage", "demo"] = "footage"
    #: Draw a labelled placeholder in every logo spot no logo fills (the
    #: shooter's, your brand, the event's), so the preview shows where each
    #: goes. Previews only; an export never draws one.
    logo_placeholders: bool = False

    @field_validator("look")
    @classmethod
    def _installed_look(cls, value: str) -> str:
        return installed_look(value)


def _look_fingerprint(name: str) -> str | None:
    """A user (or hosted account) Look's folder fingerprint for the cache
    key; ``None`` for a shipped Look, which changes only with a release."""
    look = load_look(name)
    return None if look.source == "shipped" else look_fingerprint(look.root)


def _with_placeholder(shooter: ResolvedIdentity, placeholder_dir: Path | None) -> ResolvedIdentity:
    """The shooter as the preview draws them: a placeholder in place of a
    logo they have not set, when placeholders were asked for."""
    if placeholder_dir is None or shooter.logo_path is not None:
        return shooter
    return dataclasses.replace(shooter, logo_path=placeholder_logo(logo_placeholder.SHOOTER, placeholder_dir))


def _event_logo(state: Any) -> Path | None:
    """The bound match's event logo on this disk, or ``None``."""
    try:
        match_root = state.match_root
        match = state.match()
    except HTTPException:
        return None
    return ensure_local_event_logo(match.branding, match_root, storage=state.storage, match_id=match.match_id)


def _owner() -> str | None:
    """``<user_id>/<match_id>`` in hosted mode, where one process and one
    cache folder serve every account; ``None`` locally."""
    from .server import _hosted_mode_active, current_match_id, current_tenant

    if not _hosted_mode_active():
        return None
    tenant = current_tenant.get()
    user_id = tenant.user_id if tenant is not None else None
    if not user_id:
        raise HTTPException(status_code=500, detail="hosted mode active but no authenticated tenant is bound")
    return f"{user_id}/{current_match_id.get()}"


class _NoRasterizer:
    """For ``card=frame``: nothing to rasterize, so no browser is launched."""

    def png(self, html: str, *, width: int, height: int) -> bytes:  # pragma: no cover
        raise AssertionError("the frame card never rasterizes")


@router.post("/api/shooters/{slug}/export-preview")
def export_preview(slug: str, req: ExportPreviewRequest, request: Request) -> Response:
    if req.templates:
        from .server import _hosted_mode_active

        if _hosted_mode_active():
            # A template is code; hosted runs none of an account's (desktop only).
            raise HTTPException(status_code=403, detail="template text is previewed on the desktop only")
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    stage_number = req.stage_number
    match_summary = None
    if req.card == "match_summary" and project.stages:
        # The selected stages' audits, as the export reads them; the card sits
        # on the last one's final frame whichever stage the rail has in focus.
        known = {s.stage_number for s in project.stages}
        chosen = (
            [s.stage_number for s in project.stages]
            if req.stage_numbers is None
            else [n for n in req.stage_numbers if n in known]
        )
        if chosen:
            name = req.project_name or project.name
            with tempfile.TemporaryDirectory(prefix="match-summary-") as summary_work:
                match_summary = match_summary_for(
                    project,
                    {n: state.load_audit(slug, n)[0] for n in chosen},
                    title=name,
                    label=project.competitor_name or name,
                    work_dir=Path(summary_work),
                    stage_numbers=chosen,
                )
            stage_number = chosen[-1]
    audit_doc, _audit_version = state.load_audit(slug, stage_number)
    # The event's logo (the branding work), brought to this disk like a
    # shooter's; the title page and the closing card draw it.
    event_logo = _event_logo(state) if req.card in ("title", "closing") else None
    book = load_snapshot(state.shooter_book)
    brand = (
        load_brand(state.account_profile) if req.account_brand and req.card in ("title", "closing") else None
    )
    book_entry = book.get(project.selected_shooter_id) if identity_source(project, book) == "book" else None
    rt = runtime()
    placeholders = req.logo_placeholders and req.card != "frame"
    placeholder_dir = rt.cache_dir / "logo-placeholders"
    if placeholders and req.card in ("title", "closing"):
        if event_logo is None:
            event_logo = placeholder_logo(logo_placeholder.EVENT, placeholder_dir)
        if req.account_brand and (brand is None or brand.logo_path is None):
            brand = BrandMark(
                logo_path=placeholder_logo(logo_placeholder.BRAND, placeholder_dir),
                line=brand.line if brand is not None else None,
            )
    spec = PreviewSpec(
        card=req.card,
        stage_number=stage_number,
        width=req.width,
        title_info=req.title_info,
        title_division=competitor_division(project, root) if req.title_division else None,
        head_pad_seconds=req.head_pad_seconds,
        tail_pad_seconds=req.tail_pad_seconds,
        project_name=req.project_name,
        look=req.look,
        variant=req.variant,
        at=req.at,
        motion=req.motion,
        overlay_variant=req.overlay_variant,
        overlay_options=req.hud_options(),
        backdrop=req.backdrop,
        event_logo=event_logo.name if event_logo is not None else None,
        made_with=req.made_with,
        summary_digest=summary_digest(match_summary) if match_summary is not None else None,
        book_identity=identity_digest(book_entry) if book_entry is not None else None,
        account_brand=brand_digest(brand) if brand is not None else None,
        logo_placeholders=placeholders,
        draft=(
            None
            if req.draft is None and not req.templates
            else hashlib.sha256(
                json.dumps(
                    {
                        "draft": None if req.draft is None else req.draft.model_dump(mode="json"),
                        "templates": [t.model_dump() for t in req.templates],
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        ),
    )
    cache_dir = rt.cache_dir / "export-preview"
    key = preview_key(
        spec,
        slug=slug,
        project_updated_at=project.updated_at.isoformat(),
        audit=audit_digest(audit_doc),
        owner=_owner(),
        look_fingerprint=_look_fingerprint(req.look),
    )
    # A moving preview is a WebP, a still a PNG; the key says which was asked.
    for candidate in (cache_dir / f"{key}.png", cache_dir / f"{key}.webp"):
        if candidate.exists():
            return _image(candidate.read_bytes())
    cached = cache_dir / f"{key}.png"

    def _look(work: Path) -> Look:
        saved = load_look(req.look)
        if req.draft is None and not req.templates:
            return saved
        try:
            return draft_look(saved, req.draft, work / "draft", req.templates)
        except LookStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    def _render(rasterizer: Rasterizer) -> bytes:
        with tempfile.TemporaryDirectory(prefix="export-preview-") as work:
            look = _look(Path(work))
            return render_preview(
                spec,
                project=project,
                root=root,
                audit_doc=audit_doc,
                look=look,
                rasterizer=rasterizer,
                shooter=_with_placeholder(
                    resolved_identity_for(
                        project,
                        root,
                        look=look,
                        index=0,
                        label=project.competitor_name or project.name,
                        book=book,
                    ),
                    placeholder_dir if placeholders else None,
                ),
                ffmpeg_binary=rt.ffmpeg_binary,
                work_dir=Path(work),
                event_logo=event_logo,
                match_summary=match_summary,
                brand=brand,
                book=book,
            )

    try:
        if req.card == "frame":
            png = _render(_NoRasterizer())
        else:
            # The same process-wide render bound as the share cards.
            with render_bound.render_slot(), rasterizer_factory() as rasterizer:
                png = _render(rasterizer)
    except render_bound.RenderBusyError as exc:
        raise HTTPException(
            status_code=429,
            detail="the preview renderer is busy",
            headers={"Retry-After": str(render_bound.RETRY_AFTER_S)},
        ) from exc
    except PreviewError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    except RasterizerUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="the preview needs a browser: Playwright could not launch Chromium"
        ) from exc
    if _is_webp(png):
        cached = cached.with_suffix(".webp")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(png)
    except OSError as exc:
        logger.warning("could not cache the export preview (%s)", exc)
    return _image(png)


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _image(data: bytes) -> Response:
    media = "image/webp" if _is_webp(data) else "image/png"
    return Response(content=data, media_type=media, headers={"Cache-Control": "no-store"})
