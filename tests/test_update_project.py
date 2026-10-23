"""``server._update_project``: a job's project write survives a sibling job (#1686).

Hosted, several jobs of one shooter run in parallel; each used to save the
project it loaded at its start, and every save after the first failed with
a version conflict. The helper applies the job's own fields to the document
as it is at save time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from splitsmith.db import Base, ProjectStateStore, StateConflictError, User, create_engine, sessionmaker
from splitsmith.match_project import MatchProject
from splitsmith.ui import server
from splitsmith.ui.docstore import DbDocStore

MID = "m-update-project"


@pytest.fixture
def docs(tmp_path: Path) -> DbDocStore:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'docs.sqlite'}")

    async def _seed() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as session:
            session.add(User(email="update-project@x.se", access_tier="full"))
            await session.commit()
            return (await session.execute(select(User))).scalar_one().id

    store = DbDocStore(ProjectStateStore(sessionmaker(engine), user_id=asyncio.run(_seed())), MID)
    store.save_project("anna", MatchProject(name="anna").model_dump(mode="json"), version=0)
    return store


def test_a_job_write_keeps_a_sibling_jobs_save(docs: DbDocStore) -> None:
    started, version = docs.load_project("anna")  # the job loads at its start
    assert started is not None
    # A sibling job of the same shooter saves while this one works.
    sibling = MatchProject.model_validate(started)
    sibling.competitor_name = "Anna A"
    docs.save_project("anna", sibling.model_dump(mode="json"), version=version)

    # Saving the snapshot it loaded is the old way, and it loses hosted.
    stale = MatchProject.model_validate(started)
    stale.trim_pre_buffer_seconds = 7.0
    with pytest.raises(StateConflictError):
        docs.save_project("anna", stale.model_dump(mode="json"), version=version)

    def _apply(project: MatchProject) -> None:
        project.trim_pre_buffer_seconds = 7.0

    server._update_project(SimpleNamespace(docstore=lambda: docs), "anna", _apply)  # type: ignore[arg-type]
    saved, _ = docs.load_project("anna")
    assert saved is not None
    assert saved["trim_pre_buffer_seconds"] == 7.0  # the job's field
    assert saved["competitor_name"] == "Anna A"  # the sibling's, kept
