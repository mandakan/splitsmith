"""Every export kind publishes its parts (spec 2026-10-25, plan Task 4)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from splitsmith.ui.jobs import JobHandle, JobRegistry
from tests.test_export_identity import _settled
from tests.test_youtube_api import (  # noqa: F401 -- the fixture is used by name
    _conn,
    _fake_upload,
    _match_export_body,
    _stub_mp4_render,
    export_client,
    oauth,
)


def _one_part_registry(body: Any) -> JobRegistry:
    from splitsmith.ui.server import _one_part

    registry = JobRegistry(max_concurrent=1)
    registry.bodies.register("x", _one_part(body, key="render", label="Highlight reel", kind="reel"))
    return registry


def test_a_one_part_job_ends_done() -> None:
    registry = _one_part_registry(lambda handle, **_: None)
    job = asyncio.run(registry.submit(kind="x"))
    final = _settled(registry, job.id)
    assert final.parts is not None
    assert [(p.key, p.kind, p.status) for p in final.parts] == [("render", "reel", "done")]
    assert final.parts[0].started_at is not None and final.parts[0].finished_at is not None


def test_a_one_part_job_that_raises_ends_failed() -> None:
    def boom(handle: JobHandle, **_: Any) -> None:
        raise RuntimeError("boom")

    registry = _one_part_registry(boom)
    job = asyncio.run(registry.submit(kind="x"))
    final = _settled(registry, job.id)
    assert final.status == "failed"
    assert final.parts is not None and final.parts[0].status == "failed"


def test_a_match_export_publishes_load_stages_compose_and_its_upload(
    export_client: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from tests.ui._helpers import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    _stub_mp4_render(monkeypatch)
    oauth.save_connection(_conn("Mine"))
    _fake_upload(monkeypatch)
    body = _match_export_body(output_format="mp4", youtube_sidecar=True, youtube_upload=True)
    r = client.post("/api/shooters/me/export/match", json=body)
    assert r.status_code == 200, r.text
    _wait_for_job(client, r.json()["id"])
    _wait_for_jobs_to_drain(client)
    export = client.get(f"/api/me/jobs/{r.json()['id']}").json()
    keys = [p["key"] for p in export["parts"]]
    assert keys == ["load", *(f"stage-{n}" for n in body["stage_numbers"]), "compose"]
    assert {p["status"] for p in export["parts"]} <= {"done", "skipped"}
    assert export["parts"][-1]["kind"] == "compose"
    upload = next(j for j in client.get("/api/me/jobs").json() if j["kind"] == "youtube_upload")
    upload = client.get(f"/api/me/jobs/{upload['id']}").json()
    assert [(p["key"], p["kind"], p["status"]) for p in upload["parts"]] == [("upload", "youtube", "done")]
