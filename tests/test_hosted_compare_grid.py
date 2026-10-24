"""The compare grid on a hosted install (issue #755).

A hosted worker has no ``match.json``, no ``project.json`` and no trims on
disk: the documents live in the state store and the trims in object
storage. Before this, ``POST /api/match/compare-export`` read all three
off the disk, so a hosted render either 400'd on "no trims" or ran and
left its output where no API container could reach it.

These tests boot the app in hosted mode (sqlite state store, moto S3)
through the fixtures ``test_hosted_raw_upload.py`` already provides, seed
a two-stage match whose shooter has a beeped primary and a trim *only in
storage*, stub the renderer, and check the whole path: the trim is
mirrored down for the render, the finished grid lands under the
match-scoped key, and the download route serves it back.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.compare import project_loader as pl_mod
from splitsmith.config import VideoMetadata
from splitsmith.match_project import MatchProject, StageVideo
from splitsmith.storage import S3Storage
from splitsmith.ui import export_storage
from splitsmith.ui import server as server_mod
from tests.test_hosted_raw_upload import hosted_client, hosted_client_with_match, hosted_db

__all__ = ["hosted_client", "hosted_client_with_match", "hosted_db"]  # pytest discovers fixtures by name

TRIM_BYTES = b"not really a trim, but a real object in the bucket"
GRID_BYTES = b"stub grid"


def _fake_probe(_path: Path) -> VideoMetadata:
    return VideoMetadata(width=1920, height=1080, duration_seconds=30.0, frame_rate_num=30, frame_rate_den=1)


def _give_the_shooter_a_beeped_primary(
    db_url: str, match_id: str, slug: str, *, reviewed: bool = False, stages: tuple[int, ...] = (1,)
) -> None:
    """Put a primary video with a confirmed beep on stage 1 of the shooter's
    project *document* -- the state store, not the disk, which is what a
    hosted worker reads."""
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
        for number in stages:
            project.stage(number).videos = [
                StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0, beep_reviewed=reviewed)
            ]
            project.stage(number).time_seconds = 10.0
        await store.save_project(match_id, slug, project.model_dump(mode="json"), expected_version=version)

    asyncio.run(_edit())


def _wait_for_job(client: TestClient, match_id: str, job_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/matches/{match_id}/me/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


@pytest.fixture
def inline_deferrer(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run a queued hosted job inline, through the backend's own
    ``run_job`` seam (wire args rehydrated the way the worker does it),
    instead of deferring to Procrastinate -- which needs Postgres, and the
    harness is sqlite. Requested *before* the app fixture so the stub is
    in place when ``create_app`` builds the deferrer; the app state is
    filled in afterwards through ``holder``."""
    from splitsmith import queue as queue_mod
    from splitsmith.ui.job_journal import rehydrate_args

    holder: dict[str, Any] = {}

    def fake_make_deferrer(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        async def _defer(
            *, job_id: str, user_id: str, kind: str, args: dict[str, Any], match_id: str | None
        ) -> None:
            if holder.get("hold"):  # leave the job queued: only the request runs
                holder.setdefault("held", []).append(job_id)
                return
            state = holder["state"]
            await state.jobs.run_job(job_id=job_id, kind=kind, args=rehydrate_args(kind, args))

        return _defer

    monkeypatch.setattr(queue_mod, "make_deferrer", fake_make_deferrer)
    return holder


@pytest.fixture
def hosted_grid(
    inline_deferrer, hosted_client_with_match, hosted_db, monkeypatch: pytest.MonkeyPatch
):  # noqa: F811
    """A hosted match whose one shooter has stage 1 beeped and trimmed in
    storage only, with the renderer and ffprobe stubbed."""
    client, storage, match_id, slug = hosted_client_with_match
    inline_deferrer["state"] = client.app.state.splitsmith_state
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug)
    # The trim under the shooter's own export key, the name the per-stage
    # exporter derives (stage number + the *project's* stage name).
    storage.write_bytes(f"matches/{match_id}/shooters/{slug}/exports/stage1_one_trimmed.mp4", TRIM_BYTES)

    rendered: list[dict[str, Any]] = []

    def fake_render(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        rendered.append({"shooters": shooters, "audio_label": audio_label, **kwargs})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(GRID_BYTES)
        stages = tuple(
            mp4_grid_mod.StageOutcome(stage_number=n, stage_name=f"Stage {n}", ok=True)
            for n in sorted({n for s in shooters for n in s.stages_by_number})
        )
        return mp4_grid_mod.GridRenderResult(output_path=output_path, stages=stages)

    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", fake_render)
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    return client, storage, match_id, slug, rendered


def test_hosted_render_pulls_the_trim_pushes_the_grid_and_serves_it(hosted_grid) -> None:
    client, storage, match_id, slug, rendered = hosted_grid
    assert isinstance(storage, S3Storage)

    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug, "output_name": "grid-one"},
    )
    assert resp.status_code == 200, resp.text
    job = _wait_for_job(client, match_id, resp.json()["id"])
    assert job["status"] == "succeeded", job

    # The render saw the shooter's trim (mirrored down from storage) and
    # named the tile the way the hosted shooter list names the shooter.
    (call,) = rendered
    (bundle,) = call["shooters"]
    assert bundle.label == "Me"
    assert call["audio_label"] == "Me"
    assert 1 in bundle.stages_by_number
    # #1691: nothing is pulled to plan; the renderer reads the tile inside
    # its stage's acquire, and it is gone again after.
    trim = bundle.stages_by_number[1].trim_path
    assert not trim.exists()
    with call["tile_access"].acquire(1):
        assert trim.read_bytes() == TRIM_BYTES
    call["tile_access"].release()
    assert not trim.exists()

    # The finished grid is at the match-scoped key, and the result names it.
    result = job["result"]
    assert result["output_name"] == "grid-one.mp4"
    assert result["stages_rendered"] == 1
    key = export_storage.match_export_key(match_id, Path("grid-one.mp4"))
    assert key == f"matches/{match_id}/exports/grid-one.mp4"
    assert storage.read_bytes(key) == GRID_BYTES

    # The download route serves it even after the worker's disk is gone.
    Path(result["output_path"]).unlink()
    download = client.get(f"/api/matches/{match_id}/match/exports/file/grid-one.mp4")
    assert download.status_code == 200, download.text
    assert download.content == GRID_BYTES
    assert download.headers["content-type"].startswith("video/mp4")


