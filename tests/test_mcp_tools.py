"""Pure-function tests for the read-only MCP tools (issue #211 layer 1).

Calls the tool implementations directly (they're pure functions) so
we don't need the asyncio / stdio plumbing of FastMCP. The server
wiring itself is covered separately by ``test_mcp_server.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from splitsmith.automation import AutomationOverride
from splitsmith.mcp import tools
from splitsmith.mcp.sandbox import ALLOWED_ROOT_ENV
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from splitsmith.video_probe import ProbeResult


def _build_project(
    root: Path,
    *,
    name: str = "MCP Test",
    stages: list[StageEntry] | None = None,
    automation: AutomationOverride | None = None,
) -> MatchProject:
    project = MatchProject.init(root, name=name)
    if stages is not None:
        project.stages = stages
    if automation is not None:
        project.automation = automation
    project.save(root)
    return project


def test_probe_video_returns_probe_result(tmp_path: Path) -> None:
    """probe_video wraps video_probe.probe; we mock the probe so this
    test doesn't shell out to ffprobe (per project conventions)."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00")
    fake = ProbeResult(duration=12.5, width=1920, height=1080, codec="h264")
    with patch("splitsmith.mcp.tools.video_probe.probe", return_value=fake):
        result = tools.probe_video(str(src))
    assert result["duration"] == 12.5
    assert result["width"] == 1920
    assert result["codec"] == "h264"


def test_probe_video_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tools.probe_video(str(tmp_path / "missing.mp4"))


def test_probe_video_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a file"):
        tools.probe_video(str(tmp_path))


def test_discover_videos_lists_top_level_only(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mov").write_bytes(b"b")
    (tmp_path / "ignored.txt").write_text("not video")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.mp4").write_bytes(b"deep")

    rows = tools.discover_videos(str(tmp_path))
    paths = [r["path"] for r in rows]
    assert any(p.endswith("a.mp4") for p in paths)
    assert any(p.endswith("b.mov") for p in paths)
    assert not any(p.endswith("ignored.txt") for p in paths)
    assert not any("deep" in p for p in paths)


def test_discover_videos_recursive_walks_tree(tmp_path: Path) -> None:
    (tmp_path / "top.mp4").write_bytes(b"x")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.mp4").write_bytes(b"x")
    rows = tools.discover_videos(str(tmp_path), recursive=True)
    paths = {r["path"] for r in rows}
    assert any(p.endswith("top.mp4") for p in paths)
    assert any(p.endswith("deep.mp4") for p in paths)


def test_discover_videos_skips_hidden_dirs_recursive(tmp_path: Path) -> None:
    """``.git`` / ``.cache`` would otherwise spam the results; skipping
    is what makes the tool actually usable on a real project root."""
    visible = tmp_path / "good.mp4"
    visible.write_bytes(b"x")
    hidden = tmp_path / ".cache" / "skipme.mp4"
    hidden.parent.mkdir()
    hidden.write_bytes(b"x")
    rows = tools.discover_videos(str(tmp_path), recursive=True)
    paths = {r["path"] for r in rows}
    assert any(p.endswith("good.mp4") for p in paths)
    assert not any("skipme" in p for p in paths)


def test_discover_videos_skips_top_level_hidden(tmp_path: Path) -> None:
    (tmp_path / ".hidden.mp4").write_bytes(b"x")
    (tmp_path / "visible.mp4").write_bytes(b"x")
    rows = tools.discover_videos(str(tmp_path))
    paths = {r["path"] for r in rows}
    assert any(p.endswith("visible.mp4") for p in paths)
    assert not any(".hidden" in p for p in paths)


def test_discover_videos_returns_size_and_mtime(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"123456")
    [row] = tools.discover_videos(str(tmp_path))
    assert row["size_bytes"] == 6
    assert isinstance(row["modified_at"], float)


def test_discover_videos_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tools.discover_videos(str(tmp_path / "absent"))


def test_get_project_returns_full_state(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="Hello",
                time_seconds=12.0,
                videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0)],
            )
        ],
    )
    result = tools.get_project(str(root))
    assert result["name"] == "MCP Test"
    assert len(result["stages"]) == 1
    assert result["stages"][0]["stage_name"] == "Hello"


