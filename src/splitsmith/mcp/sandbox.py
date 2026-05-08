"""Path resolution + sandboxing helpers for the MCP tools (issue #211).

All MCP tools take filesystem paths as inputs (project roots, video
files, directories to discover). Without a sandbox the agent could
ask the server to read or list arbitrary places on disk; with one,
every path argument must resolve under a configured allowed root.

The sandbox is opt-in: ``SPLITSMITH_MCP_ALLOWED_ROOT`` sets it; absent
that env var the helpers still resolve + validate paths but skip the
"under allowed root" check. This lets local users run the server
without ceremony while giving agentic deployments a single knob to
turn on the boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOWED_ROOT_ENV = "SPLITSMITH_MCP_ALLOWED_ROOT"


class SandboxError(PermissionError):
    """Raised when a path argument falls outside the allowed root."""


def allowed_root() -> Path | None:
    """Return the configured sandbox root, or ``None`` if unset.

    Reads ``SPLITSMITH_MCP_ALLOWED_ROOT`` fresh on every call so tests
    can flip the env var per-case via monkeypatch without re-importing.
    """
    raw = os.environ.get(ALLOWED_ROOT_ENV)
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def resolve_within_sandbox(value: str | Path, *, label: str = "path") -> Path:
    """Resolve ``value`` to an absolute path and verify it sits under the
    sandbox root (when one is configured).

    ``label`` is only used in error messages so tools surface
    "video_path is outside the allowed root" instead of a generic
    PermissionError. Symlinks are followed before the check so a
    symlink inside the sandbox pointing outside is rejected.
    """
    if not value:
        raise ValueError(f"{label} must be a non-empty path")
    resolved = Path(value).expanduser().resolve()
    root = allowed_root()
    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SandboxError(f"{label} {resolved} is outside the allowed root {root}") from exc
    return resolved


def resolve_project_root(value: str | Path) -> Path:
    """Resolve a project-root path and verify ``project.json`` exists.

    Layered on top of :func:`resolve_within_sandbox`: the sandbox check
    runs first, then we confirm the directory is actually a splitsmith
    project root. Tools should call this rather than instantiating
    :class:`~splitsmith.ui.project.MatchProject` directly so the
    error messages stay consistent across the surface.
    """
    resolved = resolve_within_sandbox(value, label="project_root")
    if not resolved.is_dir():
        raise FileNotFoundError(f"project_root {resolved} is not a directory")
    if not (resolved / "project.json").exists():
        raise FileNotFoundError(
            f"project_root {resolved} has no project.json -- "
            "create the project via 'splitsmith ui' or 'splitsmith init' first"
        )
    return resolved
