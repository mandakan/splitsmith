"""Tests for ``splitsmith.cleanup``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.cleanup import (
    CLEANUP_LOG_FILENAME,
    SAFE_CATEGORIES,
    CleanupCategory,
    CleanupPlan,
    apply_cleanup,
    plan_cleanup,
)
from splitsmith.ui.project import MatchProject


def _project(tmp_path: Path) -> tuple[MatchProject, Path]:
    root = tmp_path / "match"
    project = MatchProject.init(root, name="Cleanup Match")
    return project, root


def _write(path: Path, contents: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)


def test_audit_data_excluded_from_safe_categories() -> None:
    assert CleanupCategory.AUDIT_DATA not in SAFE_CATEGORIES
    assert CleanupCategory.CACHES in SAFE_CATEGORIES
    assert CleanupCategory.EXPORTS_OVERLAYS in SAFE_CATEGORIES


def test_plan_caches_empty_when_dirs_missing(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    plan = plan_cleanup(project, root, {CleanupCategory.CACHES})
    assert plan.total_file_count == 0
    assert plan.total_bytes == 0
    assert plan.totals_by_category[CleanupCategory.CACHES].file_count == 0


def test_plan_audio_separate_from_peaks(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audio_dir = project.audio_path(root)
    audio_dir.mkdir(parents=True, exist_ok=True)
    _write(audio_dir / "stage1_primary.wav", b"\x00" * 1024)
    _write(audio_dir / "stage1_primary.peaks-2048.json", b"[]")

    audio_only = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    assert audio_only.total_file_count == 1
    assert audio_only.items[0].path.name == "stage1_primary.wav"

    caches_only = plan_cleanup(project, root, {CleanupCategory.CACHES})
    assert caches_only.total_file_count == 1
    assert caches_only.items[0].path.name == "stage1_primary.peaks-2048.json"


def test_plan_exports_buckets_distinct(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    exp = project.exports_path(root)
    exp.mkdir(parents=True, exist_ok=True)
    _write(exp / "stage1_one.fcpxml")
    _write(exp / "stage1_one_splits.csv")
    _write(exp / "stage1_one_report.txt")
    _write(exp / "stage1_one_overlay.mov", b"\x00" * 4096)
    _write(exp / "stage1_one_trimmed.mp4", b"\x00" * 8192)
    _write(exp / "stage1_one_cam_abc123_trimmed.mp4", b"\x00" * 2048)

    light = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})
    assert {it.path.name for it in light.items} == {
        "stage1_one.fcpxml",
        "stage1_one_splits.csv",
        "stage1_one_report.txt",
    }

    overlays = plan_cleanup(project, root, {CleanupCategory.EXPORTS_OVERLAYS})
    assert {it.path.name for it in overlays.items} == {"stage1_one_overlay.mov"}

    trims = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    # Both primary and per-cam trims captured.
    assert {it.path.name for it in trims.items} == {
        "stage1_one_trimmed.mp4",
        "stage1_one_cam_abc123_trimmed.mp4",
    }


def test_plan_audit_data_includes_bak(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audit = project.audit_path(root)
    audit.mkdir(parents=True, exist_ok=True)
    _write(audit / "stage1.json", b"{}")
    _write(audit / "stage1.json.bak", b"{}")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIT_DATA})
    names = {it.path.name for it in plan.items}
    assert names == {"stage1.json", "stage1.json.bak"}


def test_plan_respects_audio_dir_override(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    scratch = tmp_path / "scratch_audio"
    scratch.mkdir()
    project.audio_dir = str(scratch)
    project.save(root)

    _write(scratch / "stage1_primary.wav", b"\x00" * 512)
    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    assert plan.total_file_count == 1
    assert plan.items[0].path == scratch / "stage1_primary.wav"


def test_plan_skips_symlinks(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audio_dir = project.audio_path(root)
    audio_dir.mkdir(parents=True, exist_ok=True)
    real = tmp_path / "elsewhere.wav"
    _write(real, b"\x00" * 1024)
    link = audio_dir / "stage1_primary.wav"
    link.symlink_to(real)

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    assert plan.total_file_count == 0
    assert real.exists()  # never seen


def test_plan_never_picks_up_raw(tmp_path: Path) -> None:
    """The globs should never reach into raw/ -- defense-in-depth check."""
    project, root = _project(tmp_path)
    raw = project.raw_path(root)
    raw.mkdir(parents=True, exist_ok=True)
    _write(raw / "source.mp4", b"\x00" * 4096)

    every = plan_cleanup(project, root, set(CleanupCategory))
    for item in every.items:
        # Should never resolve under raw
        try:
            item.path.resolve().relative_to(raw.resolve())
        except (OSError, ValueError):
            continue
        else:  # pragma: no cover
            raise AssertionError(f"{item.path} resolves into raw/")


def test_apply_handles_missing_files(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audio = project.audio_path(root)
    audio.mkdir(parents=True, exist_ok=True)
    target = audio / "stage1_primary.wav"
    _write(target, b"\x00" * 256)

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    target.unlink()  # remove out-of-band

    result = apply_cleanup(plan, root=root)
    assert result.failed == []
    # Best-effort: bytes from plan still counted, deletion is missing_ok.
    assert result.bytes_freed == 256


def test_apply_records_failures_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, root = _project(tmp_path)
    audio = project.audio_path(root)
    audio.mkdir(parents=True, exist_ok=True)
    keep_target = audio / "stage1_primary.wav"
    _write(keep_target, b"\x00" * 100)
    fail_target = audio / "stage2_primary.wav"
    _write(fail_target, b"\x00" * 200)

    real_unlink = Path.unlink

    def flaky(self: Path, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if self == fail_target:
            raise OSError("permission denied")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    result = apply_cleanup(plan, root=root)

    assert keep_target.exists() is False
    assert fail_target.exists() is True  # the flaky one survived
    assert result.deleted == [keep_target]
    assert len(result.failed) == 1
    assert result.failed[0][0] == fail_target


def test_apply_never_touches_raw_or_project_json(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    raw = project.raw_path(root)
    raw.mkdir(parents=True, exist_ok=True)
    _write(raw / "source.mp4", b"\x00" * 1024)
    audit = project.audit_path(root)
    audit.mkdir(parents=True, exist_ok=True)
    _write(audit / "stage1.json")

    plan = plan_cleanup(project, root, set(CleanupCategory))
    apply_cleanup(plan, root=root)

    assert (raw / "source.mp4").exists()
    assert (root / "project.json").exists()
    # audit JSON is in the plan and gets deleted
    assert not (audit / "stage1.json").exists()


def test_apply_writes_cleanup_log(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    audio = project.audio_path(root)
    audio.mkdir(parents=True, exist_ok=True)
    _write(audio / "stage1_primary.wav", b"\x00" * 64)

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    apply_cleanup(plan, root=root)

    log = root / CLEANUP_LOG_FILENAME
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["categories"] == ["audio"]
    assert record["deleted_count"] == 1
    assert record["bytes_freed"] == 64


def test_empty_categories_returns_empty_plan(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    plan = plan_cleanup(project, root, set())
    assert plan.total_file_count == 0
    assert plan.totals_by_category == {}
    assert isinstance(plan, CleanupPlan)
