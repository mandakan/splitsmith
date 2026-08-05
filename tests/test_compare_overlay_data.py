"""Shot + scoring data for the grid overlay, read straight off disk."""

import json
import logging
from pathlib import Path

import pytest

from splitsmith.compare import overlay_data, project_loader
from splitsmith.config import StageRounds
from splitsmith.ui.project import MatchProject, StageEntry, StageScorecard


def _write_audit(root: Path, stage_number: int, ms_after_beep: list[int]) -> Path:
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"stage{stage_number}.json"
    path.write_text(
        json.dumps(
            {
                "stage_time_seconds": 6.0,
                "beep_time": 3.0,
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate(ms_after_beep)
                ],
            }
        )
    )
    return path


def _bundle(tmp_path: Path, label: str, *, stage_number: int = 1, audit: Path | None = None):
    root = tmp_path / label
    root.mkdir(parents=True, exist_ok=True)
    stage = project_loader.CompareStageBundle(
        stage_number=stage_number,
        stage_name=f"Stage {stage_number}",
        trim_path=root / "trim.mov",
        audit_path=audit if audit is not None else root / "audit" / f"stage{stage_number}.json",
        beep_offset_in_clip=3.0,
        duration_seconds=9.0,
        width=1920,
        height=1080,
        frame_rate_num=60000,
        frame_rate_den=1001,
        camera_mount=None,
        substituted=False,
    )
    return project_loader.CompareShooterBundle(
        label=label,
        project_root=root,
        project=None,
        stages_by_number={stage_number: stage},
        missing_trims=[],
    )


def test_shot_times_are_measured_from_the_beep(tmp_path):
    audit = _write_audit(tmp_path / "ann", 1, [1200, 1450, 1700])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert [round(s.time_from_beep, 3) for s in tile.shots] == [1.2, 1.45, 1.7]


def test_beep_offset_in_clip_does_not_shift_shot_times(tmp_path):
    # beep_offset_in_clip is 3.0 in the fixture. If it leaked into the
    # origin every shot would be 3s late and still look plausible.
    audit = _write_audit(tmp_path / "ann", 1, [1200])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    assert data[("ann", 1)].shots[0].time_from_beep == pytest.approx(1.2)


def test_first_split_is_the_draw_and_later_splits_are_differences(tmp_path):
    # Deliberately fed out of order: with already-ordered input this test
    # cannot tell time order from shot_number order, and the two disagree
    # on exactly the audits that produce a negative split.
    audit = _write_audit(tmp_path / "ann", 1, [1450, 1200, 1700])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    splits = [round(s.split, 3) for s in data[("ann", 1)].shots]
    assert splits == [1.2, 0.25, 0.25]


def test_splits_are_recomputed_over_the_time_sorted_sequence(tmp_path):
    # ``audit.py``'s CSV apply preserves row order as ``shot_number``, so a
    # hand-sorted prep sheet lands shots out of time order.
    # ``audit_shots_to_engine_shots`` would then hand back splits of
    # [1.7, -0.5, 0.25] -- a negative number the overlay would draw.
    audit = _write_audit(tmp_path / "ann", 1, [1700, 1200, 1450])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert [round(s.time_from_beep, 3) for s in tile.shots] == [1.2, 1.45, 1.7]
    assert [round(s.split, 3) for s in tile.shots] == [1.2, 0.25, 0.25]
    assert all(s.split > 0 for s in tile.shots)


@pytest.mark.parametrize("payload", ["[]", "null", '"nope"', "3"])
def test_valid_json_that_is_not_an_object_degrades_and_warns(tmp_path, caplog, payload):
    # ``read_audit_data`` hands back whatever ``json.loads`` produced with
    # no shape check, so these reach ``.get`` and used to raise
    # ``AttributeError`` past the handler, failing the whole render.
    root = tmp_path / "cy"
    (root / "audit").mkdir(parents=True)
    bad = root / "audit" / "stage1.json"
    bad.write_text(payload)
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "cy", audit=bad)])
    assert data[("cy", 1)].shots == ()
    assert any("stage1.json" in r.getMessage() for r in caplog.records)


