"""Global user-config directory (issue #75).

Splitsmith is fundamentally per-project: each match folder is its own
``MatchProject`` with its own scoreboard cache and settings. Anything
cross-project (recent projects, the user's SSI Scoreboard identity, default
preferences) lives here.

On-disk layout::

    <home>/
      config.yaml          # global preferences (theme, default trim mode, ...)
      projects.json        # ordered list of recently-opened MatchProject roots
      scoreboard.json      # SSI Scoreboard identity (shooter_id, display name, base URL)
      cache/               # cross-project caches (reserved; not used yet)

Resolution order for ``<home>``:

1. ``SPLITSMITH_HOME`` env var (any platform): full override.
2. Linux + ``XDG_CONFIG_HOME`` set: ``$XDG_CONFIG_HOME/splitsmith``.
3. Linux fallback: ``~/.config/splitsmith``.
4. macOS / Windows / other: ``~/.splitsmith``.

Set ``SPLITSMITH_DISABLE_USER_CONFIG=1`` to opt out: every read returns an
empty default and every write is a no-op. Same behaviour falls out
naturally when the directory can't be created (permission denied, etc.) --
the module logs a warning once and continues without persisting.

The directory is created lazily on first write. Read paths never create
files. All writes are atomic (tmp file + ``os.replace``) so a crashed
write can't corrupt state.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Bumped when an on-disk schema in this directory changes incompatibly. The
# loader records this on every write so a future migration has a hook.
SCHEMA_VERSION = 1

ENV_HOME = "SPLITSMITH_HOME"
ENV_DISABLE = "SPLITSMITH_DISABLE_USER_CONFIG"
ENV_XDG = "XDG_CONFIG_HOME"

PROJECTS_FILENAME = "projects.json"
SCOREBOARD_FILENAME = "scoreboard.json"
CONFIG_FILENAME = "config.yaml"
CACHE_DIRNAME = "cache"

# Cap recent-projects history. The SPA shows ~5-10; 50 is a comfortable
# ceiling that doesn't grow unbounded for users who churn through projects.
RECENT_PROJECTS_LIMIT = 50


class RecentProject(BaseModel):
    """One entry in ``projects.json``."""

    path: str
    name: str
    last_opened_at: datetime


class ProjectsIndex(BaseModel):
    schema_version: int = SCHEMA_VERSION
    projects: list[RecentProject] = Field(default_factory=list)


class ScoreboardIdentity(BaseModel):
    """The user's SSI Scoreboard identity, shared across projects.

    All fields are optional except ``shooter_id`` so the SPA can save a
    partial identity (e.g. user pinned themselves but never set a custom
    base URL). The scoreboard import flow uses this as the default 'me'
    competitor; per-project overrides remain possible (matches where the
    user shoots a different division / club).
    """

    schema_version: int = SCHEMA_VERSION
    shooter_id: int
    display_name: str | None = None
    division: str | None = None
    club: str | None = None
    base_url: str | None = None


class GlobalPrefs(BaseModel):
    """Global preferences read from ``config.yaml``.

    Tiny on purpose -- only fields that genuinely don't belong on a
    per-project model live here. Add sparingly; per-project state is the
    default and project.json should keep winning ties.
    """

    schema_version: int = SCHEMA_VERSION
    theme: str | None = None
    default_trim_mode: str | None = None
    last_scoreboard_url: str | None = None


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------


def is_disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip() not in ("", "0", "false", "False")


def user_config_dir() -> Path:
    """Resolve the user-config directory path. Does not create it."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("linux"):
        xdg = os.environ.get(ENV_XDG)
        if xdg:
            return Path(xdg).expanduser() / "splitsmith"
        return Path.home() / ".config" / "splitsmith"
    return Path.home() / ".splitsmith"


def _ensure_dir() -> Path | None:
    """Return the directory, creating it if needed. ``None`` if disabled
    or creation failed (permission denied, read-only FS).
    """
    if is_disabled():
        return None
    target = user_config_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create user config dir %s: %s", target, exc)
        return None
    return target


# ---------------------------------------------------------------------------
# Atomic JSON / YAML helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace``.

    Mirrors the pattern used by ``MatchProject.save`` so a crashed write
    can never leave a half-written config behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable user-config file %s: %s", path, exc)
        return None


def _read_yaml(path: Path) -> Any | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Ignoring unreadable user-config file %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Recent projects
# ---------------------------------------------------------------------------


def _projects_path() -> Path:
    return user_config_dir() / PROJECTS_FILENAME


