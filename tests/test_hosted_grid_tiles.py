"""A hosted grid plans from storage and pulls a stage's tiles only to render it (#1691).

The whole-match grid job used to pull every stage's exported trim before
planning: about 11 GB on HFO Masters, onto a Cloud Run disk that is memory.
``HostedGridTiles`` lets ``_load_compare_bundles`` plan from storage alone
(the listing says which trims exist, ffprobe reads each one's header over a
presigned URL), keys every tile by its stored object instead of a local
file, and pulls a stage's tiles only inside ``acquire``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from splitsmith.compare import project_loader as pl_mod
from splitsmith.config import VideoMetadata
from splitsmith.match_project import MatchProject, StageVideo
from splitsmith.ui import server as server_mod
from splitsmith.ui.grid_tiles import HostedGridTiles
from tests.test_hosted_compare_grid import (
    SOURCE_BYTES,
    TRIM_BYTES,
    _give_the_shooter_a_beeped_primary,
    _wait_for_job,
    hosted_grid,
    hosted_source_only,
    inline_deferrer,
)
from tests.test_hosted_raw_upload import hosted_client, hosted_client_with_match, hosted_db

__all__ = [
    "hosted_client",
    "hosted_client_with_match",
    "hosted_db",
    "hosted_grid",
    "hosted_source_only",
    "inline_deferrer",
]


def _load(  # type: ignore[no-untyped-def]
    client, match_id: str, slug: str, tiles: HostedGridTiles, stages: list[int] | None = None, **kwargs: Any
):
    """``_load_compare_bundles`` as the grid job calls it, inside a job body
    so it runs with the job's match and tenant context: the request route
    queues it, the inline deferrer runs it."""
    stages = stages or [1]
    state = client.app.state.splitsmith_state
    out: dict[str, Any] = {}

    def body(handle, req, match_root: str) -> None:  # type: ignore[no-untyped-def]
        out["state"] = state
        out["bundles"] = server_mod._load_compare_bundles(
            Path(match_root), state.match(), state=state, stage_numbers=stages, tiles=tiles, **kwargs
        )
        out["project"] = state.shooter_project(req.audio_from)
        out["root"] = state.shooter_root(req.audio_from)

    state.jobs.bodies.register("compare-grid", body)
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export", json={"stage_numbers": stages, "audio_from": slug}
    )
    assert resp.status_code == 200, resp.text
    job = _wait_for_job(client, match_id, resp.json()["id"])
    assert job["status"] == "succeeded", job
    _LAST.update(out)
    return out["bundles"]


_LAST: dict[str, Any] = {}


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record what ffprobe is pointed at: a planning probe reads a URL."""
    seen: list[object] = []

    def probe(target: object) -> VideoMetadata:
        seen.append(target)
        return VideoMetadata(
            width=1920, height=1080, duration_seconds=30.0, frame_rate_num=30, frame_rate_den=1
        )

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", probe)
    return seen


def _bytes_read() -> int:
    from splitsmith.observability import STORAGE_IO

    return STORAGE_IO.snapshot()[0]


def test_lazy_bundles_pull_nothing(hosted_grid, probes) -> None:
    client, _storage, match_id, slug, _rendered = hosted_grid
    tiles = HostedGridTiles()
    before = _bytes_read()
    (bundle,) = _load(client, match_id, slug, tiles)
    stage = bundle.stages_by_number[1]
    assert not stage.trim_path.exists()
    assert _bytes_read() - before < len(TRIM_BYTES)
    assert probes and all(isinstance(p, str) and p.startswith("http") for p in probes)
    assert tiles.tile_meta[str(stage.trim_path)]["duration_seconds"] == 30.0


def test_a_second_load_reuses_the_first_ones_probes(hosted_grid, probes) -> None:
    client, _storage, match_id, slug, _rendered = hosted_grid
    first = HostedGridTiles()
    _load(client, match_id, slug, first)
    probes.clear()
    _load(client, match_id, slug, HostedGridTiles(tile_meta=first.tile_meta))
    assert probes == []


def test_identities_are_the_same_from_two_workers(hosted_grid, probes) -> None:
    client, _storage, match_id, slug, _rendered = hosted_grid
    one, two = HostedGridTiles(), HostedGridTiles()
    _load(client, match_id, slug, one)
    _load(client, match_id, slug, two)
    assert one.identities == two.identities
    (identity,) = one.identities.values()
    assert identity.startswith("storage:") and "exports/stage1_one_trimmed.mp4" in identity
    assert "http" not in identity and "Signature" not in identity


def test_identity_changes_when_the_export_changes(hosted_grid, probes) -> None:
    client, storage, match_id, slug, _rendered = hosted_grid
    before = HostedGridTiles()
    _load(client, match_id, slug, before)
    storage.write_bytes(
        f"matches/{match_id}/shooters/{slug}/exports/stage1_one_trimmed.mp4", TRIM_BYTES + b"!"
    )
    after = HostedGridTiles()
    _load(client, match_id, slug, after)
    assert before.identities != after.identities


