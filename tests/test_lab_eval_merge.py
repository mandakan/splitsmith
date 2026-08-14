"""Slug-scoped eval must merge into a same-config cached run.

A full Validate run is expensive (~10 min); labeling one fixture
triggers a scoped eval and must not clobber it. Same config_hash ->
merge (scoped fixtures replace/extend the cached universe); different
config -> replace, as before.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith import ensemble as ensemble_module
from splitsmith import lab as lab_module
from splitsmith import match_model
from splitsmith.lab import core as lab_core
from splitsmith.ui.server import create_app


def _candidate(number: int, t: float, *, truth: int = 1, kept: bool = True) -> lab_core.EvalCandidate:
    """A single candidate that clears the default consensus (3-of-3)."""
    return lab_core.EvalCandidate(
        candidate_number=number,
        time=t,
        ms_after_beep=int(t * 1000),
        confidence=0.9,
        peak_amplitude=0.5,
        score_c=0.9,
        clap_diff=0.5,
        gunshot_prob=0.9,
        vote_a=1,
        vote_b=1,
        vote_c=1,
        vote_total=3,
        apriori_boost=0.0,
        ensemble_score=3.0,
        kept=kept,
        truth=truth,
    )


def _metrics(n_truth: int, n_kept: int) -> lab_core.EvalFixtureMetrics:
    tp = min(n_truth, n_kept)
    return lab_core.EvalFixtureMetrics(
        n_truth=n_truth,
        n_kept=n_kept,
        true_positives=tp,
        false_positives=n_kept - tp,
        false_negatives=n_truth - tp,
        precision=1.0 if n_kept else 0.0,
        recall=1.0 if n_truth else 0.0,
        f1=1.0 if n_truth and n_kept else 0.0,
        voter_recall={"vote_a": 1.0, "vote_b": 1.0, "vote_c": 1.0},
    )


def _fixture(slug: str, *, t: float) -> lab_core.EvalFixture:
    cand = _candidate(1, t)
    return lab_core.EvalFixture(
        slug=slug,
        audit_path=f"/fixtures/{slug}.json",
        audio_path=f"/fixtures/{slug}.wav",
        candidates=[cand],
        truth_times=[t],
        metrics=_metrics(1, 1),
        audit_mtime=0.0,
    )


def _canned_run(fixtures: list[lab_core.EvalFixture], config: lab_core.EvalConfig) -> lab_core.EvalRun:
    """Build an ``EvalRun`` the way ``run_eval`` would, minus the model calls.

    Uses the real config hashing so the "same config -> same hash"
    identity the merge logic relies on holds exactly as it would in
    production.
    """
    universe = lab_core.EvalUniverse(
        fixtures=fixtures,
        voter_a_floor=0.5,
        voter_b_threshold=0.4,
        voter_c_threshold=0.5,
        tolerance_ms=config.tolerance_ms,
    )
    n_truth = sum(f.metrics.n_truth for f in fixtures)
    n_kept = sum(f.metrics.n_kept for f in fixtures)
    tp = sum(f.metrics.true_positives for f in fixtures)
    fp = sum(f.metrics.false_positives for f in fixtures)
    fn = sum(f.metrics.false_negatives for f in fixtures)
    summary = lab_core.RunSummary(
        n_fixtures=len(fixtures),
        n_truth=n_truth,
        n_kept=n_kept,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=1.0 if n_kept else 0.0,
        recall=1.0 if n_truth else 0.0,
        f1=1.0 if n_truth and n_kept else 0.0,
    )
    return lab_core.EvalRun(
        config=config,
        summary=summary,
        universe=universe,
        config_hash=lab_core._hash_config(config),
        built_at="2026-08-14T00:00:00+00:00",
    )


def _fake_run_eval(
    runtime: Any,
    *,
    slugs: list[str] | None = None,
    config: lab_core.EvalConfig | None = None,
    progress: Any = None,
) -> lab_core.EvalRun:
    """Stand-in for ``lab.run_eval`` -- no CLAP/PANN, deterministic per call.

    Mirrors what the real thing would produce for each of the three
    calls this test drives: a full run (fixture-a, fixture-b), a
    scoped re-eval of fixture-b with fresh data, and a scoped eval of
    fixture-c under a different config.
    """
    cfg = config or lab_core.EvalConfig()
    if slugs is None:
        fixtures = [_fixture("fixture-a", t=1.0), _fixture("fixture-b", t=1.0)]
    elif slugs == ["fixture-b"]:
        fixtures = [_fixture("fixture-b", t=2.0)]
    elif slugs == ["fixture-c"]:
        fixtures = [_fixture("fixture-c", t=3.0)]
    else:
        raise AssertionError(f"unexpected slugs in test: {slugs!r}")
    return _canned_run(fixtures, cfg)


def _setup_match(root: Path) -> None:
    """Bare match + one shooter. Eval is monkeypatched so no project
    scaffolding beyond a registrable match is needed."""
    match = match_model.Match.init(root, name="Lab Eval Merge Test")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))


def _submit_and_wait(client: TestClient, match_id: str, payload: dict, *, timeout: float = 5.0) -> dict:
    resp = client.post(f"/api/matches/{match_id}/lab/eval", json=payload)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        poll = client.get(f"/api/me/jobs/{job_id}")
        assert poll.status_code == 200, poll.text
        body = poll.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            assert body["status"] == "succeeded", body
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_scoped_eval_merges_into_same_config_cached_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import bound_match_id

    root = tmp_path / "match"
    _setup_match(root)

    monkeypatch.setattr(ensemble_module.api, "load_ensemble_runtime", lambda: object())
    monkeypatch.setattr(lab_module, "run_eval", _fake_run_eval)
    monkeypatch.setattr(lab_module, "save_run", lambda run, **kw: Path("/dev/null"))

    app = create_app(project_root=root, project_name="ignored", lab_enabled=True)
    client = TestClient(app)
    match_id = bound_match_id(app)

    # 1. Full run: fixture-a, fixture-b (no slugs -> wanted_slugs is None,
    #    so this always replaces the cache outright).
    _submit_and_wait(client, match_id, {"persist": False})
    last_run = client.get("/api/lab/last-run")
    assert last_run.status_code == 200, last_run.text
    fixtures = {f["slug"]: f for f in last_run.json()["universe"]["fixtures"]}
    assert set(fixtures) == {"fixture-a", "fixture-b"}
    assert fixtures["fixture-b"]["candidates"][0]["time"] == 1.0

    # 2. Scoped re-eval of fixture-b, same (default) config -> must fold
    #    into the cached full run rather than clobber it: fixture-a
    #    survives, fixture-b's data is the fresh one.
    _submit_and_wait(client, match_id, {"slugs": ["fixture-b"], "persist": False})
    last_run = client.get("/api/lab/last-run")
    assert last_run.status_code == 200, last_run.text
    fixtures = {f["slug"]: f for f in last_run.json()["universe"]["fixtures"]}
    assert set(fixtures) == {"fixture-a", "fixture-b"}, "scoped eval clobbered the cached full run"
    assert fixtures["fixture-b"]["candidates"][0]["time"] == 2.0, "merge did not replace fixture-b's data"

    # 3. Scoped eval of fixture-c under a *different* config -> config
    #    identity changed, so this replaces the cache instead of merging.
    _submit_and_wait(
        client,
        match_id,
        {"slugs": ["fixture-c"], "config": {"consensus": 2}, "persist": False},
    )
    last_run = client.get("/api/lab/last-run")
    assert last_run.status_code == 200, last_run.text
    fixtures = {f["slug"]: f for f in last_run.json()["universe"]["fixtures"]}
    assert set(fixtures) == {"fixture-c"}, "config change should have replaced, not merged"
