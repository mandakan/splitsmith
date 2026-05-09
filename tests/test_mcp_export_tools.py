"""Tests for the export MCP tools (issue #211 layer 3e)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith.mcp import export_tools
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _build_project(
    root: Path,
    *,
    name: str = "MCP Export Test",
    stages: list[StageEntry] | None = None,
) -> MatchProject:
    project = MatchProject.init(root, name=name)
    if stages is not None:
        project.stages = stages
    project.save(root)
    return project


def _make_audit_json(audit_dir: Path, stage_number: int, *, shots: int = 2) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage_number": stage_number,
        "stage_name": f"Stage {stage_number}",
        "stage_time_seconds": 12.0,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": i + 1,
                "time": 5.5 + 0.5 * i,
                "ms_after_beep": 500 + 500 * i,
                "source": "detected",
            }
            for i in range(shots)
        ],
    }
    out = audit_dir / f"stage{stage_number}.json"
    out.write_text(json.dumps(payload, indent=2))
    return out


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------


def test_list_templates_returns_builtin_catalogue() -> None:
    """The repo ships builtin templates under
    ``src/splitsmith/data/templates/``; the tool surfaces them."""
    rows = export_tools.list_templates_tool()
    ids = {r["id"] for r in rows}
    # match-recap.yaml + action-cut.yaml ship in the repo.
    assert "match-recap" in ids
    for row in rows:
        assert row["source"] in ("builtin", "user")
        assert isinstance(row["settings"], dict)
        # Schema version must NOT leak into ``settings`` -- the agent
        # forwards the dict to ``export_match`` and that arg shape
        # does not include schema_version.
        assert "schema_version" not in row["settings"]


def test_list_templates_user_dir_overrides_builtin(tmp_path: Path) -> None:
    """A user template with the same id wins on collision."""
    user_dir = tmp_path / "custom_templates"
    user_dir.mkdir()
    (user_dir / "match-recap.yaml").write_text("schema_version: 1\nname: Custom Recap\n")
    rows = export_tools.list_templates_tool(user_dir=str(user_dir))
    recap = next(r for r in rows if r["id"] == "match-recap")
    assert recap["source"] == "user"
    assert recap["name"] == "Custom Recap"


# ---------------------------------------------------------------------------
# export_stage
# ---------------------------------------------------------------------------


def _seed_export_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "match"
    src = root / "raw" / "primary.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"FAKE_MP4")
    primary = StageVideo(
        path=Path("raw/primary.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
        beep_confidence=1.0,
        beep_reviewed=True,
    )
    project = MatchProject.init(root, name="Export Test")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="K-Vallen",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    return root, src


def test_export_stage_calls_helper_and_returns_paths(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    audit_path = _make_audit_json(root / "audit", 1)

    fake_result = type(
        "FakeResult",
        (),
        {
            "stage_number": 1,
            "trimmed_video_path": root / "exports" / "stage1_k-vallen_trimmed.mp4",
            "csv_path": root / "exports" / "stage1_k-vallen_splits.csv",
            "fcpxml_path": root / "exports" / "stage1_k-vallen.fcpxml",
            "report_path": root / "exports" / "stage1_k-vallen_report.txt",
            "overlay_path": None,
            "shots_written": 2,
            "anomalies": [],
            "secondary_trimmed_paths": {},
        },
    )()

    with patch(
        "splitsmith.mcp.export_tools.export_helpers.export_stage", return_value=fake_result
    ) as mock_export:
        result = export_tools.export_stage_tool(str(root), stage_number=1)

    mock_export.assert_called_once()
    kwargs = mock_export.call_args.kwargs
    # Audit + exports paths flow through correctly.
    assert kwargs["audit_path"] == audit_path
    assert kwargs["exports_dir"].name == "exports"
    assert kwargs["beep_time_in_source"] == 5.0
    assert result["fcpxml_path"].endswith("stage1_k-vallen.fcpxml")
    assert result["shots_written"] == 2
    assert result["anomalies"] == []


def test_export_stage_rejects_missing_audit_json(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    # Note: no audit JSON created.
    with pytest.raises(FileNotFoundError, match="audit JSON missing"):
        export_tools.export_stage_tool(str(root), stage_number=1)


def test_export_stage_rejects_no_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "p.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    primary = StageVideo(path=Path("raw/p.mp4"), role="primary")
    project = MatchProject.init(root, name="No Beep Export")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="no beep_time"):
        export_tools.export_stage_tool(str(root), stage_number=1)


def test_export_stage_includes_secondaries_with_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    primary_src = root / "raw" / "p.mp4"
    secondary_src = root / "raw" / "s.mp4"
    for s in (primary_src, secondary_src):
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_bytes(b"x")
    primary = StageVideo(
        path=Path("raw/p.mp4"),
        role="primary",
        beep_time=5.0,
        beep_source="manual",
    )
    secondary = StageVideo(
        path=Path("raw/s.mp4"),
        role="secondary",
        beep_time=4.5,
        beep_source="aligned",
    )
    project = MatchProject.init(root, name="Multi-Cam")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary, secondary],
        )
    ]
    project.save(root)
    _make_audit_json(root / "audit", 1)

    fake_result = type(
        "FakeResult",
        (),
        {
            "stage_number": 1,
            "trimmed_video_path": None,
            "csv_path": None,
            "fcpxml_path": None,
            "report_path": None,
            "overlay_path": None,
            "shots_written": 0,
            "anomalies": [],
            "secondary_trimmed_paths": {},
        },
    )()
    with patch(
        "splitsmith.mcp.export_tools.export_helpers.export_stage", return_value=fake_result
    ) as mock_export:
        export_tools.export_stage_tool(str(root), stage_number=1)

    secondaries = mock_export.call_args.kwargs["secondaries"]
    assert len(secondaries) == 1
    assert secondaries[0].video_id == secondary.video_id
    assert secondaries[0].beep_time_in_source == 4.5


# ---------------------------------------------------------------------------
# export_match
# ---------------------------------------------------------------------------


def test_export_match_calls_helper_with_assembled_inputs(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    _make_audit_json(root / "audit", 1)
    # Pretend per-stage export produced the lossless trim already.
    exports_dir = root / "exports"
    exports_dir.mkdir(exist_ok=True)
    trimmed = exports_dir / "stage1_k-vallen_trimmed.mp4"
    trimmed.write_bytes(b"FAKE_TRIMMED_MP4")

    fake_result = type(
        "FakeMatchResult",
        (),
        {
            "fcpxml_path": exports_dir / "match.fcpxml",
            "stage_count": 1,
            "duration_seconds": 22.5,
            "anomalies": ["one warning"],
        },
    )()
    with patch(
        "splitsmith.mcp.export_tools.match_export_helpers.export_match",
        return_value=fake_result,
    ) as mock_export:
        result = export_tools.export_match_tool(str(root), stage_numbers=[1])

    mock_export.assert_called_once()
    stages_input = mock_export.call_args.kwargs["stages"]
    assert len(stages_input) == 1
    assert stages_input[0].stage_number == 1
    assert stages_input[0].trimmed_path == trimmed
    assert result["output_path"].endswith("match.fcpxml")
    assert result["anomalies"] == ["one warning"]
    assert result["stage_count"] == 1


def test_export_match_errors_when_trim_missing(tmp_path: Path) -> None:
    """Match composer needs every stage's lossless trim to exist."""
    root, _src = _seed_export_project(tmp_path)
    _make_audit_json(root / "audit", 1)
    # No trim file created.
    with pytest.raises(FileNotFoundError, match="lossless trim missing"):
        export_tools.export_match_tool(str(root), stage_numbers=[1])


