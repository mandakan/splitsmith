"""Match-project on-disk model.

A *match* is the persistence unit. The project directory IS the match. Videos
can be added incrementally (head-cam now, bay-cam from a friend a day later);
secondary videos only need beep + trim, anchored to the primary's already-
audited timeline (issue #11).

On-disk layout::

    <project-root>/
      project.json              # MatchProject metadata + index
      raw/                      # original video files (or symlinks)
      audio/                    # extracted .wav cache
      trimmed/                  # per-stage trimmed MP4s
      audit/                    # per-stage audit JSON
      exports/                  # CSV / FCPXML / report.txt
      scoreboard/               # cached SSI JSON + raw fetch responses

All writes to ``project.json`` and per-stage audit JSONs go through
``atomic_write_json`` so a crashed save can never corrupt project state. This
is the foundation that makes incremental ingest safe.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

PROJECT_FILE = "project.json"
SUBDIRS = ("raw", "audio", "trimmed", "audit", "exports", "scoreboard")

# Bumped when the on-disk schema changes in a backwards-incompatible way.
SCHEMA_VERSION = 1


VideoRole = Literal["primary", "secondary", "ignored"]


class StageVideo(BaseModel):
    """One video file assigned to a stage.

    ``role`` drives the pipeline: primary runs the full pipeline (beep + shot
    detect + trim), secondary only needs beep + trim (anchored to primary's
    timeline), ignored is skipped entirely.

    ``processed`` is the source of truth for what's been done; the UI computes
    per-stage status by scanning these flags rather than re-running detection.
    """

    path: Path
    role: VideoRole = "secondary"
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    processed: dict[str, bool] = Field(
        default_factory=lambda: {"beep": False, "shot_detect": False, "trim": False}
    )
    beep_time: float | None = None
    notes: str = ""


class StageEntry(BaseModel):
    """A stage in the match: scoreboard data + assigned videos + audit status."""

    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime | None = None
    videos: list[StageVideo] = Field(default_factory=list)
    skipped: bool = False

    def primary(self) -> StageVideo | None:
        """Return the primary video, or ``None`` if no video is the primary yet."""
        for v in self.videos:
            if v.role == "primary":
                return v
        return None


class MatchProject(BaseModel):
    """Top-level on-disk match project."""

    schema_version: int = SCHEMA_VERSION
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    competitor_name: str | None = None
    scoreboard_match_id: str | None = None
    stages: list[StageEntry] = Field(default_factory=list)

    @classmethod
    def init(cls, root: Path, *, name: str) -> MatchProject:
        """Create a fresh project at ``root`` with the standard subdirectory layout.

        Idempotent: if ``project.json`` already exists, the existing project is
        loaded and returned unchanged. Subdirectories are created if missing.
        """
        root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (root / sub).mkdir(exist_ok=True)
        existing = root / PROJECT_FILE
        if existing.exists():
            return cls.load(root)
        project = cls(name=name)
        project.save(root)
        return project

    @classmethod
    def load(cls, root: Path) -> MatchProject:
        """Load the project from ``root``. Raises ``FileNotFoundError`` if missing."""
        path = root / PROJECT_FILE
        if not path.exists():
            raise FileNotFoundError(f"no project.json in {root}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, root: Path) -> None:
        """Atomically persist the project to ``root/project.json``."""
        self.updated_at = datetime.now(UTC)
        atomic_write_json(root / PROJECT_FILE, self.model_dump(mode="json"))

    def stage(self, stage_number: int) -> StageEntry:
        """Return the stage with this number; raises ``KeyError`` if absent."""
        for s in self.stages:
            if s.stage_number == stage_number:
                return s
        raise KeyError(f"no stage {stage_number} in project {self.name!r}")


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp + rename).

    On POSIX, ``os.replace`` is atomic within the same filesystem, so an
    interrupted save never leaves a half-written file at the destination. The
    temp file lives in the same directory as the destination to guarantee that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=_json_default)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")
