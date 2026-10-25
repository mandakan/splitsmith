"""The export progress routes (spec 2026-10-25, plan Task 7)."""

from __future__ import annotations

from typing import Any

from tests.test_hosted_grid_stage_jobs import (
    _grid,
    _stage_jobs,
    hosted_client,
    hosted_client_with_match,
    hosted_db,
    hosted_grid,
    inline_deferrer,
    two_stages,
)

__all__ = [
    "hosted_client",
    "hosted_client_with_match",
    "hosted_db",
    "hosted_grid",
    "inline_deferrer",
    "two_stages",
]


def _start(client: Any, match_id: str, slug: str) -> dict:
    r = client.post(
        f"/api/matches/{match_id}/match/compare-export", json={"stage_numbers": [1, 2], "audio_from": slug}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_active_shows_a_grid_with_its_stage_rows(two_stages: Any, inline_deferrer: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True
    grid = _start(client, match_id, slug)
    body = client.get("/api/me/exports/active").json()
    assert body["limit"] == 1
    (export,) = body["exports"]
    assert export["export_id"] == grid["export_id"]
    assert (export["kind"], export["label"], export["status"]) == ("compare-grid", "Compare grid", "running")
    labels = [row["label"] for row in export["rows"]]
    assert labels[:2] == ["Stage 1", "Stage 2"]  # the stage jobs, queued, then the grid job
    assert all(row["status"] == "waiting" for row in export["rows"])
    assert export["time_left_s"] > 0 and export["approximate"] is True


def test_active_lists_exports_across_matches(two_stages: Any, inline_deferrer: Any) -> None:
    """The route is the account's, not a match's: the Export page of any
    match learns what is running and where."""
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True
    _start(client, match_id, slug)
    for path in ("/api/me/exports/active", f"/api/matches/{match_id}/me/exports/active"):
        (export,) = client.get(path).json()["exports"]
        assert export["match_id"] == match_id


def test_cancel_cancels_every_part(two_stages: Any, inline_deferrer: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True
    grid = _start(client, match_id, slug)
    view = client.post(f"/api/me/exports/{grid['export_id']}/cancel").json()
    assert view["status"] == "cancelled"
    jobs = client.get(f"/api/matches/{match_id}/me/jobs").json()
    assert {j["status"] for j in jobs if j["export_id"] == grid["export_id"]} == {"cancelled"}
    assert client.get("/api/me/exports/active").json()["exports"] == []


def test_retry_a_failed_stage_requeues_the_grid_in_the_same_export(two_stages: Any) -> None:
    client, _storage, match_id, slug, _calls, failing, _raising = two_stages
    failing.add(2)
    grid = _grid(client, match_id, slug, [1, 2])
    stage_two = next(j for j in _stage_jobs(client, match_id) if j["stage_number"] == 2)
    view = client.get(f"/api/me/exports/{grid['export_id']}").json()
    row = next(r for r in view["rows"] if r["job_id"] == stage_two["id"])
    assert row["status"] == "failed" and row["retryable"] is True

    failing.clear()
    retried = client.post(f"/api/me/exports/{grid['export_id']}/retry", json={"job_id": stage_two["id"]})
    assert retried.status_code == 200, retried.text
    jobs = client.get(f"/api/matches/{match_id}/me/jobs").json()
    grids = [j for j in jobs if j["kind"] == "compare-grid" and j["export_id"] == grid["export_id"]]
    assert len(grids) == 2  # the first and the re-run, one export


def test_retry_refuses_a_job_that_did_not_fail(two_stages: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    grid = _grid(client, match_id, slug, [1, 2])
    r = client.post(f"/api/me/exports/{grid['export_id']}/retry", json={"job_id": grid["id"]})
    assert r.status_code == 409


def test_another_users_export_is_404(two_stages: Any) -> None:
    client, *_ = two_stages
    assert client.get("/api/me/exports/not-mine").status_code == 404
    assert client.post("/api/me/exports/not-mine/cancel").status_code == 404


def test_active_reads_history_once(two_stages: Any, inline_deferrer: Any, monkeypatch: Any) -> None:
    client, _storage, match_id, slug, *_ = two_stages
    inline_deferrer["hold"] = True
    _start(client, match_id, slug)
    from splitsmith.db.job_backend import PostgresJobBackend

    calls: list[int] = []
    original = PostgresJobBackend.recent_export_jobs

    async def counting(self: Any, limit: int = 200) -> Any:
        calls.append(limit)
        return await original(self, limit)

    monkeypatch.setattr(PostgresJobBackend, "recent_export_jobs", counting)
    client.get("/api/me/exports/active")
    assert calls == [200]


def test_rows_follow_the_order_parts_run_in() -> None:
    """A grid's planning comes before its stage jobs, its join after them."""
    from datetime import UTC, datetime, timedelta

    from splitsmith.ui.exports_progress_api import build_view
    from splitsmith.ui.jobs import Job, JobPart, JobStatus

    t0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    def job(job_id: str, kind: str, created: int, parts: list[JobPart] | None) -> Job:
        at = t0 + timedelta(seconds=created)
        return Job(
            id=job_id,
            kind=kind,
            status=JobStatus.RUNNING,
            export_id="e1",
            parts=parts,
            stage_number=created + 1 if kind == "compare-grid-stage" else None,
            created_at=at,
            updated_at=at,
        )

    jobs = [
        job("s1", "compare-grid-stage", 0, None),
        job("s2", "compare-grid-stage", 1, None),
        job(
            "g",
            "compare-grid",
            2,
            [
                JobPart(key="plan", label="Planning", kind="load", status="done"),
                JobPart(key="stages", label="Stage renders", kind="wait", status="running"),
                JobPart(key="stitch", label="Joining", kind="stitch"),
            ],
        ),
    ]
    view = build_view("e1", jobs, {}, now=t0 + timedelta(seconds=60))
    assert [(r.job_id, r.key) for r in view.rows] == [
        ("g", "plan"),
        ("s1", "job"),
        ("s2", "job"),
        ("g", "stitch"),
    ]
