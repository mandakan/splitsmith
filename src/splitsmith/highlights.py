"""The highlight reel's moments (spec 2026-10-18-highlight-reel-design).

Pure: reads ``TileStageData`` and audit docs handed in, never a file. A
stage is ready for the reel when its shots are audited and its breakdown
is done: opened in Breakdown (``events_seeded``) with no auto proposal
left.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .match_project import StageStatus

ReadinessState = Literal[
    "ready", "not_audited", "breakdown_not_opened", "proposals_left", "no_footage", "skipped"
]


@dataclass(frozen=True)
class StageReadiness:
    stage_number: int
    state: ReadinessState
    proposals: int = 0


def _auto_proposals(doc: dict[str, Any]) -> int:
    events = doc.get("events")
    if not isinstance(events, list):
        return 0
    return sum(1 for e in events if isinstance(e, dict) and e.get("source") == "auto")


def stage_readiness(
    stage_number: int, status: StageStatus, audit_doc: dict[str, Any] | None, *, has_footage: bool
) -> StageReadiness:
    """Where one stage stands for the reel. The order is the order a user
    fixes things in: audit, then breakdown."""
    if status == StageStatus.skipped:
        return StageReadiness(stage_number, "skipped")
    if not has_footage:
        return StageReadiness(stage_number, "no_footage")
    if status != StageStatus.audited or audit_doc is None:
        return StageReadiness(stage_number, "not_audited")
    if not audit_doc.get("events_seeded"):
        return StageReadiness(stage_number, "breakdown_not_opened")
    proposals = _auto_proposals(audit_doc)
    if proposals:
        return StageReadiness(stage_number, "proposals_left", proposals=proposals)
    return StageReadiness(stage_number, "ready")


def reel_unlocked(rows: Sequence[StageReadiness]) -> bool:
    """Every stage ready or out of play (skipped, no footage), and at
    least one ready."""
    if not any(r.state == "ready" for r in rows):
        return False
    return all(r.state in ("ready", "skipped", "no_footage") for r in rows)
