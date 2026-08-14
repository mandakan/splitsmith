"""End-to-end tests for the Coach HTTP endpoints (issue #161).

Bootstraps a minimal project + an audit JSON, then exercises GET /coach,
POST /coach/reclassify, and PATCH /shots/{n}/coach. Mirrors the test
style in ``test_ui_server.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _bootstrap(tmp_path: Path) -> tuple[TestClient, Path, str]:
    """Returns ``(client, audit_file, url_base)`` -- url_base is the
    ``/api/matches/{match_id}`` prefix that every shooter-scoped URL
    in this module needs after Tier 1 step 3 of doc 10."""
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Coach Match")
    project = MatchProject.load(shooter_root)
    project.competitor_name = "Tester"
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
        )
    ]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "stage1.json"
    payload = {
        "stage_number": 1,
        "stage_name": "K-vallen",
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
            {"shot_number": 2, "ms_after_beep": 1800, "source": "detected"},  # 0.30 -> split
            {"shot_number": 3, "ms_after_beep": 2700, "source": "detected"},  # 0.90 -> transition
            {"shot_number": 4, "ms_after_beep": 5300, "source": "detected"},  # 2.60 -> movement
        ],
    }
    audit_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    app = create_app(project_root=root, project_name="Coach Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), audit_file, f"/api/matches/{match_id}"


def _read(audit_file: Path) -> dict[str, Any]:
    return json.loads(audit_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_coach_backfills_classes_on_first_read(tmp_path: Path) -> None:
    """#775: a legacy audit doc with unclassified shots heals on first
    read - the response carries auto classes and the doc is persisted."""
    client, audit_file, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_number"] == 1
    assert body["beep_time"] == 5.0
    assert len(body["shots"]) == 4

    assert [s["interval_class"] for s in body["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    for s in body["shots"]:
        assert s["interval_class_source"] == "auto"
        assert s["stale"] is False
        assert s["improvement_flag"] is False

    # The heal is persisted, silently (no new audit event kinds).
    stored = _read(audit_file)
    assert [s["interval_class"] for s in stored["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]
    assert not any(e.get("kind") == "coach_reclassify" for e in stored.get("audit_events") or [])

    # time_absolute = beep_time + ms/1000 so the SPA can seek videos.
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)
    # First shot's "split" is the draw.
    assert body["shots"][0]["split"] == pytest.approx(1.5)
    assert body["shots"][1]["split"] == pytest.approx(0.3)
    # Reload-hint flag fires on the long gap.
    assert body["shots"][3]["reload_hint"] is True


def test_get_coach_heal_survives_slim_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A local install without the ``[hosted]`` extra has no sqlalchemy,
    so the heal-persist branch must not import ``splitsmith.db``
    unguarded - it 500ed with ModuleNotFoundError on a released local
    install (user report 2026-08-10). Poisoning ``sys.modules`` makes any ``splitsmith.db``
    import raise, simulating the slim install; the GET must still serve
    and persist the heal."""
    client, audit_file, base = _bootstrap(tmp_path)
    monkeypatch.setitem(sys.modules, "splitsmith.db", None)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    stored = _read(audit_file)
    assert [s["interval_class"] for s in stored["shots"]] == [
        "first_shot",
        "split",
        "transition",
        "movement",
    ]


def test_get_coach_does_not_write_when_already_classified(tmp_path: Path) -> None:
    """#775: once a first GET has healed a bootstrap doc, a second GET
    over an already-fully-classified stage must not touch the file -
    ``needs_backfill`` is False, so there is nothing to persist."""
    client, audit_file, base = _bootstrap(tmp_path)
    first = client.get(f"{base}/shooters/me/stages/1/coach")
    assert first.status_code == 200, first.text
    before = audit_file.read_bytes()

    second = client.get(f"{base}/shooters/me/stages/1/coach")
    assert second.status_code == 200, second.text
    after = audit_file.read_bytes()
    assert after == before