def test_shots_come_back_in_time_order(tmp_path):
    """Ordered by time, whatever order the audit's rows were in.

    ``TileShot`` carries no shot number: the sequence is the ordering, and
    a stored ``index + 1`` would disagree with the audit's own
    ``shot_number`` on exactly this input.
    """
    audit = _write_audit(tmp_path / "ann", 1, [1700, 1200, 1450])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert [round(s.time_from_beep, 3) for s in tile.shots] == [1.2, 1.45, 1.7]
    assert not hasattr(tile.shots[0], "number")


def test_missing_audit_degrades_to_no_shots(tmp_path):
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "bo")])
    tile = data[("bo", 1)]
    assert tile.shots == ()
    assert tile.has_shots is False
    assert tile.shot_count == 0
    assert tile.last_shot_time is None


def test_corrupt_audit_degrades_and_warns(tmp_path, caplog):
    root = tmp_path / "cy"
    (root / "audit").mkdir(parents=True)
    bad = root / "audit" / "stage1.json"
    bad.write_text("{not json")
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "cy", audit=bad)])
    assert data[("cy", 1)].shots == ()
    assert any("stage1.json" in r.getMessage() for r in caplog.records)


def test_missing_project_json_degrades_without_raising(tmp_path):
    audit = _write_audit(tmp_path / "dee", 1, [1000])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "dee", audit=audit)])
    tile = data[("dee", 1)]
    assert tile.scorecard is None
    assert tile.stage_time_seconds is None
    assert tile.shot_count == 1  # the audit still read fine


def test_every_label_stage_pair_is_present_even_when_empty(tmp_path):
    bundles = [_bundle(tmp_path, "ann"), _bundle(tmp_path, "bo")]
    data = overlay_data.load_overlay_data(bundles)
    assert set(data) == {("ann", 1), ("bo", 1)}


# --- the rest of the degradation table -------------------------------------


def _write_project(
    root: Path,
    *,
    stage_number: int = 1,
    time_seconds: float = 12.5,
    time_seconds_manual: bool = False,
    scorecard: StageScorecard | None = None,
    stage_rounds: StageRounds | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    project = MatchProject(name=root.name)
    project.stages = [
        StageEntry(
            stage_number=stage_number,
            stage_name=f"Stage {stage_number}",
            time_seconds=time_seconds,
            time_seconds_manual=time_seconds_manual,
            scorecard=scorecard,
            stage_rounds=stage_rounds,
        )
    ]
    project.save(root)


def test_stub_audit_is_treated_as_no_audit(tmp_path):
    # A contract test, not a branch test: ``is_stub_audit`` only returns
    # True for a document with no shots, so removing the explicit stub
    # check cannot change this outcome. Pinned anyway because the
    # requirement is real and the sentinel's definition could loosen.
    root = tmp_path / "ann"
    (root / "audit").mkdir(parents=True)
    stub = root / "audit" / "stage1.json"
    stub.write_text(json.dumps({"detection": "none", "shots": [], "audit_events": []}))
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=stub)])
    tile = data[("ann", 1)]
    assert tile.shots == ()
    assert tile.has_shots is False


def test_scoring_comes_off_the_project_on_disk(tmp_path):
    root = tmp_path / "ann"
    card = StageScorecard(hit_factor=6.5, stage_points=80.0, stage_pct=91.25)
    rounds = StageRounds(expected=12)
    _write_project(root, time_seconds=12.5, scorecard=card, stage_rounds=rounds)
    audit = _write_audit(root, 1, [1200, 1450])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert tile.stage_time_seconds == pytest.approx(12.5)
    assert tile.stage_time_is_manual is False
    assert tile.scorecard is not None
    assert tile.scorecard.stage_pct == pytest.approx(91.25)
    assert tile.stage_rounds is not None
    assert tile.stage_rounds.expected == 12


def test_non_positive_stage_time_reads_as_unset(tmp_path):
    root = tmp_path / "ann"
    _write_project(root, time_seconds=0.0)
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann")])
    assert data[("ann", 1)].stage_time_seconds is None