def test_export_match_errors_when_audit_missing(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    exports_dir = root / "exports"
    exports_dir.mkdir(exist_ok=True)
    (exports_dir / "stage1_k-vallen_trimmed.mp4").write_bytes(b"x")
    # No audit JSON.
    with pytest.raises(FileNotFoundError, match="audit JSON missing"):
        export_tools.export_match_tool(str(root), stage_numbers=[1])


def test_export_match_validates_head_pad_against_project_buffer(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    project = MatchProject.load(root)
    project.trim_pre_buffer_seconds = 4.0
    project.save(root)
    with pytest.raises(ValueError, match="head_pad_seconds"):
        export_tools.export_match_tool(str(root), stage_numbers=[1], head_pad_seconds=5.0)


def test_export_match_rejects_empty_stage_numbers(tmp_path: Path) -> None:
    root = tmp_path / "match"
    MatchProject.init(root, name="Empty").save(root)
    with pytest.raises(ValueError, match="stage_numbers cannot be empty"):
        export_tools.export_match_tool(str(root), stage_numbers=[])


def test_export_match_rejects_unknown_stage_number(tmp_path: Path) -> None:
    root, _src = _seed_export_project(tmp_path)
    with pytest.raises(ValueError, match="not found in project"):
        export_tools.export_match_tool(str(root), stage_numbers=[99])


def test_export_match_rejects_stage_without_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    src = root / "raw" / "p.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    primary = StageVideo(path=Path("raw/p.mp4"), role="primary")
    project = MatchProject.init(root, name="Test")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="One",
            time_seconds=12.0,
            videos=[primary],
        )
    ]
    project.save(root)
    with pytest.raises(ValueError, match="no primary or no beep"):
        export_tools.export_match_tool(str(root), stage_numbers=[1])