def test_list_stages_returns_compact_per_stage_summary(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=2,
                stage_name="Two",
                time_seconds=15.0,
                videos=[
                    StageVideo(path=Path("raw/p.mp4"), role="primary", beep_time=5.0),
                    StageVideo(path=Path("raw/s.mp4"), role="secondary"),
                ],
            ),
            StageEntry(
                stage_number=1,
                stage_name="One",
                time_seconds=12.0,
                videos=[StageVideo(path=Path("raw/p2.mp4"), role="primary")],
            ),
        ],
    )
    rows = tools.list_stages(str(root))
    assert [r["stage_number"] for r in rows] == [1, 2], "must be sorted ascending"
    stage_two = next(r for r in rows if r["stage_number"] == 2)
    assert stage_two["secondary_count"] == 1
    assert stage_two["primary"]["beep_time"] == 5.0
    stage_one = next(r for r in rows if r["stage_number"] == 1)
    assert stage_one["primary"]["beep_time"] is None


def test_list_stages_handles_stage_with_no_primary(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="No Primary",
                time_seconds=10.0,
                videos=[StageVideo(path=Path("raw/s.mp4"), role="secondary")],
            )
        ],
    )
    [row] = tools.list_stages(str(root))
    assert row["primary"] is None
    assert row["secondary_count"] == 1


def test_get_hitl_queue_lists_low_confidence(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="One",
                time_seconds=12.0,
                videos=[
                    StageVideo(
                        path=Path("raw/p.mp4"),
                        role="primary",
                        beep_time=5.0,
                        beep_source="auto",
                        beep_confidence=0.4,
                        beep_reviewed=False,
                    )
                ],
            )
        ],
    )
    result = tools.get_hitl_queue(str(root))
    assert result["threshold"] == 0.95
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["kind"] == "beep_low_confidence"
    assert item["confidence"] == 0.4
    assert "candidate" in item["suggested_action"].lower()


def test_get_hitl_queue_lists_missing_beep(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=2,
                stage_name="Missing",
                time_seconds=12.0,
                videos=[
                    StageVideo(
                        path=Path("raw/p.mp4"),
                        role="primary",
                        beep_time=None,
                        beep_source="auto",
                        beep_auto_detect_failed=True,
                    )
                ],
            )
        ],
    )
    [item] = tools.get_hitl_queue(str(root))["items"]
    assert item["kind"] == "beep_missing"
    assert item["confidence"] is None


def test_get_hitl_queue_omits_trusted_and_manual(tmp_path: Path) -> None:
    """Reviewed (auto-trusted) and manual beeps don't need attention."""
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="Trusted",
                time_seconds=12.0,
                videos=[
                    StageVideo(
                        path=Path("raw/p.mp4"),
                        role="primary",
                        beep_time=5.0,
                        beep_source="auto",
                        beep_confidence=0.9,
                        beep_reviewed=True,
                    )
                ],
            ),
            StageEntry(
                stage_number=2,
                stage_name="Manual",
                time_seconds=12.0,
                videos=[
                    StageVideo(
                        path=Path("raw/q.mp4"),
                        role="primary",
                        beep_time=5.0,
                        beep_source="manual",
                        beep_confidence=1.0,
                        beep_reviewed=True,
                    )
                ],
            ),
        ],
    )
    assert tools.get_hitl_queue(str(root))["items"] == []


def test_get_hitl_queue_threshold_picks_up_project_override(tmp_path: Path) -> None:
    root = tmp_path / "match"
    _build_project(
        root,
        stages=[
            StageEntry(
                stage_number=1,
                stage_name="One",
                time_seconds=12.0,
                videos=[
                    StageVideo(
                        path=Path("raw/p.mp4"),
                        role="primary",
                        beep_time=5.0,
                        beep_source="auto",
                        beep_confidence=0.65,
                        beep_reviewed=False,
                    )
                ],
            )
        ],
        automation=AutomationOverride(beep_low_confidence_threshold=0.9),
    )
    result = tools.get_hitl_queue(str(root))
    assert result["threshold"] == 0.9


def test_tools_honour_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A sandboxed server rejects out-of-sandbox arguments. Smoke-tests
    the wiring -- per-helper sandbox behaviour is covered by
    test_mcp_sandbox."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"x")
    monkeypatch.setenv(ALLOWED_ROOT_ENV, str(sandbox))

    with pytest.raises(PermissionError):
        tools.discover_videos(str(outside))
    with pytest.raises(PermissionError):
        tools.probe_video(str(outside / "video.mp4"))