def test_get_coach_returns_videos_with_beep_in_clip(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "videos" in body
    assert len(body["videos"]) == 1
    primary_entry = body["videos"][0]
    assert primary_entry["role"] == "primary"
    # No trimmed clip on disk in the bootstrap; beep_in_clip == beep in
    # source so clip coords match source coords for this fixture.
    assert primary_entry["beep_in_clip"] == pytest.approx(5.0)


def test_get_coach_clamps_beep_to_pre_buffer_when_trimmed(tmp_path: Path) -> None:
    """Beep at 8 s in source + a trimmed clip on disk -> the SPA must
    seek inside the trimmed clip, where the beep sits at min(8, 5)=5 s.
    All shot ``time_absolute`` values follow the same anchor.
    """
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Trimmed Match")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-vallen",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=8.0)],
        )
    ]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "K-vallen",
                "shots": [
                    {"shot_number": 1, "ms_after_beep": 1500, "source": "detected"},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    trimmed_dir = project.trimmed_path(shooter_root)
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    # Reload so the model validator stamps stage_number on the video; the
    # server does the same on load, so video_id must match what it computes.
    stamped = MatchProject.load(shooter_root)
    primary_id = stamped.stages[0].videos[0].video_id
    (trimmed_dir / f"stage1_cam_{primary_id}_trimmed.mp4").write_bytes(b"not really a video, but non-empty")

    app = create_app(project_root=root, project_name="Trimmed Match")
    client = TestClient(app)
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    base = f"/api/matches/{match_id}"
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Default trim_pre_buffer_seconds is 5.0; beep clamps to that.
    assert body["beep_time"] == pytest.approx(5.0)
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(5.0)
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)


