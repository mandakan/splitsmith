"""Hosted-mode stage status is derived from ``state_docs``, not disk.

Regression guard for the "sidebar counter stuck at 0/N" bug: in hosted
mode audit docs live in Postgres ``state_docs``, but the status/count
helpers used to read a local ``audit/stage{n}.json`` that never exists on
the web container -- so every stage reported ``ready`` and no stage ever
flipped to ``audited``. That also broke the anonymous share/Results view
(no audited stage -> nothing to stream), which is the exact flow these
tests protect.

Uses the sqlite-backed hosted harness (no docker) from ``hosted_helpers``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select as _select

from splitsmith import match_model
from splitsmith.db import ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

# ``hosted_app`` / ``hosted_env`` are auto-discovered via conftest.py; only
# the helpers need importing (same convention as test_share_routes.py).
from tests.hosted_helpers import _CapturingSender, login, seed_match

MID = "brm-status-01"
SLUG = "anna"
OWNER = "owner@example.com"


def _seed_audited_stage(db_url: str, user_email: str) -> None:
    """Seed a match + shooter with one audit-ready stage, and a saved audit
    doc in state_docs (a ``save`` event -> the stage is ``audited``). No
    audit file is ever written to disk -- that is the whole point."""
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            user_id = (await s.execute(_select(User).where(User.email == user_email))).scalar_one().id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(
            match_id=MID,
            name="Status match",
            shooters=[SLUG],
            stages=[match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")],
        )
        await store.save_match(MID, match.model_dump(mode="json"), expected_version=0)
        project = MatchProject(
            name="Anna",
            stages=[
                StageEntry(
                    stage_number=1,
                    stage_name="Stage 1",
                    time_seconds=12.5,
                    videos=[StageVideo(path=Path("raw/v.mp4"), role="primary")],
                )
            ],
        )
        await store.save_project(MID, SLUG, project.model_dump(mode="json"), expected_version=0)
        # The saved audit lives ONLY in state_docs, never on disk.
        await store.save_audit(
            MID,
            SLUG,
            1,
            {"stage_number": 1, "shots": [], "audit_events": [{"kind": "save"}]},
            expected_version=0,
        )

    asyncio.run(_seed())


def test_get_project_reports_audited_from_state_docs(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    _seed_audited_stage(hosted_env, OWNER)

    resp = client.get(f"/api/matches/{MID}/shooters/{SLUG}/project")
    assert resp.status_code == 200, resp.text
    stages = resp.json()["stages"]
    assert stages[0]["status"] == "audited", stages[0]


def test_shooter_list_counts_audited_from_state_docs(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    seed_match(hosted_env, OWNER, MID)
    _seed_audited_stage(hosted_env, OWNER)

    resp = client.get(f"/api/matches/{MID}/match/shooters")
    assert resp.status_code == 200, resp.text
    shooter = resp.json()["shooters"][0]
    assert shooter["stages_audited"] == 1, shooter
    assert shooter["stage_statuses"][0]["status"] == "audited", shooter
