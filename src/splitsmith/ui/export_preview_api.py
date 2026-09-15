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

import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..export_preview import (
    PreviewCard,
    PreviewError,
    PreviewSpec,
    audit_digest,
    preview_key,
    render_preview,
)
from ..overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from ..overlay_theme import load_theme
from ..runtime import runtime

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
    head_pad_seconds: float = Field(default=5.0, ge=0)
    tail_pad_seconds: float = Field(default=5.0, ge=0)
    #: The bundle name, as the match export's ``project_name``.
    project_name: str | None = None


class _NoRasterizer:
    """For ``card=frame``: nothing to rasterize, so no browser is launched."""

    def png(self, html: str, *, width: int, height: int) -> bytes:  # pragma: no cover
        raise AssertionError("the frame card never rasterizes")


@router.post("/api/shooters/{slug}/export-preview")
def export_preview(slug: str, req: ExportPreviewRequest, request: Request) -> Response:
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    audit_doc, _audit_version = state.load_audit(slug, req.stage_number)
    spec = PreviewSpec(
        card=req.card,
        stage_number=req.stage_number,
        width=req.width,
        title_info=req.title_info,
        head_pad_seconds=req.head_pad_seconds,
        tail_pad_seconds=req.tail_pad_seconds,
        project_name=req.project_name,
    )
    rt = runtime()
    cache_dir = rt.cache_dir / "export-preview"
    key = preview_key(
        spec, slug=slug, project_updated_at=project.updated_at.isoformat(), audit=audit_digest(audit_doc)
    )
    cached = cache_dir / f"{key}.png"
    if cached.exists():
        return _png(cached.read_bytes())

    def _render(rasterizer: Rasterizer) -> bytes:
        with tempfile.TemporaryDirectory(prefix="export-preview-") as work:
            return render_preview(
                spec,
                project=project,
                root=root,
                audit_doc=audit_doc,
                theme=load_theme("splitsmith"),
                rasterizer=rasterizer,
                ffmpeg_binary=rt.ffmpeg_binary,
                work_dir=Path(work),
            )

    try:
        if req.card == "frame":
            png = _render(_NoRasterizer())
        else:
            with rasterizer_factory() as rasterizer:
                png = _render(rasterizer)
    except PreviewError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    except RasterizerUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="the preview needs a browser: Playwright could not launch Chromium"
        ) from exc
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(png)
    except OSError as exc:
        logger.warning("could not cache the export preview (%s)", exc)
    return _png(png)


def _png(data: bytes) -> Response:
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})
