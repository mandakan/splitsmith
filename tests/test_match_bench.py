"""``scripts/match_bench.py``: the manifest a hosted benchmark replays."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "match_bench.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("match_bench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # pydantic resolves the postponed annotations through sys.modules.
    sys.modules["match_bench"] = module
    spec.loader.exec_module(module)
    return module


def _video(path: str, role: str, beep: float | None, reviewed: bool = True, mount: str = "head") -> dict:
    return {"path": path, "role": role, "beep_time": beep, "beep_reviewed": reviewed, "camera_mount": mount}


def _write_match(root: Path, shooters: dict[str, dict]) -> Path:
    root.mkdir()
    (root / "match.json").write_text(
        json.dumps(
            {
                "name": "Club Cup",
                "match_id": "club-cup-1",
                "scoreboard_match_id": "123",
                "stages": [
                    {"stage_number": 1, "stage_name": "One", "stage_rounds": {"expected": 12}},
                    {"stage_number": 2, "stage_name": "Two"},
                ],
                "shooters": list(shooters),
            }
        )
    )
    for slug, project in shooters.items():
        shooter_dir = root / "shooters" / slug
        (shooter_dir / "raw").mkdir(parents=True)
        for stage in project["stages"]:
            for video in stage["videos"]:
                target = shooter_dir / video["path"]
                if not target.exists():
                    target.write_bytes(b"\0" * 10)
        (shooter_dir / "project.json").write_text(json.dumps(project))
    return root


def test_manifest_orders_primaries_first_and_keeps_reviewed_beeps(bench, tmp_path: Path) -> None:
    root = _write_match(
        tmp_path / "match",
        {
            "s_a": {
                "name": "a",
                "competitor_name": "Ann",
                "competitor_division": "Production Optics",
                "selected_competitor_id": 7,
                "selected_shooter_id": 70,
                "stages": [
                    {
                        "stage_number": 1,
                        "time_seconds": 14.5,
                        "videos": [
                            _video("raw/head1.mp4", "secondary", 5.9),
                            _video("raw/hand1.mov", "primary", 25.1, mount="hand"),
                        ],
                    },
                    {
                        "stage_number": 2,
                        "time_seconds": None,
                        "videos": [_video("raw/hand2.mov", "primary", 3.0, False)],
                    },
                ],
            },
        },
    )

    manifest = bench.build_manifest(root)

    assert [c.filename for c in manifest.clips] == ["hand1.mov", "hand2.mov", "head1.mp4"]
    hand1, hand2, head1 = manifest.clips
    assert (hand1.role, hand1.beeps, hand1.camera_mount) == ("primary", {1: 25.1}, "hand")
    assert hand2.beeps == {}  # an unreviewed beep is not ground truth
    assert (head1.role, head1.stages) == ("secondary", [1])
    shooter = manifest.shooters[0]
    assert (shooter.name, shooter.competitor_id, shooter.stage_times) == ("Ann", 7, {1: 14.5})
    assert manifest.stages[0].expected_rounds == 12
    assert manifest.total_bytes == 30


def test_one_take_over_two_stages_is_one_clip(bench, tmp_path: Path) -> None:
    root = _write_match(
        tmp_path / "match",
        {
            "s_a": {
                "name": "a",
                "stages": [
                    {"stage_number": 1, "videos": [_video("raw/take.mp4", "primary", 4.0)]},
                    {"stage_number": 2, "videos": [_video("raw/take.mp4", "primary", 90.0)]},
                ],
            },
        },
    )
    (clip,) = bench.build_manifest(root).clips
    assert (clip.stages, clip.beeps) == ([1, 2], {1: 4.0, 2: 90.0})


def test_a_filename_two_shooters_use_is_refused(bench, tmp_path: Path) -> None:
    stage = {"stage_number": 1, "videos": [_video("raw/GOPR0001.MP4", "primary", 4.0)]}
    root = _write_match(
        tmp_path / "match",
        {"s_a": {"name": "a", "stages": [stage]}, "s_b": {"name": "b", "stages": [stage]}},
    )
    with pytest.raises(ValueError, match="GOPR0001.MP4 belongs to both"):
        bench.build_manifest(root)


def test_subset_keeps_only_the_named_stages(bench, tmp_path: Path) -> None:
    root = _write_match(
        tmp_path / "match",
        {
            "s_a": {
                "name": "a",
                "stages": [
                    {
                        "stage_number": 1,
                        "time_seconds": 10.0,
                        "videos": [_video("raw/one.mp4", "primary", 4.0)],
                    },
                    {
                        "stage_number": 2,
                        "time_seconds": 20.0,
                        "videos": [_video("raw/two.mp4", "primary", 5.0)],
                    },
                ],
            },
        },
    )
    cut = bench.subset(bench.build_manifest(root), [2])
    assert [s.stage_number for s in cut.stages] == [2]
    assert [(c.filename, c.stages, c.beeps) for c in cut.clips] == [("two.mp4", [2], {2: 5.0})]
    assert cut.shooters[0].stage_times == {2: 20.0}
