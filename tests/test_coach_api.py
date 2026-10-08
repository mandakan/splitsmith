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

from splitsmith.audit_revision import audit_revision
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _bootstrap(
    tmp_path: Path, *, division: str | None = None, pinned_competitor: int | None = None
) -> tuple[TestClient, Path, str]:
    """Returns ``(client, audit_file, url_base)`` -- url_base is the
    ``/api/matches/{match_id}`` prefix that every shooter-scoped URL
    in this module needs after Tier 1 step 3 of doc 10.

    ``pinned_competitor`` pins that competitor of the scoreboard fixture
    match (22/27190) and drops the match file into the shooter's
    ``scoreboard/``, with no stored division: an older project whose
    division only its scoreboard files know."""
    import shutil

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
    project.competitor_division = division
    if pinned_competitor is not None:
        project.scoreboard_match_id = "27190"
        project.scoreboard_content_type = 22
        project.selected_competitor_id = pinned_competitor
        (shooter_root / "scoreboard").mkdir(exist_ok=True)
        fixture = Path(__file__).parent / "fixtures" / "scoreboard" / "match_22_27190.json"
        shutil.copy(fixture, shooter_root / "scoreboard" / "match.json")
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
            {"shot_number": 3, "ms_after_beep": 3300, "source": "detected"},  # 1.50 -> transition
            # 2.60 -> hinted: the first GET seeds a reload region over the
            # gap, so the heal says reload rather than movement.
            {"shot_number": 4, "ms_after_beep": 5900, "source": "detected"},
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
        "reload",
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
        "reload",
    ]
    assert not any(e.get("kind") == "coach_reclassify" for e in stored.get("audit_events") or [])

    # time_absolute = beep_time + ms/1000 so the SPA can seek videos.
    assert body["shots"][0]["time_absolute"] == pytest.approx(5.0 + 1.5)
    # First shot's "split" is the draw.
    assert body["shots"][0]["split"] == pytest.approx(1.5)
    assert body["shots"][1]["split"] == pytest.approx(0.3)
    # Reload-hint flag fires on the long gap.
    assert body["shots"][3]["reload_hint"] is True
    # Each camera carries the name a viewer switches by.
    assert [v["label"] for v in body["videos"]] == ["Camera 1"]
    # And what a camera choice keys on across stages, plus the saved one.
    assert [v["mount"] for v in body["videos"]] == [None]
    assert body["compare_camera"] is None


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
        "reload",
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


def test_get_coach_entry_kind_stays_trim_locally_with_web_file(tmp_path: Path) -> None:
    """Local mode serves the full-res trim from disk; the 720p rendition
    (#1031) is for presigned hosted playback only, so its presence on disk
    must not flip the kind."""
    from splitsmith.trim import web_trim_path

    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    body = resp.json()
    assert body["videos"][0]["kind"] == "trim"
    trimmed = next(tmp_path.rglob("stage1_cam_*_trimmed.mp4"), None)
    assert trimmed is not None
    web_trim_path(trimmed).write_bytes(b"WEB")

    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.json()["videos"][0]["kind"] == "trim"


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

    # Simulate an Audit-side timestamp move: shot 2 drifts from 0.30 -> 1.40 s gap.
    saved = _read(audit_file)
    for s in saved["shots"]:
        if s["shot_number"] == 2:
            s["ms_after_beep"] = 2900  # 1500 + 1400 ms
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


def test_get_coach_carries_the_saved_compare_camera(tmp_path: Path) -> None:
    """Compare and the stage page start each shooter on their saved camera:
    the coach payload carries it, as saved through the compare-camera route."""
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.patch(f"{base}/shooters/me/compare-camera", json={"camera": "primary"})
    assert resp.status_code == 200, resp.text
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert body["compare_camera"] == "primary"


# ---------------------------------------------------------------------------
# Stage events on the coach payload (spec 2026-10-08)
# ---------------------------------------------------------------------------


def _write_shots(audit_file: Path, ms: list[int]) -> None:
    doc = json.loads(audit_file.read_text(encoding="utf-8"))
    doc["shots"] = [
        {"shot_number": i + 1, "ms_after_beep": m, "source": "detected"} for i, m in enumerate(ms)
    ]
    doc.pop("events", None)
    doc.pop("events_seeded", None)
    audit_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _po_shots_with_a_reload_gap() -> list[int]:
    quick = [1200 + i * 300 for i in range(12)]
    after = [quick[-1] + 3200 + i * 300 for i in range(4)]
    return quick + after


