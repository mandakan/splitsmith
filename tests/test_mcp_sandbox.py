"""Sandbox helpers for the MCP tools (issue #211 layer 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.mcp.sandbox import (
    ALLOWED_ROOT_ENV,
    SandboxError,
    allowed_root,
    resolve_project_root,
    resolve_within_sandbox,
)


def test_allowed_root_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOWED_ROOT_ENV, raising=False)
    assert allowed_root() is None


def test_allowed_root_resolves_user_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tilde expansion + absolute resolution match :func:`Path.resolve`."""
    monkeypatch.setenv(ALLOWED_ROOT_ENV, str(tmp_path))
    assert allowed_root() == tmp_path.resolve()


def test_resolve_within_sandbox_passes_when_under_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ALLOWED_ROOT_ENV, str(tmp_path))
    inside = tmp_path / "inner" / "video.mp4"
    inside.parent.mkdir()
    inside.write_bytes(b"x")
    resolved = resolve_within_sandbox(inside, label="video")
    assert resolved == inside.resolve()


def test_resolve_within_sandbox_rejects_path_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    monkeypatch.setenv(ALLOWED_ROOT_ENV, str(sandbox))
    with pytest.raises(SandboxError, match="outside the allowed root"):
        resolve_within_sandbox(outside, label="path")


def test_resolve_within_sandbox_no_sandbox_allows_any_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absent ``SPLITSMITH_MCP_ALLOWED_ROOT`` the helper still resolves
    + validates path shape; it just doesn't enforce a boundary."""
    monkeypatch.delenv(ALLOWED_ROOT_ENV, raising=False)
    target = tmp_path / "anywhere.mp4"
    target.write_bytes(b"x")
    resolved = resolve_within_sandbox(target, label="path")
    assert resolved == target.resolve()


def test_resolve_within_sandbox_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        resolve_within_sandbox("", label="video_path")


def test_resolve_project_root_requires_project_json(tmp_path: Path) -> None:
    bare = tmp_path / "no_project_here"
    bare.mkdir()
    with pytest.raises(FileNotFoundError, match="project.json"):
        resolve_project_root(bare)


def test_resolve_project_root_passes_when_project_json_exists(tmp_path: Path) -> None:
    root = tmp_path / "match"
    root.mkdir()
    (root / "project.json").write_text("{}")
    assert resolve_project_root(root) == root.resolve()


def test_resolve_project_root_rejects_non_directory(tmp_path: Path) -> None:
    f = tmp_path / "not_a_dir"
    f.write_text("hi")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        resolve_project_root(f)


def test_resolve_project_root_honours_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sandbox enforcement runs BEFORE the project.json check, so an
    out-of-sandbox project root errors with SandboxError, not a
    misleading FileNotFoundError pointing at project.json."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside_match"
    outside.mkdir()
    (outside / "project.json").write_text("{}")
    monkeypatch.setenv(ALLOWED_ROOT_ENV, str(sandbox))
    with pytest.raises(SandboxError):
        resolve_project_root(outside)
