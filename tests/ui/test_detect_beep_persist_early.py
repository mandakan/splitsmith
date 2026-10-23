"""Beep detection saves the beep before any trim work (#1680), and the
trim it then needs is one ``trim`` job a confirm adopts, skipped when
the beep did not move (#1678).

ffmpeg and the detector are faked; the trim fake can be held on a gate to
stand for a 4K cut that takes minutes.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from splitsmith.match_project import MatchProject
from tests.media_bytes import tiny_mp4
from tests.ui._helpers import (
    _seed_project_with_primary,
    _stub_detect,
    _wait_for_job,
    _wait_for_jobs_to_drain,
)

BEEP = 6.5


def _shooter_root(tmp_path: Path) -> Path:
    return tmp_path / "match" / "shooters" / "me"


def _primary(tmp_path: Path):  # type: ignore[no-untyped-def]
    primary = MatchProject.load(_shooter_root(tmp_path)).stages[0].primary()
    assert primary is not None
    return primary


def _prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: threading.Event | None = None
) -> list[float | None]:
    """A source on disk and a fake trim that records the beep the project
    on disk held when the cut started."""
    root = _shooter_root(tmp_path)
    project = MatchProject.load(root)
    primary = project.stages[0].primary()
    assert primary is not None
    source = project.resolve_video_path(root, primary.path).resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"FAKE_SOURCE_MP4")

    from splitsmith import trim

    seen: list[float | None] = []

    def fake_trim_video(**kwargs):  # type: ignore[no-untyped-def]
        seen.append(_primary(tmp_path).beep_time)
        if gate is not None:
            assert gate.wait(timeout=5.0), "trim gate never released"
        Path(kwargs["output_path"]).write_bytes(tiny_mp4())
        return trim.TrimResult(output_path=kwargs["output_path"], start_time=1.5, end_time=20.0)

    from splitsmith.ui import audio as audio_helpers

    monkeypatch.setattr(audio_helpers, "ensure_video_audit_wav", lambda *a, **k: None)
    monkeypatch.setattr(trim, "select_audit_encoder", lambda *a, **k: "libx264")
    monkeypatch.setattr(trim, "trim_video", fake_trim_video)
    monkeypatch.setattr(
        trim, "transcode_web_trim", lambda i, o, c, **k: Path(o).write_bytes(tiny_mp4(duration_s=2.0))
    )
    return seen


def _kinds(client) -> list[str]:  # type: ignore[no-untyped-def]
    return [j["kind"] for j in client.get("/api/me/jobs").json()]


def test_the_beep_is_in_the_project_before_the_trim_starts(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    seen = _prepare(tmp_path, monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)

    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"
    _wait_for_jobs_to_drain(client)

    assert seen == [pytest.approx(BEEP)], "the trim started before the beep was saved"
    assert _primary(tmp_path).processed["trim"] is True


def test_a_confirm_during_the_trim_adopts_it_and_chains_detection(tmp_path: Path, monkeypatch) -> None:
    """The beep shows while the trim is still cutting; confirming it then
    neither starts a second cut nor loses the shot detection."""
    client, _ = _seed_project_with_primary(tmp_path)
    gate = threading.Event()
    seen = _prepare(tmp_path, monkeypatch, gate)
    _stub_detect(monkeypatch, beep_time=BEEP)
    shot_detects: list[dict] = []
    client.app.state.splitsmith_state.jobs.bodies.register(
        "shot_detect", lambda handle, **kw: shot_detects.append(kw)
    )

    client.post("/api/shooters/me/stages/1/detect-beep")
    deadline = time.time() + 5.0
    while not seen and time.time() < deadline:
        time.sleep(0.02)
    assert seen, "the trim never started"
    # The cut is held: the beep is already there to review.
    primary = _primary(tmp_path)
    assert primary.beep_time == pytest.approx(BEEP)
    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep/review", json={"reviewed": True}
    )
    assert resp.status_code == 200, resp.text
    gate.set()
    jobs = _wait_for_jobs_to_drain(client)

    assert len(seen) == 1, "the confirm started a second cut"
    assert [j["kind"] for j in jobs].count("trim") == 1
    assert len(shot_detects) == 1


def test_a_re_detection_that_finds_the_same_beep_keeps_the_trim(tmp_path: Path, monkeypatch) -> None:
    """#1678: confirming the primary re-detects the stage's other cameras;
    one whose beep comes back where it was must not be trimmed again."""
    client, _ = _seed_project_with_primary(tmp_path)
    seen = _prepare(tmp_path, monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)
    assert len(seen) == 1

    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"
    jobs = _wait_for_jobs_to_drain(client)

    assert len(seen) == 1, "an unchanged beep was trimmed again"
    assert [j["kind"] for j in jobs].count("trim") == 1
    assert _primary(tmp_path).processed["trim"] is True


def test_a_re_detection_that_moves_the_beep_re_cuts(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_project_with_primary(tmp_path)
    seen = _prepare(tmp_path, monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)

    _stub_detect(monkeypatch, beep_time=BEEP + 0.25)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)

    assert seen == [pytest.approx(BEEP), pytest.approx(BEEP + 0.25)]
    assert _primary(tmp_path).processed["trim"] is True


def _wait_for(predicate, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.time() + 5.0
    while not predicate() and time.time() < deadline:
        time.sleep(0.02)
    assert predicate(), what


def _trim_file(tmp_path: Path) -> Path:
    from splitsmith.ui.audio import trimmed_video_path

    root = _shooter_root(tmp_path)
    return trimmed_video_path(root, 1, _primary(tmp_path), project=MatchProject.load(root))


def _trim_params(tmp_path: Path) -> dict:
    from splitsmith.ui.audio import trim_params_path

    return json.loads(trim_params_path(_trim_file(tmp_path)).read_text(encoding="utf-8"))


def _record_publishes(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The beep each published trim was cut for (its params at the push: a
    hosted trim's storage copy is its params sidecar and rendition, #1683)."""
    from splitsmith.ui import audio as audio_helpers

    published: list[float] = []
    real_push = audio_helpers._push_trim_params

    def push(project, local_mp4):  # type: ignore[no-untyped-def]
        params = audio_helpers.trim_params_path(local_mp4)
        published.append(json.loads(params.read_text(encoding="utf-8"))["beep_time"])
        real_push(project, local_mp4)

    monkeypatch.setattr(audio_helpers, "_push_trim_params", push)
    return published