def test_get_coach_seeds_a_reload_proposal_once(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path, division="Production Optics")
    _write_shots(audit_file, _po_shots_with_a_reload_gap())

    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [(e["kind"], e["source"]) for e in body["events"]] == [("reload", "auto")]
    assert body["events"][0]["start"] == pytest.approx(12 * 0.3 + 0.9)  # the 12th shot, 1.2 + 11*0.3
    assert body["events"][0]["id"] == "evt-1"
    assert body["event_summary"]["reloads"] == 1
    assert body["event_summary"]["capacity_warning"] is None
    assert all(s["moving"] is False for s in body["shots"])
    assert isinstance(body["_version"], str) and len(body["_version"]) == 16

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["events_seeded"] is True
    assert len(stored["events"]) == 1

    # The user deletes the proposal; the next read does not resurrect it.
    stored["events"] = []
    audit_file.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    body2 = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert body2["events"] == []


def test_get_coach_without_a_division_seeds_from_the_hint_alone(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 4000, 4300, 7200])  # two gaps over 2.5 s
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [e["kind"] for e in body["events"]] == ["reload", "reload"]


def test_get_coach_marks_moving_shots_and_sums_the_summary(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 2500, 2800, 6000])
    doc = json.loads(audit_file.read_text(encoding="utf-8"))
    doc["events"] = [
        {"id": "evt-1", "kind": "movement", "start": 2.0, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 3.4, "end": 5.0, "source": "manual"},
        {"id": "evt-3", "kind": "reload", "start": 3.6, "end": 5.4, "source": "manual"},
    ]
    doc["events_seeded"] = True
    audit_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [s["moving"] for s in body["shots"]] == [False, False, True, True, False]
    summary = body["event_summary"]
    assert summary["moving_shots"] == 2
    assert summary["movement_s"] == pytest.approx(2.6)
    assert summary["overhang_s"] == pytest.approx(0.4)
    # The 3.2 s gap before shot 5 overlaps the reload region -> auto reload.
    assert body["shots"][4]["interval_class"] == "reload"
    assert body["shots"][4]["interval_class_source"] == "auto"
    # ...and that verdict is not "stale" against the region-blind rule.
    assert body["shots"][4]["stale"] is False


def test_get_coach_video_entries_carry_trim_and_scrub_versions(tmp_path: Path) -> None:
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    primary = body["videos"][0]
    assert primary["kind"] == "trim"
    assert isinstance(primary["trim_version"], str) and primary["trim_version"]
    assert primary["scrub_version"] is None  # no _web.mp4 beside the trim in this fixture


