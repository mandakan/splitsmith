"""Stage details (spec ``docs/superpowers/specs/2026-10-22-stage-details-design.md``):
read and write one shooter's stage times, scores and shooting order.

Local and hosted alike: it edits the shooter's project document through
the usual load and save, like the stage time route it complements.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import stage_details
from ..match_project import MatchProject, StageScorecard
from . import server as _server

logger = logging.getLogger(__name__)

router = APIRouter()

#: The content type SSI gives IPSC matches; their participant pages use
#: ``PARTICIPANT_CT``. Read off a real link (Black Handgun 2023: match
#: ``22/17169``, Janne's page ``participant/23/512019``). Other match kinds
#: get the match page instead, since their participant type is unknown.
IPSC_MATCH_CT = 22
PARTICIPANT_CT = 23


class StageDetailsStage(BaseModel):
    stage_number: int
    stage_name: str
    skipped: bool
    #: None when the stage has no time yet.
    time_seconds: float | None
    time_manual: bool
    shot_order: int | None
    scorecard: StageScorecard | None


class StageDetailsOther(BaseModel):
    """Another shooter of the match whose order can be copied."""

    slug: str
    name: str
    #: Stage numbers in shooting order.
    order: list[int]
    #: The order comes from scorecard times, not one the user typed.
    from_scorecards: bool


class StageDetailsView(BaseModel):
    slug: str
    name: str
    division: str | None
    #: The effective power factor, and whether the user set it.
    power_factor: Literal["minor", "major"]
    power_factor_set: bool
    #: The shooter's results page on Shoot'n Score It, or the match's.
    ssi_url: str | None
    #: The order the scorecards imply, for the greyed hint.
    scorecard_order: list[int]
    stages: list[StageDetailsStage]
    others: list[StageDetailsOther]


class StageDetailsRow(BaseModel):
    stage_number: int
    time_seconds: float | None = None
    shot_order: int | None = None
    alphas: int | None = None
    charlies: int | None = None
    deltas: int | None = None
    misses: int | None = None
    no_shoots: int | None = None
    procedurals: int | None = None
    stage_points: float | None = None
    hit_factor: float | None = None
    stage_pct: float | None = None
    dq: bool | None = None
    #: Score fields the user typed or pasted; the rest keep what is stored.
    manual: list[str] = Field(default_factory=list)


class StageDetailsRequest(BaseModel):
    rows: list[StageDetailsRow]
    power_factor: Literal["minor", "major"] | None = None


def ssi_url(project: MatchProject) -> str | None:
    ct, match_id = project.scoreboard_content_type, project.scoreboard_match_id
    if ct is None or not match_id:
        return None
    if ct == IPSC_MATCH_CT and project.selected_competitor_id is not None:
        return (
            f"https://shootnscoreit.com/event/participant/{PARTICIPANT_CT}/{project.selected_competitor_id}/"
        )
    return f"https://shootnscoreit.com/event/{ct}/{match_id}/"


def _typed_order(project: MatchProject) -> list[int]:
    typed = [s for s in project.stages if s.shot_order is not None]
    return [s.stage_number for s in sorted(typed, key=lambda s: s.shot_order)]  # type: ignore[arg-type,return-value]


def _name(project: MatchProject, slug: str) -> str:
    return project.competitor_name or slug


def _view(slug: str, project: MatchProject, others: dict[str, MatchProject]) -> StageDetailsView:
    other_rows: list[StageDetailsOther] = []
    for other_slug, other in others.items():
        if other_slug == slug:
            continue
        typed = _typed_order(other)
        order = typed or stage_details.scorecard_order(other)
        if order:
            other_rows.append(
                StageDetailsOther(
                    slug=other_slug, name=_name(other, other_slug), order=order, from_scorecards=not typed
                )
            )
    return StageDetailsView(
        slug=slug,
        name=_name(project, slug),
        division=project.competitor_division,
        power_factor=stage_details.power_factor_for(project.competitor_division, project.power_factor),
        power_factor_set=project.power_factor is not None,
        ssi_url=ssi_url(project),
        scorecard_order=stage_details.scorecard_order(project),
        stages=[
            StageDetailsStage(
                stage_number=s.stage_number,
                stage_name=s.stage_name,
                skipped=s.skipped,
                time_seconds=s.time_seconds if s.time_seconds > 0 else None,
                time_manual=s.time_seconds_manual,
                shot_order=s.shot_order,
                scorecard=s.scorecard,
            )
            for s in project.stages
        ],
        others=other_rows,
    )


def _match_projects(state: object) -> dict[str, MatchProject]:
    _, match = _server._resolve_match_context(state)  # type: ignore[arg-type]
    return state.match_bundle(match).projects  # type: ignore[attr-defined]


def apply_rows(project: MatchProject, req: StageDetailsRequest) -> None:
    """Write the page's rows onto ``project``; raises ``ValueError`` with a
    user-facing message on a row the page should not have sent."""
    stages = {s.stage_number: s for s in project.stages}
    orders = [r.shot_order for r in req.rows if r.shot_order is not None]
    if len(orders) != len(set(orders)):
        raise ValueError("Two stages have the same place in the shooting order.")
    if any(o < 1 for o in orders):
        raise ValueError("The shooting order starts at 1.")
    unknown = [r.stage_number for r in req.rows if r.stage_number not in stages]
    if unknown:
        raise ValueError(f"No stage {unknown[0]} in this match.")
    project.power_factor = req.power_factor
    pf = stage_details.power_factor_for(project.competitor_division, project.power_factor)
    for row in req.rows:
        stage = stages[row.stage_number]
        stage.shot_order = row.shot_order
        stored = stage.time_seconds if stage.time_seconds > 0 else None
        if row.time_seconds != stored:
            if row.time_seconds is None or row.time_seconds <= 0:
                stage.time_seconds, stage.time_seconds_manual = 0.0, False
            else:
                stage.time_seconds, stage.time_seconds_manual = float(row.time_seconds), True
        manual = [f for f in stage_details.SCORE_FIELDS if f in row.manual]
        card = stage.scorecard or StageScorecard()
        if not manual and not card.manual and stage.scorecard is None:
            continue
        card = card.model_copy(deep=True)
        for field in card.manual:
            if field not in manual:
                setattr(card, field, None)
        for field in manual:
            setattr(card, field, getattr(row, field))
        card.manual = manual
        card.derived = [f for f in card.derived if f not in manual]
        stage.scorecard = stage_details.derive(card, stage.time_seconds, pf)


@router.get("/api/shooters/{slug}/stage-details", response_model=StageDetailsView)
def get_stage_details(request: Request, slug: str) -> StageDetailsView:
    state = request.app.state.splitsmith_state
    projects = _match_projects(state)
    project = projects.get(slug)
    if project is None:
        raise HTTPException(status_code=404, detail=f"No shooter {slug!r} in this match.")
    return _view(slug, project, projects)


@router.put("/api/shooters/{slug}/stage-details", response_model=StageDetailsView)
def put_stage_details(request: Request, slug: str, req: StageDetailsRequest) -> StageDetailsView:
    state = request.app.state.splitsmith_state
    projects = _match_projects(state)
    if slug not in projects:
        raise HTTPException(status_code=404, detail=f"No shooter {slug!r} in this match.")
    project = state.shooter_project(slug)
    try:
        apply_rows(project, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.save(state.shooter_root(slug))
    projects[slug] = project
    return _view(slug, project, projects)