def test_hosted_download_confines_to_the_exports_dir_and_404s_the_unknown(hosted_grid) -> None:
    client, _storage, match_id, _slug, _rendered = hosted_grid
    assert client.get(f"/api/matches/{match_id}/match/exports/file/../match.json").status_code in (400, 404)
    assert client.get(f"/api/matches/{match_id}/match/exports/file/nope.mp4").status_code == 404


def test_hosted_validation_sees_the_storage_trims(hosted_grid) -> None:
    """The endpoint's own "no shooter has a trim" 400 used to fire hosted
    because it looked at the disk; the trims are in storage and count."""
    client, storage, match_id, slug, _rendered = hosted_grid
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [2], "audio_from": slug},
    )
    # Stage 2 has no beep and no trim: still refused, for the right reason.
    assert resp.status_code == 400
    assert "confirmed beep" in resp.json()["detail"]
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    assert resp.status_code == 200, resp.text


def test_the_request_reads_no_trim_bytes(hosted_grid, inline_deferrer) -> None:
    """Validation answers from a storage listing; the trims are the render
    job's to pull. Pulling every exported trim in the request ran serve
    (2 GiB, in-memory disk) out of memory on a 48-trim match."""
    from splitsmith.observability import STORAGE_IO

    client, _storage, match_id, slug, _rendered = hosted_grid
    inline_deferrer["hold"] = True
    read_before, _ = STORAGE_IO.snapshot()
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    read_after, _ = STORAGE_IO.snapshot()
    assert resp.status_code == 200, resp.text
    assert inline_deferrer["held"] == [resp.json()["id"]]
    # The documents the request reads are small; the trim alone is
    # len(TRIM_BYTES), and none of it moved.
    assert read_after - read_before < len(TRIM_BYTES)


SOURCE_BYTES = b"the raw head-cam file, only in the bucket" * 64
CUT_BYTES = b"a stage trim the grid job cut from the source"


@pytest.fixture
def hosted_source_only(hosted_grid, hosted_db, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """The ``hosted_grid`` match with no exported trim: a confirmed beep on
    stage 1 and its source in the bucket, nothing else (#1681). A
    storage-backed project keeps no full-resolution audit trim (#1683), so
    the grid job has to cut the tile from the source. ffmpeg is faked: the
    recorded calls are the cut."""
    client, storage, match_id, slug, rendered = hosted_grid
    storage.delete(f"matches/{match_id}/shooters/{slug}/exports/stage1_one_trimmed.mp4")
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug, reviewed=True)
    storage.write_bytes("raw/v.mp4", SOURCE_BYTES)
    cuts: list[dict[str, Any]] = []

    def fake_trim_video(input_path: Path, output_path: Path, **kwargs: Any) -> Any:
        # The source is local while the cut runs: the job mirrored it down.
        cuts.append({"input": Path(input_path), "source_bytes": Path(input_path).read_bytes(), **kwargs})
        Path(output_path).write_bytes(CUT_BYTES)

    monkeypatch.setattr(server_mod.trim_module, "trim_video", fake_trim_video)
    return client, storage, match_id, slug, rendered, cuts