def _record_shot_detects(client) -> list[dict]:  # type: ignore[no-untyped-def]
    shot_detects: list[dict] = []
    client.app.state.splitsmith_state.jobs.bodies.register(
        "shot_detect", lambda handle, **kw: shot_detects.append(kw)
    )
    return shot_detects


def test_an_override_during_the_cut_ends_trimmed_for_the_new_beep(tmp_path: Path, monkeypatch) -> None:
    """The beep is visible while its trim cuts, so it can be moved then. The
    override adopts the running trim (it dedupes on the trim kind); that trim
    must not publish its old window and must cut again for the new beep, then
    chain detection."""
    client, _ = _seed_project_with_primary(tmp_path)
    gate = threading.Event()
    seen = _prepare(tmp_path, monkeypatch, gate)
    published = _record_publishes(monkeypatch)
    shot_detects = _record_shot_detects(client)
    _stub_detect(monkeypatch, beep_time=BEEP, confidence=1.0)  # auto-trusted

    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for(lambda: bool(seen), "the trim never started")
    primary = _primary(tmp_path)
    resp = client.post(f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep", json={"beep_time": 7.0})
    assert resp.status_code == 200, resp.text
    gate.set()
    _wait_for_jobs_to_drain(client)

    assert seen == [pytest.approx(BEEP), pytest.approx(7.0)]
    assert published == [pytest.approx(7.0)], "the cut for the old beep was published"
    assert _primary(tmp_path).processed["trim"] is True
    assert _trim_params(tmp_path)["beep_time"] == pytest.approx(7.0)
    assert len(shot_detects) == 1


def test_a_re_detection_during_the_cut_ends_trimmed_for_the_new_beep(tmp_path: Path, monkeypatch) -> None:
    """A secondary re-detected right after the primary's confirm, while its
    first trim still cuts: the same adoption, the same outcome."""
    client, _ = _seed_project_with_primary(tmp_path)
    gate = threading.Event()
    seen = _prepare(tmp_path, monkeypatch, gate)
    published = _record_publishes(monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)

    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for(lambda: bool(seen), "the trim never started")
    _stub_detect(monkeypatch, beep_time=BEEP + 0.25)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"
    gate.set()
    _wait_for_jobs_to_drain(client)

    assert seen == [pytest.approx(BEEP), pytest.approx(BEEP + 0.25)]
    assert published == [pytest.approx(BEEP + 0.25)]
    assert _primary(tmp_path).processed["trim"] is True
    assert _trim_params(tmp_path)["beep_time"] == pytest.approx(BEEP + 0.25)


def test_a_re_detection_that_moves_the_beep_retires_the_old_trim_at_once(tmp_path: Path, monkeypatch) -> None:
    """Between the saved beep and its new trim, nothing may present the old
    cut as the video's trim: the flag is cleared, the files go, and the
    storage copies are dropped (a hosted reader keys on their existence)."""
    from splitsmith.ui import audio as audio_helpers

    client, _ = _seed_project_with_primary(tmp_path)
    _prepare(tmp_path, monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)
    assert _trim_file(tmp_path).exists()

    # Hold the follow-up trim off entirely: observe the state the re-detection leaves.
    queued: list[dict] = []
    client.app.state.splitsmith_state.jobs.bodies.register("trim", lambda handle, **kw: queued.append(kw))
    invalidations: list[dict] = []
    real_invalidate = audio_helpers.invalidate_video_audit_trim

    def spy(*a, **kw):  # type: ignore[no-untyped-def]
        invalidations.append(kw)
        real_invalidate(*a, **kw)

    monkeypatch.setattr(audio_helpers, "invalidate_video_audit_trim", spy)
    _stub_detect(monkeypatch, beep_time=BEEP + 0.25)
    job = client.post("/api/shooters/me/stages/1/detect-beep").json()
    assert _wait_for_job(client, job["id"])["status"] == "succeeded"
    _wait_for_jobs_to_drain(client)

    assert len(queued) == 1
    assert _primary(tmp_path).processed["trim"] is False
    assert not _trim_file(tmp_path).exists()
    assert [kw.get("stored") for kw in invalidations] == [True]


def test_a_stage_time_changed_since_the_cut_re_cuts(tmp_path: Path, monkeypatch) -> None:
    """A trim is current by its params, not by the beep alone: a stage time
    edited after the cut leaves ``processed.trim`` set, and a re-detection
    finding the same beep must still cut the new window."""
    client, _ = _seed_project_with_primary(tmp_path)
    seen = _prepare(tmp_path, monkeypatch)
    _stub_detect(monkeypatch, beep_time=BEEP)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)
    resp = client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 25.0})
    assert resp.status_code == 200, resp.text
    assert _primary(tmp_path).processed["trim"] is True

    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)

    assert len(seen) == 2
    assert _trim_params(tmp_path)["stage_time_seconds"] == pytest.approx(25.0)


