"""The highlight reel over the API (spec 2026-10-18-highlight-reel-design,
part 2): readiness and the moment menu.

Reads the project and its audit docs only, so it answers the same locally
and hosted; the render job is what touches footage.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..config import DEFAULT_MOMENTS
from ..highlights import (
    BEAT_ORDER,
    MOMENT_GROUPS,
    MomentGroup,
    ReadinessState,
    readiness_for_project,
    reel_unlocked,
)
from ..reel_plan import MOMENT_LABELS

router = APIRouter()


class StageReadinessOut(BaseModel):
    stage_number: int
    state: ReadinessState
    proposals: int


class MenuItem(BaseModel):
    kind: str
    label: str
    group: MomentGroup
    default: bool


class HighlightsOut(BaseModel):
    unlocked: bool
    ready: int
    total: int
    stages: list[StageReadinessOut]
    menu: list[MenuItem]
    cuts: list[str] = ["normal", "short"]


def stage_rows(state: Any, slug: str) -> tuple:
    """Readiness for every stage of ``slug``: the audit docs in one query
    hosted, one file per stage locally."""
    project = state.shooter_project(slug)
    root = state.shooter_root(slug)
    docs = state.load_audit_docs(slug)
    statuses = project.stage_statuses(root, audit_docs=docs)
    if docs is None:
        docs = {stage.stage_number: state.load_audit(slug, stage.stage_number)[0] for stage in project.stages}
    return readiness_for_project(project, statuses, docs)


@router.get("/api/shooters/{slug}/highlights", response_model=HighlightsOut)
def get_highlights(slug: str, request: Request) -> HighlightsOut:
    rows = stage_rows(request.app.state.splitsmith_state, slug)
    counted = [r for r in rows if r.state not in ("skipped", "no_video")]
    return HighlightsOut(
        unlocked=reel_unlocked(rows),
        ready=sum(1 for r in counted if r.state == "ready"),
        total=len(counted),
        stages=[
            StageReadinessOut(stage_number=r.stage_number, state=r.state, proposals=r.proposals) for r in rows
        ],
        menu=[
            MenuItem(
                kind=kind,
                label=MOMENT_LABELS[kind],
                group=MOMENT_GROUPS[kind],
                default=kind in DEFAULT_MOMENTS,
            )
            for kind in BEAT_ORDER
        ],
    )


__all__ = ["router", "stage_rows"]
