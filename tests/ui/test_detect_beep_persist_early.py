"""Beep detection saves the beep before any trim work (#1680), and the
trim it then needs is one ``trim`` job a confirm adopts, skipped when
the beep did not move (#1678).

ffmpeg and the detector are faked; the trim fake can be held on a gate to
stand for a 4K cut that takes minutes.
"""

from __future__ import annotations

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


def test_a_beep_moved_during_the_cut_leaves_the_video_untrimmed(tmp_path: Path, monkeypatch) -> None:
    """With the beep visible while its trim cuts, the user can move it then:
    the cut for the old beep is not the video's trim, and detection does not
    run over it."""
    client, _ = _seed_project_with_primary(tmp_path)
    gate = threading.Event()
    seen = _prepare(tmp_path, monkeypatch, gate)
    _stub_detect(monkeypatch, beep_time=BEEP, confidence=1.0)  # auto-trusted
    shot_detects: list[dict] = []
    client.app.state.splitsmith_state.jobs.bodies.register(
        "shot_detect", lambda handle, **kw: shot_detects.append(kw)
    )

    client.post("/api/shooters/me/stages/1/detect-beep")
    deadline = time.time() + 5.0
    while not seen and time.time() < deadline:
        time.sleep(0.02)
    assert seen, "the trim never started"
    primary = _primary(tmp_path)
    resp = client.post(f"/api/shooters/me/stages/1/videos/{primary.video_id}/beep", json={"beep_time": 7.0})
    assert resp.status_code == 200, resp.text
    gate.set()
    _wait_for_jobs_to_drain(client)

    assert _primary(tmp_path).processed["trim"] is False
    assert shot_detects == []