def test_hosted_grid_cuts_a_tile_with_no_export_from_its_source(
    hosted_source_only, inline_deferrer, hosted_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request checks a confirmed beep and the source's listing and
    moves no media; the job cuts the stage trim from the source with the
    export's window and stream copy, drops the source on a memory-backed
    disk, and renders the tile from the cut, marked as such."""
    from splitsmith.observability import STORAGE_IO

    client, _storage, match_id, slug, rendered, cuts = hosted_source_only
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "splitsmith-worker-abc")
    # Stage 2 could be cut too, but only stage 1 is selected: one source read.
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug, reviewed=True, stages=(1, 2))

    inline_deferrer["hold"] = True
    read_before, _ = STORAGE_IO.snapshot()
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    read_after, _ = STORAGE_IO.snapshot()
    assert resp.status_code == 200, resp.text
    assert cuts == []  # the request plans the cut; only the job makes it
    assert read_after - read_before < len(SOURCE_BYTES)

    inline_deferrer["hold"] = False
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug, "output_name": "grid-cut"},
    )
    assert resp.status_code == 200, resp.text
    job = _wait_for_job(client, match_id, resp.json()["id"])
    assert job["status"] == "succeeded", job

    (cut,) = cuts
    assert cut["source_bytes"] == SOURCE_BYTES
    assert cut["mode"] == "lossless"
    assert cut["beep_time"] == 5.0
    assert cut["stage_time"] == 10.0
    assert (cut["pre_buffer_seconds"], cut["post_buffer_seconds"]) == (5.0, 5.0)
    # Dropped once cut: Cloud Run's disk is memory.
    assert not cut["input"].exists()
    (call,) = rendered
    (bundle,) = call["shooters"]
    stage = bundle.stages_by_number[1]
    assert stage.trim_source == "source"
    assert stage.trim_path.name.endswith("_cut.mp4")
    assert job["result"]["missing_trims"] == []


def test_hosted_grid_keeps_the_source_on_a_real_disk(hosted_source_only, monkeypatch) -> None:
    """A worker with a real disk (gaspode) keeps its source cache."""
    client, _storage, match_id, slug, _rendered, cuts = hosted_source_only
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, match_id, resp.json()["id"])["status"] == "succeeded"
    (cut,) = cuts
    assert cut["input"].exists()


def test_hosted_grid_refuses_an_unconfirmed_beep(hosted_source_only, hosted_db) -> None:
    """A detected but unconfirmed beep may be wrong: no tile is cut around it."""
    client, _storage, match_id, slug, _rendered, cuts = hosted_source_only
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug, reviewed=False)
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "Missing: Me (stage 1)." in detail
    assert "confirmed beep" in detail and "Quick trims" not in detail
    assert cuts == []


def test_hosted_grid_refuses_a_stage_whose_source_is_gone(hosted_source_only) -> None:
    client, storage, match_id, slug, _rendered, cuts = hosted_source_only
    storage.delete("raw/v.mp4")
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export",
        json={"stage_numbers": [1], "audio_from": slug},
    )
    assert resp.status_code == 400, resp.text
    assert "footage" in resp.json()["detail"]
    assert cuts == []


def test_compare_grid_args_rehydrate_for_the_worker() -> None:
    """A queued ``req`` comes back as the typed model, the way ``export``
    and ``match_export`` already do; a hosted worker used to get a dict."""
    from splitsmith.ui.exports_api import CompareGridRequest
    from splitsmith.ui.job_journal import rehydrate_args, to_wire_args

    req = CompareGridRequest(stage_numbers=[1], audio_from="me", overlay=True, stage_titles="slate")
    wire = to_wire_args({"req": req, "match_root": "/m"})
    assert isinstance(wire["req"], dict)
    back = rehydrate_args("compare-grid", wire)
    assert isinstance(back["req"], CompareGridRequest)
    assert back["req"] == req
    assert back["match_root"] == "/m"