def test_manual_stage_time_is_recorded_as_manual(tmp_path):
    root = tmp_path / "ann"
    _write_project(root, time_seconds=11.0, time_seconds_manual=True, scorecard=None)
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann")])
    tile = data[("ann", 1)]
    assert tile.stage_time_seconds == pytest.approx(11.0)
    assert tile.stage_time_is_manual is True
    assert tile.scorecard is None


def test_stage_absent_from_the_project_degrades_without_raising(tmp_path):
    # The bundle covers stage 1 but project.json only knows stage 2.
    root = tmp_path / "ann"
    _write_project(root, stage_number=2)
    audit = _write_audit(root, 1, [1000])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert tile.stage_time_seconds is None
    assert tile.scorecard is None
    assert tile.shot_count == 1


def test_missing_project_is_logged_once_per_shooter_not_per_stage(tmp_path, caplog):
    root = tmp_path / "ann"
    root.mkdir(parents=True, exist_ok=True)
    stages = {}
    for n in (1, 2, 3):
        _write_audit(root, n, [1000])
        stages[n] = project_loader.CompareStageBundle(
            stage_number=n,
            stage_name=f"Stage {n}",
            trim_path=root / "trim.mov",
            audit_path=root / "audit" / f"stage{n}.json",
            beep_offset_in_clip=3.0,
            duration_seconds=9.0,
            width=1920,
            height=1080,
            frame_rate_num=60000,
            frame_rate_den=1001,
        )
    bundle = project_loader.CompareShooterBundle(
        label="ann", project_root=root, project=None, stages_by_number=stages
    )
    with caplog.at_level(logging.WARNING, logger="splitsmith.compare.overlay_data"):
        data = overlay_data.load_overlay_data([bundle])
    assert set(data) == {("ann", 1), ("ann", 2), ("ann", 3)}
    project_warnings = [r for r in caplog.records if "project.json" in r.getMessage()]
    assert len(project_warnings) == 1


def test_an_invalid_project_json_stays_loud(tmp_path):
    # Only *missing* data degrades. A project.json that exists but will not
    # validate is a bug, and a shooter that silently renders without any
    # scoring is exactly how it would go unnoticed.
    root = tmp_path / "ann"
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(json.dumps({"name": "ann", "stages": "not a list"}))
    with pytest.raises(Exception) as excinfo:
        overlay_data.load_overlay_data([_bundle(tmp_path, "ann")])
    assert not isinstance(excinfo.value, OSError)


def test_a_preloaded_project_on_the_bundle_is_reused(tmp_path):
    # ``load_shooter`` already carries the MatchProject; re-reading it would
    # be wasted I/O and would miss in-memory state the caller set up.
    root = tmp_path / "ann"
    root.mkdir(parents=True, exist_ok=True)
    project = MatchProject(name="ann")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=9.75)]
    bundle = _bundle(tmp_path, "ann")
    bundle = project_loader.CompareShooterBundle(
        label=bundle.label,
        project_root=bundle.project_root,
        project=project,
        stages_by_number=bundle.stages_by_number,
    )
    assert not (root / "project.json").exists()
    data = overlay_data.load_overlay_data([bundle])
    assert data[("ann", 1)].stage_time_seconds == pytest.approx(9.75)


def test_one_bad_audit_does_not_stop_the_other_stages(tmp_path):
    root = tmp_path / "ann"
    _write_audit(root, 1, [1000])
    _write_audit(root, 2, [1100, 1400])
    (root / "audit" / "stage2.json").write_text("{not json")
    _write_audit(root, 3, [1200])
    stages = {
        n: project_loader.CompareStageBundle(
            stage_number=n,
            stage_name=f"Stage {n}",
            trim_path=root / "trim.mov",
            audit_path=root / "audit" / f"stage{n}.json",
            beep_offset_in_clip=3.0,
            duration_seconds=9.0,
            width=1920,
            height=1080,
            frame_rate_num=60000,
            frame_rate_den=1001,
        )
        for n in (1, 2, 3)
    }
    bundle = project_loader.CompareShooterBundle(
        label="ann", project_root=root, project=None, stages_by_number=stages
    )
    data = overlay_data.load_overlay_data([bundle])
    assert [data[("ann", n)].shot_count for n in (1, 2, 3)] == [1, 0, 1]