def test_a_confirm_saved_just_before_the_trim_saves_still_chains_detection(
    tmp_path: Path, monkeypatch
) -> None:
    """The trim job reads ``beep_reviewed`` from the document it writes, so a
    confirm that wins the race to the store is not lost."""
    import splitsmith.ui.server as server_mod

    client, _ = _seed_project_with_primary(tmp_path)
    _prepare(tmp_path, monkeypatch)
    shot_detects = _record_shot_detects(client)
    _stub_detect(monkeypatch, beep_time=BEEP)
    real_update = server_mod._update_project

    def confirm_first(state, slug, apply):  # type: ignore[no-untyped-def]
        if apply.__name__ != "_apply_beep":  # the trim job's write
            root = _shooter_root(tmp_path)
            project = MatchProject.load(root)
            project.stages[0].primary().beep_reviewed = True  # type: ignore[union-attr]
            project.save(root)
        return real_update(state, slug, apply)

    monkeypatch.setattr(server_mod, "_update_project", confirm_first)
    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for_jobs_to_drain(client)

    assert _primary(tmp_path).processed["trim"] is True
    assert len(shot_detects) == 1


@pytest.mark.parametrize("moment", ["before_its_save", "after_its_save"])
def test_an_override_late_in_the_trim_job_still_ends_trimmed(
    tmp_path: Path, monkeypatch, moment: str
) -> None:
    """The cut is published and the job is saving or chaining when the beep
    moves. The override still adopts the job (it is active), so the job must
    notice on its own, drop the stored copies of its cut and trim again."""
    import splitsmith.ui.server as server_mod
    from splitsmith.ui import audio as audio_helpers

    client, _ = _seed_project_with_primary(tmp_path)
    seen = _prepare(tmp_path, monkeypatch)
    shot_detects = _record_shot_detects(client)
    _stub_detect(monkeypatch, beep_time=BEEP, confidence=1.0)  # auto-trusted
    reached, release = threading.Event(), threading.Event()
    real_update = server_mod._update_project

    def hold(state, slug, apply):  # type: ignore[no-untyped-def]
        first_trim_save = apply.__name__ == "_apply_trim" and not reached.is_set()
        if first_trim_save and moment == "before_its_save":
            reached.set()
            assert release.wait(timeout=5.0)
        version = real_update(state, slug, apply)
        if first_trim_save and moment == "after_its_save":
            reached.set()
            assert release.wait(timeout=5.0)
        return version

    stored_drops: list[bool] = []
    real_invalidate = audio_helpers.invalidate_video_audit_trim

    def spy(*a, **kw):  # type: ignore[no-untyped-def]
        if kw.get("stored"):
            stored_drops.append(True)
        real_invalidate(*a, **kw)

    monkeypatch.setattr(server_mod, "_update_project", hold)
    monkeypatch.setattr(audio_helpers, "invalidate_video_audit_trim", spy)
    client.post("/api/shooters/me/stages/1/detect-beep")
    assert reached.wait(timeout=5.0), "the trim job never saved"
    primary = _primary(tmp_path)
    resp = client.post(f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep", json={"beep_time": 7.0})
    assert resp.status_code == 200, resp.text
    stored_drops.clear()  # the first detection's own (no beep before it)
    release.set()
    _wait_for_jobs_to_drain(client)

    assert seen == [pytest.approx(BEEP), pytest.approx(7.0)]
    assert _primary(tmp_path).processed["trim"] is True
    assert _trim_params(tmp_path)["beep_time"] == pytest.approx(7.0)
    assert stored_drops == [True], "the stored copies of the old cut were left"
    assert len(shot_detects) >= 1


def test_a_beep_cleared_during_the_cut_queues_no_trim(tmp_path: Path, monkeypatch) -> None:
    """Clearing the beep leaves nothing to trim: no second trim job, which
    would fail on the missing beep."""
    client, _ = _seed_project_with_primary(tmp_path)
    gate = threading.Event()
    seen = _prepare(tmp_path, monkeypatch, gate)
    _stub_detect(monkeypatch, beep_time=BEEP)

    client.post("/api/shooters/me/stages/1/detect-beep")
    _wait_for(lambda: bool(seen), "the trim never started")
    primary = _primary(tmp_path)
    resp = client.post(f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep", json={"beep_time": None})
    assert resp.status_code == 200, resp.text
    gate.set()
    jobs = _wait_for_jobs_to_drain(client)

    assert [j["kind"] for j in jobs].count("trim") == 1
    assert [j for j in jobs if j["status"] == "failed"] == []
    assert _primary(tmp_path).processed["trim"] is False