def test_get_coach_source_video_has_null_versions(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    primary = client.get(f"{base}/shooters/me/stages/1/coach").json()["videos"][0]
    assert primary["kind"] == "source"
    assert primary["trim_version"] is None
    assert primary["scrub_version"] is None


def test_get_coach_version_is_the_stored_docs_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_version`` names the stored doc. An owner read saves its seed, so
    it is the saved doc's; a mirror read seeds in memory only, never
    persists, and still hands back the stored doc's revision -- else the
    events PUT would 409 against it forever."""
    from splitsmith.audit_revision import audit_revision
    from splitsmith.ui import server as server_module

    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 4000])
    before = _read(audit_file)

    monkeypatch.setattr(server_module, "_is_mirror", lambda: True)
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [e["kind"] for e in body["events"]] == ["reload"]  # served in memory
    assert _read(audit_file) == before  # neither the seed nor the heal persisted
    assert body["_version"] == audit_revision(before)

    monkeypatch.setattr(server_module, "_is_mirror", lambda: False)
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    stored = _read(audit_file)
    assert stored["events_seeded"] is True
    assert body["_version"] == audit_revision(stored)


def test_get_coach_corrupt_events_are_a_422(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    doc = json.loads(audit_file.read_text(encoding="utf-8"))
    doc["events"] = [{"id": "evt-1", "kind": "nap", "start": 1.0, "end": 2.0, "source": "manual"}]
    audit_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    resp = client.get(f"{base}/shooters/me/stages/1/coach")
    assert resp.status_code == 422, resp.text
    assert "invalid events" in resp.json()["detail"]


def _po_shots_past_capacity_without_a_hinted_gap() -> list[int]:
    """20 shots, no gap over the 2.5 s hint: 14 quick, a 1.0 s gap, 6 quick.
    Production Optics holds 15 + 1, so the seeder must place a reload by
    capacity alone -- in the longest gap of the window, after shot 14."""
    first = [1200 + i * 300 for i in range(14)]
    rest = [first[-1] + 1000 + i * 300 for i in range(6)]
    return first + rest


def test_get_coach_seeds_by_division_capacity_when_no_gap_is_hinted(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path, division="Production Optics")
    shots = _po_shots_past_capacity_without_a_hinted_gap()
    _write_shots(audit_file, shots)

    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [(e["kind"], e["source"]) for e in body["events"]] == [("reload", "auto")]
    assert body["events"][0]["start"] == pytest.approx(shots[13] / 1000)
    assert body["events"][0]["end"] == pytest.approx(shots[14] / 1000)
    assert body["event_summary"]["capacity_warning"] is None

    # The user deletes the proposal: 20 shots on one magazine is over capacity.
    stored = _read(audit_file)
    stored["events"] = []
    audit_file.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    body2 = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert body2["events"] == []
    assert body2["event_summary"]["capacity_warning"] == "20 shots without a reload"


def test_get_coach_capacity_from_the_scoreboard_files_of_an_older_project(tmp_path: Path) -> None:
    """No stored division: ``competitor_division`` reads the shooter's
    dropped match file, so the route must hand it the shooter directory."""
    client, audit_file, base = _bootstrap(tmp_path, pinned_competitor=727539)  # Production Optics
    _write_shots(audit_file, _po_shots_past_capacity_without_a_hinted_gap())
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [e["kind"] for e in body["events"]] == ["reload"]


# ---------------------------------------------------------------------------
# PUT stage events (spec 2026-10-08)
# ---------------------------------------------------------------------------


def _coach(client, base: str) -> dict:
    return client.get(f"{base}/shooters/me/stages/1/coach").json()


def test_put_events_replaces_the_list_and_returns_the_coach_payload(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    before = _coach(client, base)
    events = [
        {"id": "evt-1", "kind": "movement", "start": 1.4, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "reload", "start": 2.0, "end": 3.4, "source": "manual", "note": "slow"},
    ]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events", json={"events": events, "_version": before["_version"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["events"] == events
    assert body["_version"] != before["_version"]
    assert body["event_summary"]["reloads"] == 1
    assert "shots" in body and "videos" in body

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["events"] == events
    assert stored["events_seeded"] is True
    assert stored["audit_events"][-1]["kind"] == "events_save"
    assert stored["audit_events"][-1]["payload"] == {"count": 2}

    # The served ``_version`` is the saved doc's revision, so the editor's
    # next save goes through rather than 409ing.
    assert body["_version"] == audit_revision(stored)
    again = client.put(
        f"{base}/shooters/me/stages/1/events", json={"events": events[:1], "_version": body["_version"]}
    )
    assert again.status_code == 200, again.text


def test_put_events_reclassifies_the_overlapped_gap(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    # 2.3 s gap: movement by the thresholds, but under the 2.5 s reload hint,
    # so the GET's seeder leaves it alone and the classifier sees no region yet.
    _write_shots(audit_file, [1000, 1300, 3600])
    assert _coach(client, base)["shots"][2]["interval_class"] == "movement"
    v = _coach(client, base)["_version"]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={
            "events": [{"id": "evt-1", "kind": "reload", "start": 1.6, "end": 3.0, "source": "manual"}],
            "_version": v,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["shots"][2]["interval_class"] == "reload"


def test_put_events_rejects_a_lane_overlap_naming_both(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    v = _coach(client, base)["_version"]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={
            "events": [
                {"id": "evt-1", "kind": "movement", "start": 1.0, "end": 3.0, "source": "manual"},
                {"id": "evt-2", "kind": "movement", "start": 2.5, "end": 4.0, "source": "manual"},
            ],
            "_version": v,
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "lane_overlap"
    assert "evt-1" in detail["message"] and "evt-2" in detail["message"]


def test_put_events_rejects_end_before_start(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "reload", "start": 2.0, "end": 2.0, "source": "manual"}]},
    )
    assert resp.status_code == 422


def test_put_events_stale_version_is_a_409_version_conflict(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    _coach(client, base)
    resp = client.put(
        f"{base}/shooters/me/stages/1/events", json={"events": [], "_version": "0000000000000000"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "version_conflict"


def test_put_events_with_no_shots_still_saves(tmp_path: Path) -> None:
    # Review focus 3: a movement drawn before detection ran.
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [])
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "movement", "start": 1.0, "end": 2.0, "source": "manual"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"][0]["id"] == "evt-1"
    assert resp.json()["event_summary"]["moving_shots"] == 0


def test_put_events_unknown_stage_is_404(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.put(f"{base}/shooters/me/stages/9/events", json={"events": []})
    assert resp.status_code == 404
    # The route's own 404 (the stage lookup), not a missing route's "Not Found".
    assert "no stage 9" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Every audit-doc writer classifies against the doc's regions (final review #1)
# ---------------------------------------------------------------------------


def _stage_with_a_manual_reload(tmp_path: Path) -> tuple[TestClient, Path, str]:
    """Three shots whose last gap (2.3 s) is ``movement`` by the thresholds
    and under the reload hint, so no proposal is seeded; a manual reload
    region over it makes the classifier say ``reload``. Every writer below
    must keep that verdict."""
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 3600])
    v = _coach(client, base)["_version"]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={
            "events": [{"id": "evt-1", "kind": "reload", "start": 1.6, "end": 3.0, "source": "manual"}],
            "_version": v,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["shots"][2]["interval_class"] == "reload"
    return client, audit_file, base


def _stored_class(audit_file: Path, index: int) -> str | None:
    shots = sorted(_read(audit_file)["shots"], key=lambda s: s["ms_after_beep"])
    return shots[index].get("interval_class")


def test_first_read_auto_classes_a_sub_hint_gap_as_movement(tmp_path: Path) -> None:
    """A 2.0-2.5 s gap with no region over it is plain ``movement``: under
    the reload hint, so the seeder proposes nothing and the heal is the
    region-blind rule's verdict."""
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 3600])
    body = _coach(client, base)
    assert body["events"] == []
    assert body["shots"][2]["interval_class"] == "movement"
    assert body["shots"][2]["interval_class_source"] == "auto"
    assert body["shots"][2]["stale"] is False
    assert _stored_class(audit_file, 2) == "movement"


