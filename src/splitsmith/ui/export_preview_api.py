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

from ..division import competitor_division
from ..export_preview import (
    PreviewCard,
    PreviewError,
    PreviewSpec,
    audit_digest,
    preview_key,
    render_preview,
)
from ..look_store import LookStoreError, StoredLookBody, TemplateEdit, draft_look
from ..looks import Look, load_look, look_fingerprint
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..runtime import runtime
from . import render_bound
from .exports_api import installed_look
from .identity_media import ensure_local_event_logo, resolved_identity_for

logger = logging.getLogger(__name__)

router = APIRouter()

#: Swapped by tests. Called only on a cache miss, never for ``frame``.
rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]] = ChromiumRasterizer


class ExportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    card: PreviewCard
    stage_number: int
    width: int = Field(default=960, ge=160, le=1920)
    title_info: str | None = None
    title_division: bool = True
    #: "Made with splitsmith" on the closing card.
    made_with: bool = True
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

    @field_validator("look")
    @classmethod
    def _installed_look(cls, value: str) -> str:
        return installed_look(value)


def _look_fingerprint(name: str) -> str | None:
    """A user (or hosted account) Look's folder fingerprint for the cache
    key; ``None`` for a shipped Look, which changes only with a release."""
    look = load_look(name)
    return None if look.source == "shipped" else look_fingerprint(look.root)


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
    audit_doc, _audit_version = state.load_audit(slug, req.stage_number)
    # The event's logo (the branding work), brought to this disk like a
    # shooter's; the title page and the closing card draw it.
    event_logo = _event_logo(state) if req.card in ("title", "closing") else None
    spec = PreviewSpec(
        card=req.card,
        stage_number=req.stage_number,
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
        backdrop=req.backdrop,
        event_logo=event_logo.name if event_logo is not None else None,
        made_with=req.made_with,
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
    rt = runtime()
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
                shooter=resolved_identity_for(
                    project,
                    root,
                    look=look,
                    index=0,
                    label=project.competitor_name or project.name,
                ),
                ffmpeg_binary=rt.ffmpeg_binary,
                work_dir=Path(work),
                event_logo=event_logo,
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