def test_acquire_pulls_a_stage_once_and_release_deletes_it(hosted_grid, probes) -> None:
    """A stage's reads come in a row (hold still, edges, encode): the first
    acquire pulls it and the next ones find it; release deletes it."""
    client, _storage, match_id, slug, _rendered = hosted_grid
    tiles = HostedGridTiles()
    (bundle,) = _load(client, match_id, slug, tiles)
    trim = bundle.stages_by_number[1].trim_path
    assert not trim.exists()
    before = _bytes_read()
    with tiles.acquire(1):
        assert trim.read_bytes() == TRIM_BYTES
    pulled = _bytes_read() - before
    assert pulled >= len(TRIM_BYTES)
    with tiles.acquire(1):
        assert trim.read_bytes() == TRIM_BYTES
    assert _bytes_read() - before == pulled
    tiles.release()
    assert not trim.exists()


def test_acquiring_another_stage_releases_the_first(two_stage_grid, probes) -> None:
    client, match_id, slug = two_stage_grid
    tiles = HostedGridTiles()
    (bundle,) = _load(client, match_id, slug, tiles, stages=[1, 2])
    one, two = (bundle.stages_by_number[n].trim_path for n in (1, 2))
    with tiles.acquire(1):
        assert one.exists()
    with tiles.acquire(2):
        assert two.exists() and not one.exists()
    tiles.release()
    assert not two.exists()


def test_a_tile_already_on_disk_is_never_deleted(hosted_grid, probes) -> None:
    client, _storage, match_id, slug, _rendered = hosted_grid
    tiles = HostedGridTiles()
    (bundle,) = _load(client, match_id, slug, tiles)
    trim = bundle.stages_by_number[1].trim_path
    trim.parent.mkdir(parents=True, exist_ok=True)
    trim.write_bytes(b"a local export")
    with tiles.acquire(1):
        pass
    tiles.release()
    assert trim.read_bytes() == b"a local export"


def test_an_inset_clip_is_acquired_and_identified(hosted_grid, hosted_db, probes) -> None:
    client, storage, match_id, slug, _rendered = hosted_grid
    _give_a_chest_camera(hosted_db, match_id, slug)
    _load(client, match_id, slug, HostedGridTiles())
    project, root = _LAST["project"], _LAST["root"]
    chest = next(v for v in project.stage(1).videos if v.camera_mount == "chest")
    inset_trim = pl_mod.trim_path_for_video(project, root, 1, project.stage(1).stage_name, chest)
    storage.write_bytes(f"matches/{match_id}/shooters/{slug}/exports/{inset_trim.name}", b"inset")

    tiles = HostedGridTiles()
    (bundle,) = _load(client, match_id, slug, tiles, inset="chest")
    inset = bundle.stages_by_number[1].inset
    assert inset is not None and str(inset.trim_path) in tiles.identities
    with tiles.acquire(1):
        assert inset.trim_path.read_bytes() == b"inset"
    tiles.release()
    assert not inset.trim_path.exists()


def test_a_tile_cut_from_the_source_is_identified_and_cut_only_on_acquire(hosted_source_only, probes) -> None:
    client, _storage, match_id, slug, _rendered, cuts = hosted_source_only
    tiles = HostedGridTiles()
    (bundle,) = _load(client, match_id, slug, tiles)
    stage = bundle.stages_by_number[1]
    assert stage.trim_source == "source"
    identity = tiles.identities[str(stage.trim_path)]
    assert identity.startswith("source-cut:raw/v.mp4:")
    # Planning cut it once to measure it, then dropped it.
    assert len(cuts) == 1 and not stage.trim_path.exists()
    with tiles.acquire(1):
        assert stage.trim_path.exists()
    with tiles.acquire(1):
        pass
    tiles.release()
    assert len(cuts) == 2 and not stage.trim_path.exists()
    assert cuts[0]["source_bytes"] == SOURCE_BYTES


@pytest.fixture
def two_stage_grid(hosted_grid, hosted_db):  # type: ignore[no-untyped-def]  # noqa: F811
    client, storage, match_id, slug, _rendered = hosted_grid
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug, stages=(1, 2))
    storage.write_bytes(f"matches/{match_id}/shooters/{slug}/exports/stage2_two_trimmed.mp4", TRIM_BYTES)
    return client, match_id, slug


def test_an_unknown_version_never_matches_a_stored_segment(hosted_grid, probes, monkeypatch) -> None:
    """A tile whose object cannot be stat'ed gets an identity of its own
    each time, so a segment keyed by it is never reused."""
    client, storage, match_id, slug, _rendered = hosted_grid
    monkeypatch.setattr(type(storage), "stat", lambda self, path: None)
    one, two = HostedGridTiles(), HostedGridTiles()
    _load(client, match_id, slug, one)
    _load(client, match_id, slug, two)
    assert one.identities != two.identities


def _give_a_chest_camera(db_url: str, match_id: str, slug: str) -> None:
    """A second, beeped camera on stage 1, mounted on the chest."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from splitsmith.db import User, sessionmaker
    from splitsmith.db.project_state import ProjectStateStore

    async def _edit() -> None:
        factory = sessionmaker(create_async_engine(db_url))
        async with factory() as s:
            user_id = (await s.execute(select(User))).scalars().first().id
        store = ProjectStateStore(factory, user_id=user_id)
        doc, version = await store.load_project(match_id, slug)
        project = MatchProject.model_validate(doc)
        project.stage(1).videos.append(
            StageVideo(path=Path("raw/chest.mp4"), role="secondary", beep_time=4.0, camera_mount="chest")
        )
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)

    asyncio.run(_edit())
