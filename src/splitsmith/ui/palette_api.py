"""``POST /api/shooters/{slug}/palette-sources`` (issue #1273): what the Look
editor's palette suggestions are chosen against. The stages' frames (from the
trims on this disk; none on a hosted container), their average colour, and
the shooter's logo colours. Reads the project; writes nothing. A sync route,
like the export preview: ffmpeg and Pillow block.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..looks import DEFAULT_LOOK, load_look
from ..palette_sources import RGB, Swatch, average_colour, dominant_colours, logo_colours, stage_frames
from ..runtime import runtime
from .identity_media import resolved_identity_for

router = APIRouter()


class PaletteSourcesRequest(BaseModel):
    stage_numbers: list[int] = Field(default_factory=list, max_length=6)


class PaletteSources(BaseModel):
    footage: list[Swatch]
    average: RGB | None
    logo: list[Swatch]


@router.post("/api/shooters/{slug}/palette-sources", response_model=PaletteSources)
def palette_sources(slug: str, req: PaletteSourcesRequest, request: Request) -> PaletteSources:
    state = request.app.state.splitsmith_state
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    frames = []
    with tempfile.TemporaryDirectory(prefix="palette-sources-") as work:
        for number in req.stage_numbers:
            frames.extend(
                stage_frames(project, root, number, ffmpeg_binary=runtime().ffmpeg_binary, work=Path(work))
            )
    identity = resolved_identity_for(
        project, root, look=load_look(DEFAULT_LOOK), index=0, label=project.competitor_name or project.name
    )
    return PaletteSources(
        footage=dominant_colours(frames),
        average=average_colour(frames),
        logo=logo_colours(identity.logo_path),
    )


__all__ = ["router"]