def _bootstrap_legacy_trim(tmp_path: Path, *, stage_numbers: tuple[int, ...]) -> tuple[TestClient, str]:
    """Project(s) with beep at 8 s and ONLY a pre-take-spec legacy-keyed
    trim (path-only video_id) + params sidecar (pre_buffer 3.0) on disk
    for stage 1. Two ``stage_numbers`` share one source path to model a
    multi-stage single take (ambiguous registration)."""
    from splitsmith.ui import audio as audio_helpers
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Legacy Trim Match")
    project = MatchProject.load(shooter_root)
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=30.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=8.0)],
        )
        for n in stage_numbers
    ]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "stage_name": "S1",
                "shots": [{"shot_number": 1, "ms_after_beep": 1500, "source": "detected"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    stamped = MatchProject.load(shooter_root)
    video = stamped.stages[0].videos[0]
    legacy_id = audio_helpers.legacy_video_id(video)
    assert legacy_id != video.video_id  # fixture must exercise the divergence
    trimmed_dir = stamped.trimmed_path(shooter_root)
    trimmed_dir.mkdir(parents=True, exist_ok=True)
    (trimmed_dir / f"stage1_cam_{legacy_id}_trimmed.mp4").write_bytes(b"legacy trim bytes")
    (trimmed_dir / f"stage1_cam_{legacy_id}_trimmed.params.json").write_text(
        json.dumps(
            {
                "beep_time": 8.0,
                "stage_time_seconds": 30.0,
                "pre_buffer_seconds": 3.0,
                "post_buffer_seconds": 5.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    app = create_app(project_root=root, project_name="Legacy Trim Match")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}"


def test_get_coach_anchors_on_legacy_trim_when_unambiguous(tmp_path: Path) -> None:
    """Regression (take-spec video_id change): stream_video serves the
    legacy-keyed trim via the read fallback, so the beep anchor must be
    trim-based too - and use the sidecar's pre_buffer (3.0), not the
    project default (5.0). Anchor/bytes mismatch offsets every marker
    by beep - pre_buffer."""
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # min(beep 8.0, sidecar pre_buffer 3.0) inside the legacy trim.
    assert body["beep_time"] == pytest.approx(3.0)
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(3.0)
    assert body["shots"][0]["time_absolute"] == pytest.approx(3.0 + 1.5)


def test_get_coach_stays_source_anchored_when_legacy_trim_ambiguous(tmp_path: Path) -> None:
    """Same path registered on two stages: the read fallback refuses the
    legacy trim (its window belongs to an unknown stage), stream_video
    serves the source, and the anchor stays source-based to match."""
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1, 2))
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["beep_time"] == pytest.approx(8.0)
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(8.0)
    # A refused trim must label source too - a trim file exists on disk
    # here, so "file exists => trim" would pass the other kind tests but
    # mislabel this one and pin the SPA to a clip stream_video refuses.
    assert body["videos"][0]["kind"] == "source"
    assert body["shots"][0]["time_absolute"] == pytest.approx(8.0 + 1.5)


def test_get_coach_entry_kind_source_without_trim(tmp_path: Path) -> None:
    """No trim on disk: the entry pins kind=source, matching the bytes
    stream_video would serve for kind=auto."""
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    assert resp.json()["videos"][0]["kind"] == "source"


def test_get_coach_entry_kind_trim_with_trim_on_disk(tmp_path: Path) -> None:
    """Trim + params sidecar on disk: kind=trim rides with the trim-based
    beep_in_clip, so the SPA can pin the exact clip the anchor was
    measured against."""
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["videos"][0]["kind"] == "trim"
    assert body["videos"][0]["beep_in_clip"] == pytest.approx(3.0)


def test_get_stage_distributions(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/stages/1/coach/distributions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_number"] == 1
    classes = {d["interval_class"] for d in body["distributions"]}
    assert classes == {"split", "transition", "movement", "reload"}
    by_class = {d["interval_class"]: d for d in body["distributions"]}
    assert by_class["split"]["count"] == 1
    assert by_class["transition"]["count"] == 1
    assert by_class["movement"]["count"] == 1
    assert by_class["reload"]["count"] == 0
    assert body["first_shot_s"] == pytest.approx(1.5)


def test_get_match_distributions_aggregates(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.get(f"{base}/shooters/me/coach/distributions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stage_count"] == 1
    by_class = {d["interval_class"]: d for d in body["distributions"]}
    assert by_class["split"]["count"] == 1
    assert by_class["transition"]["count"] == 1
    assert body["first_shot_seconds"] == pytest.approx([1.5])


def test_get_coach_returns_null_when_no_audit(tmp_path: Path) -> None:
    """No audit JSON yet -> 200 null. "Stage exists but isn't audited"
    is a normal pre-audit state and shouldn't surface as a failed
    request in DevTools. 404 is reserved for unknown stage numbers."""
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Empty")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="x", time_seconds=10.0, videos=[])]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Empty")
    client = TestClient(app)
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    base = f"/api/matches/{match_id}"
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200
    assert resp.json() is None


# ---------------------------------------------------------------------------
# Reclassify
# ---------------------------------------------------------------------------


def test_reclassify_persists_auto_classes(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)

    resp = client.post(f"{base}/shooters/me/stages/1/coach/reclassify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    classes = [s["interval_class"] for s in body["shots"]]
    assert classes == ["first_shot", "split", "transition", "movement"]
    assert all(s["interval_class_source"] == "auto" for s in body["shots"])
    assert all(s["stale"] is False for s in body["shots"])

    # Persisted on disk.
    saved = _read(audit_file)
    persisted = [s["interval_class"] for s in saved["shots"]]
    assert persisted == ["first_shot", "split", "transition", "movement"]
    # audit_events appended.
    events = saved.get("audit_events", [])
    assert any(e.get("kind") == "coach_reclassify" for e in events)


def test_reclassify_preserves_manual(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)

    # Set a manual override on shot 4 first.
    resp = client.patch(
        f"{base}/shooters/me/stages/1/shots/4/coach",
        json={"interval_class": "reload", "interval_class_source": "manual"},
    )
    assert resp.status_code == 200, resp.text

    # Reclassify -- should leave shot 4 alone.
    resp = client.post(f"{base}/shooters/me/stages/1/coach/reclassify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shots"][3]["interval_class"] == "reload"
    assert body["shots"][3]["interval_class_source"] == "manual"
    # Auto would say "movement" -> stale flag fires on the manual entry.
    assert body["shots"][3]["stale"] is True


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_set_class_and_note_and_flag(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)

    resp = client.patch(
        f"{base}/shooters/me/stages/1/shots/2/coach",
        json={
            "interval_class": "split",
            "interval_class_source": "manual",
            "improvement_flag": True,
            "coaching_note": "second A was slow",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    s = body["shots"][1]
    assert s["interval_class"] == "split"
    assert s["interval_class_source"] == "manual"
    assert s["improvement_flag"] is True
    assert s["coaching_note"] == "second A was slow"

    saved = _read(audit_file)
    target = next(x for x in saved["shots"] if x["shot_number"] == 2)
    assert target["interval_class"] == "split"
    assert target["coaching_note"] == "second A was slow"


def test_patch_clear_class_reverts_to_auto_verdict(tmp_path: Path) -> None:
    """#775: clearing a manual class must not re-open the partial
    classification state - it drops straight back to the rule's auto
    verdict, never leaving the shot unclassified."""
    client, audit_file, base = _bootstrap(tmp_path)
    client.patch(
        f"{base}/shooters/me/stages/1/shots/2/coach",
        json={"interval_class": "reload", "interval_class_source": "manual"},
    )
    resp = client.patch(f"{base}/shooters/me/stages/1/shots/2/coach", json={"clear_class": True})
    assert resp.status_code == 200
    body = resp.json()
    # Shot 2's gap is 0.30s (<= split_max_s), so the rule's verdict is
    # "split" once the manual override is cleared.
    assert body["shots"][1]["interval_class"] == "split"
    assert body["shots"][1]["interval_class_source"] == "auto"

    saved = _read(audit_file)
    assert not any(
        s.get("ms_after_beep") is not None and s.get("interval_class") is None for s in saved["shots"]
    )


def test_patch_class_without_source_rejected(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.patch(
        f"{base}/shooters/me/stages/1/shots/2/coach",
        json={"interval_class": "split"},
    )
    assert resp.status_code == 400


def test_patch_unknown_shot_404(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.patch(
        f"{base}/shooters/me/stages/1/shots/999/coach",
        json={"improvement_flag": True},
    )
    assert resp.status_code == 404


def test_patch_clear_note(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    client.patch(
        f"{base}/shooters/me/stages/1/shots/2/coach",
        json={"coaching_note": "old note"},
    )
    resp = client.patch(f"{base}/shooters/me/stages/1/shots/2/coach", json={"clear_note": True})
    assert resp.status_code == 200
    assert resp.json()["shots"][1]["coaching_note"] is None


def test_patch_emits_audit_event(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    client.patch(
        f"{base}/shooters/me/stages/1/shots/2/coach",
        json={"improvement_flag": True, "coaching_note": "fix me"},
    )
    saved = _read(audit_file)
    events = saved.get("audit_events", [])
    coach_events = [e for e in events if e.get("kind") == "coach_patch"]
    assert len(coach_events) == 1
    assert coach_events[0]["payload"]["shot_number"] == 2


# ---------------------------------------------------------------------------
# Stale recompute on Audit-style timestamp drift
# ---------------------------------------------------------------------------


def test_stale_after_audit_edit(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    # First persist auto classes.
    client.post(f"{base}/shooters/me/stages/1/coach/reclassify")

    # Simulate an Audit-side timestamp move: shot 2 drifts from 0.30 -> 0.70 s gap.
    saved = _read(audit_file)
    for s in saved["shots"]:
        if s["shot_number"] == 2:
            s["ms_after_beep"] = 2200  # 1500 + 700 ms
    audit_file.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")

    # GET surfaces stale=True; the stored class is "split" but the rule
    # would now say "transition".
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 200
    body = resp.json()
    moved = body["shots"][1]
    assert moved["interval_class"] == "split"
    assert moved["stale"] is True

    # Reclassify clears stale.
    resp = client.post(f"{base}/shooters/me/stages/1/coach/reclassify")
    body = resp.json()
    moved = body["shots"][1]
    assert moved["interval_class"] == "transition"
    assert moved["stale"] is False