def test_reclassify_keeps_a_region_derived_reload(tmp_path: Path) -> None:
    client, audit_file, base = _stage_with_a_manual_reload(tmp_path)
    resp = client.post(f"{base}/shooters/me/stages/1/coach/reclassify")
    assert resp.status_code == 200, resp.text
    assert resp.json()["shots"][2]["interval_class"] == "reload"
    assert resp.json()["shots"][2]["stale"] is False
    assert _stored_class(audit_file, 2) == "reload"


def test_audit_put_keeps_a_region_derived_reload(tmp_path: Path) -> None:
    client, audit_file, base = _stage_with_a_manual_reload(tmp_path)
    doc = client.get(f"{base}/shooters/me/stages/1/audit").json()
    resp = client.put(f"{base}/shooters/me/stages/1/audit", json=doc)
    assert resp.status_code == 200, resp.text
    assert _stored_class(audit_file, 2) == "reload"
    assert _coach(client, base)["shots"][2]["stale"] is False


def test_accept_keeps_a_region_derived_reload(tmp_path: Path) -> None:
    client, audit_file, base = _stage_with_a_manual_reload(tmp_path)
    resp = client.post(f"{base}/shooters/me/stages/1/audit/accept")
    assert resp.status_code == 200, resp.text
    assert _stored_class(audit_file, 2) == "reload"


def test_coach_patch_keeps_a_region_derived_reload(tmp_path: Path) -> None:
    client, audit_file, base = _stage_with_a_manual_reload(tmp_path)
    resp = client.patch(f"{base}/shooters/me/stages/1/shots/1/coach", json={"coaching_note": "x"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["shots"][2]["interval_class"] == "reload"
    assert resp.json()["shots"][2]["stale"] is False
    assert _stored_class(audit_file, 2) == "reload"


def test_reclassify_with_corrupt_events_is_a_422_and_leaves_the_doc(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    doc = _read(audit_file)
    doc["events"] = [{"id": "evt-1", "kind": "nap", "start": 1.0, "end": 2.0, "source": "manual"}]
    audit_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    before = audit_file.read_text(encoding="utf-8")
    resp = client.post(f"{base}/shooters/me/stages/1/coach/reclassify")
    assert resp.status_code == 422, resp.text
    assert "invalid events" in resp.json()["detail"]
    assert audit_file.read_text(encoding="utf-8") == before