def _load_projects_index() -> ProjectsIndex:
    if is_disabled():
        return ProjectsIndex()
    raw = _read_json(_projects_path())
    if not isinstance(raw, dict):
        return ProjectsIndex()
    try:
        return ProjectsIndex.model_validate(raw)
    except Exception as exc:
        logger.warning("Discarding malformed %s: %s", PROJECTS_FILENAME, exc)
        return ProjectsIndex()


def _save_projects_index(index: ProjectsIndex) -> None:
    target = _ensure_dir()
    if target is None:
        return
    payload = json.dumps(index.model_dump(mode="json"), indent=2, sort_keys=True)
    try:
        _atomic_write_text(target / PROJECTS_FILENAME, payload)
    except OSError as exc:
        logger.warning("Could not write %s: %s", PROJECTS_FILENAME, exc)


def record_project_open(path: Path, name: str) -> None:
    """Append or refresh a project entry in ``projects.json``.

    Resolves ``path`` so two opens of the same project via different
    relative paths collapse to one entry. Moves the entry to the front
    of the list and trims to ``RECENT_PROJECTS_LIMIT``. No-op if the
    user-config directory is disabled or unwritable.
    """
    if is_disabled():
        return
    resolved = str(Path(path).expanduser().resolve())
    now = datetime.now(UTC)
    index = _load_projects_index()
    remaining = [p for p in index.projects if p.path != resolved]
    updated = [RecentProject(path=resolved, name=name, last_opened_at=now), *remaining]
    if len(updated) > RECENT_PROJECTS_LIMIT:
        updated = updated[:RECENT_PROJECTS_LIMIT]
    index.projects = updated
    index.schema_version = SCHEMA_VERSION
    _save_projects_index(index)


def get_recent_projects() -> list[RecentProject]:
    """Return the recent-projects list, most-recent first.

    Entries whose ``path`` no longer exists on disk are kept (the issue
    asks us not to auto-prune; the SPA can flag stale entries).
    """
    return list(_load_projects_index().projects)


def remove_recent_project(path: Path) -> bool:
    """Drop one entry from the recent-projects list. Returns ``True`` if
    something was removed. Used by the SPA's "forget this project"
    affordance so users can prune entries the daemon won't auto-delete.
    """
    if is_disabled():
        return False
    resolved = str(Path(path).expanduser().resolve())
    index = _load_projects_index()
    before = len(index.projects)
    index.projects = [p for p in index.projects if p.path != resolved]
    if len(index.projects) == before:
        return False
    _save_projects_index(index)
    return True


# ---------------------------------------------------------------------------
# Scoreboard identity
# ---------------------------------------------------------------------------


def _scoreboard_path() -> Path:
    return user_config_dir() / SCOREBOARD_FILENAME


def load_scoreboard_identity() -> ScoreboardIdentity | None:
    if is_disabled():
        return None
    raw = _read_json(_scoreboard_path())
    if not isinstance(raw, dict):
        return None
    try:
        return ScoreboardIdentity.model_validate(raw)
    except Exception as exc:
        logger.warning("Discarding malformed %s: %s", SCOREBOARD_FILENAME, exc)
        return None


def save_scoreboard_identity(identity: ScoreboardIdentity) -> None:
    target = _ensure_dir()
    if target is None:
        return
    identity = identity.model_copy(update={"schema_version": SCHEMA_VERSION})
    payload = json.dumps(identity.model_dump(mode="json"), indent=2, sort_keys=True)
    try:
        _atomic_write_text(target / SCOREBOARD_FILENAME, payload)
    except OSError as exc:
        logger.warning("Could not write %s: %s", SCOREBOARD_FILENAME, exc)


def clear_scoreboard_identity() -> None:
    if is_disabled():
        return
    path = _scoreboard_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not delete %s: %s", SCOREBOARD_FILENAME, exc)


# ---------------------------------------------------------------------------
# Global preferences (config.yaml)
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    return user_config_dir() / CONFIG_FILENAME


def load_global_prefs() -> GlobalPrefs:
    if is_disabled():
        return GlobalPrefs()
    raw = _read_yaml(_config_path())
    if not isinstance(raw, dict):
        return GlobalPrefs()
    try:
        return GlobalPrefs.model_validate(raw)
    except Exception as exc:
        logger.warning("Discarding malformed %s: %s", CONFIG_FILENAME, exc)
        return GlobalPrefs()


def save_global_prefs(prefs: GlobalPrefs) -> None:
    target = _ensure_dir()
    if target is None:
        return
    prefs = prefs.model_copy(update={"schema_version": SCHEMA_VERSION})
    payload = yaml.safe_dump(prefs.model_dump(mode="json"), sort_keys=True)
    try:
        _atomic_write_text(target / CONFIG_FILENAME, payload)
    except OSError as exc:
        logger.warning("Could not write %s: %s", CONFIG_FILENAME, exc)
