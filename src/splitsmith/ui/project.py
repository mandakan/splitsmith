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
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import BeepCandidate, StageData, VideoMatchConfig
from ..video_match import match_videos_to_stages

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}

PROJECT_FILE = "project.json"
SUBDIRS = ("raw", "audio", "trimmed", "audit", "exports", "scoreboard", "probes", "thumbs")

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
    # Provenance for ``beep_time``: who set it. ``None`` = not yet detected;
    # ``"auto"`` = beep_detect.detect_beep() result; ``"manual"`` = user override
    # via the ingest screen. Manual overrides survive subsequent auto-detect
    # attempts unless the user explicitly forces re-detection (issue #22).
    beep_source: Literal["auto", "manual"] | None = None
    # Diagnostic outputs from ``detect_beep`` -- not used by the pipeline but
    # surfaced in the UI to help the user judge auto-detection confidence.
    beep_peak_amplitude: float | None = None
    beep_duration_ms: float | None = None
    # Ranked alternative candidates from the most recent auto-detection run
    # (silence-preference score, descending). The production UI offers these
    # as one-click alternatives to the auto-winner so the user rarely has to
    # type a timestamp by hand. Cleared on manual override / clear (issue #22).
    beep_candidates: list[BeepCandidate] = Field(default_factory=list)
    notes: str = ""


class StageEntry(BaseModel):
    """A stage in the match: scoreboard data + assigned videos + audit status."""

    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime | None = None
    videos: list[StageVideo] = Field(default_factory=list)
    skipped: bool = False
    # Placeholder stages are created without a scoreboard ("I shot 6 stages,
    # let me start ingesting"). They carry stage_number + a generic name so the
    # rest of the pipeline works; importing a real scoreboard later overlays
    # the proper metadata while preserving any video assignments. See
    # MatchProject.init_placeholder_stages and import_scoreboard.
    placeholder: bool = False

    def primary(self) -> StageVideo | None:
        """Return the primary video, or ``None`` if no video is the primary yet."""
        for v in self.videos:
            if v.role == "primary":
                return v
        return None


class ScoreboardImportConflictError(Exception):
    """Raised when ``import_scoreboard`` would overwrite existing stage data."""


class RemovalPlan(BaseModel):
    """Side-effect description returned by :meth:`MatchProject.remove_video`.

    The model mutates project state (drops the StageVideo, optionally clears
    audit) but never touches the filesystem. The endpoint walks this plan to
    do the actual deletes -- keeping pure model logic separate from I/O so
    tests can exercise the model without disk fixtures.
    """

    video_path: Path  # the path that was removed (project-relative or absolute)
    raw_link_path: Path  # symlink under raw_dir to unlink
    audio_cache_path: Path | None = None  # WAV cache to clear if cached
    trimmed_cache_path: Path | None = None  # trimmed clip to clear if cached
    audit_path: Path | None = None  # stage audit JSON to clear when reset_audit
    was_primary: bool = False
    stage_number: int | None = None
    audit_reset: bool = False  # caller-facing flag: did we wipe stage audit?


class MatchProject(BaseModel):
    """Top-level on-disk match project."""

    schema_version: int = SCHEMA_VERSION
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    competitor_name: str | None = None
    scoreboard_match_id: str | None = None
    # Optional match date (the day the match was shot). Used as a hint for the
    # SSI Scoreboard suggestion flow and surfaced in the UI. Auto-filled from
    # the earliest video mtime when starting source-first; overwritten by
    # ``import_scoreboard`` when a real scoreboard arrives.
    match_date: date | None = None
    stages: list[StageEntry] = Field(default_factory=list)
    # Videos registered with the project but not yet assigned to any stage.
    # The Sub 2 (#13) ingest screen surfaces these in a "tray" so the user can
    # drag them onto stages or mark them as ignored.
    unassigned_videos: list[StageVideo] = Field(default_factory=list)
    # Last folder the user scanned for videos. Persisted so the folder picker
    # in the ingest UI can default back here on the next scan.
    last_scanned_dir: str | None = None
    # Storage path overrides (issue #23). All four are optional; ``None`` means
    # "use the default subdirectory under the project root" (raw / audio /
    # trimmed / exports). Relative paths are resolved against the project root.
    # Absolute paths are used as-is so users can put heavy intermediates on a
    # scratch SSD or outputs next to a Final Cut Pro library.
    raw_dir: str | None = None
    audio_dir: str | None = None
    trimmed_dir: str | None = None
    exports_dir: str | None = None
    # Probe + thumbnail caches (issue #24). Same override semantics as the
    # other path fields: ``None`` -> default subdir under project root,
    # relative -> resolved against project root, absolute -> as-is.
    probes_dir: str | None = None
    thumbs_dir: str | None = None
    # Audit-mode trim buffers (#15 / #16). Pre is the pad before the beep;
    # post is the pad after the stage end. Asymmetric defaults are allowed;
    # users typically want longer post-buffers for FCP fades. Both default
    # to 5.0 s -- the historical single-knob value.
    trim_pre_buffer_seconds: float = 5.0
    trim_post_buffer_seconds: float = 5.0
    # Audit-mode encoder selection (issue #26). ``"auto"`` probes ffmpeg
    # for ``h264_videotoolbox`` on macOS (~10x faster on 4K Insta360
    # footage) and falls back to ``libx264`` everywhere else. Override to
    # a specific encoder name (e.g. ``"libx264"``) to pin the choice; the
    # next trim job uses the new value.
    trim_audit_encoder: str = "auto"

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

    # ------------------------------------------------------------------
    # Storage path resolvers (issue #23)
    # ------------------------------------------------------------------
    #
    # Each resolver returns an absolute, mkdir-ready path. ``None`` overrides
    # default to ``<root>/<subdir>`` so existing projects keep working with
    # zero changes. Relative overrides are resolved against the project root,
    # which keeps "zip and share" projects portable. Absolute overrides are
    # used as-is so users can keep heavy intermediates off the project drive.

    def _resolve_dir(self, root: Path, override: str | None, default_subdir: str) -> Path:
        if override is None:
            return root / default_subdir
        candidate = Path(override).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    def raw_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.raw_dir, "raw")

    def audio_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.audio_dir, "audio")

    def trimmed_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.trimmed_dir, "trimmed")

    def exports_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.exports_dir, "exports")

    def probes_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.probes_dir, "probes")

    def thumbs_path(self, root: Path) -> Path:
        return self._resolve_dir(root, self.thumbs_dir, "thumbs")

    def audit_path(self, root: Path) -> Path:
        # Audit output is always project-local; surface a resolver for the
        # remove-video flow so it can clean up the per-stage JSON.
        return root / "audit"

    # ------------------------------------------------------------------
    # Video registry helpers (Sub 2 / #13)
    # ------------------------------------------------------------------

    def all_videos(self) -> list[StageVideo]:
        """Every video known to the project, assigned or not."""
        out: list[StageVideo] = list(self.unassigned_videos)
        for s in self.stages:
            out.extend(s.videos)
        return out

    def find_video(self, path: Path) -> tuple[StageEntry | None, StageVideo] | None:
        """Locate a video by path. Returns ``(stage_or_None, video)`` or ``None``.

        ``stage`` is ``None`` when the video lives in ``unassigned_videos``.
        Path comparison uses string equality on the stored value (the project
        stores paths relative to the project root, so this is unambiguous).
        """
        target = str(path)
        for v in self.unassigned_videos:
            if str(v.path) == target:
                return None, v
        for s in self.stages:
            for v in s.videos:
                if str(v.path) == target:
                    return s, v
        return None

    def init_placeholder_stages(
        self,
        count: int,
        *,
        match_name: str | None = None,
        match_date: date | None = None,
    ) -> None:
        """Create ``count`` placeholder stages with no scoreboard data.

        Used when the user starts source-first ("I shot 6 stages, here are the
        videos"). Each placeholder gets ``stage_number = 1..count`` and a
        generic name; ``time_seconds`` is 0.0 and ``scorecard_updated_at`` is
        None, which keeps :meth:`auto_match` from misfiring (it filters stages
        without ``scorecard_updated_at``).

        If non-placeholder stages already exist, this raises
        :class:`ScoreboardImportConflictError` -- placeholders are a
        bootstrap-only concept and should not stomp real scoreboard data.
        Existing placeholders are replaced; any video assignments are moved
        back to ``unassigned_videos`` so the user can re-bind to the new
        layout.
        """
        if count < 1:
            raise ValueError("placeholder stage count must be >= 1")
        real = [s for s in self.stages if not s.placeholder]
        if real:
            raise ScoreboardImportConflictError(
                "project already has scoreboard-backed stages; "
                "clear them or import via overwrite first"
            )
        # Move any videos out of existing placeholders -- the new placeholder
        # layout might have a different stage count.
        for stage in self.stages:
            for video in stage.videos:
                video.role = "secondary"
                self.unassigned_videos.append(video)

        if match_name:
            self.name = match_name
        if match_date is not None:
            self.match_date = match_date

        self.stages = [
            StageEntry(
                stage_number=i,
                stage_name=f"Stage {i}",
                time_seconds=0.0,
                placeholder=True,
            )
            for i in range(1, count + 1)
        ]

    def import_scoreboard(self, raw: dict[str, Any], *, overwrite: bool = False) -> None:
        """Populate ``stages`` (and metadata) from a parsed SSI Scoreboard JSON.

        Picks the first competitor (multi-competitor support is v2 / out of
        scope per #11). Raises :class:`ScoreboardImportConflictError` if
        scoreboard-backed stages already exist and ``overwrite`` is ``False``;
        overwriting would orphan existing video assignments, so the default is
        to refuse.

        Placeholder stages (created via :meth:`init_placeholder_stages`) are
        always overlaid: video assignments keyed on ``stage_number`` are
        preserved, scoreboard metadata replaces the generic placeholder data,
        and the ``placeholder`` flag clears. If the scoreboard has fewer
        stages than the placeholders, any extras are dropped; videos in
        dropped extras are moved back to ``unassigned_videos`` so nothing is
        lost.
        """
        real_stages = [s for s in self.stages if not s.placeholder]
        if real_stages and not overwrite:
            raise ScoreboardImportConflictError(
                "project already has scoreboard-backed stages; pass "
                "overwrite=True to replace (this orphans current video "
                "assignments)"
            )
        match_meta = raw.get("match", {}) or {}
        competitors = raw.get("competitors") or []
        if not competitors:
            raise ValueError("no competitors in scoreboard JSON")
        primary_competitor = competitors[0]

        match_name = match_meta.get("name")
        if match_name:
            self.name = match_name
        self.scoreboard_match_id = (
            match_meta.get("id") or match_meta.get("match_id") or self.scoreboard_match_id
        )
        self.competitor_name = primary_competitor.get("name")

        # Overlay path: snapshot existing placeholders' videos by stage_number
        # so we can replant them into the matching scoreboard stage. If
        # ``overwrite`` was used to wipe real stages, those videos are *not*
        # preserved (overwrite is the user's explicit "orphan everything"
        # choice).
        videos_by_stage: dict[int, list[StageVideo]] = {}
        if not real_stages:
            for s in self.stages:
                if s.videos:
                    videos_by_stage[s.stage_number] = list(s.videos)

        new_stages: list[StageEntry] = []
        scoreboard_numbers: set[int] = set()
        for s in primary_competitor.get("stages", []):
            stage_data = StageData.model_validate(s)
            scoreboard_numbers.add(stage_data.stage_number)
            new_stages.append(
                StageEntry(
                    stage_number=stage_data.stage_number,
                    stage_name=stage_data.stage_name,
                    time_seconds=stage_data.time_seconds,
                    scorecard_updated_at=stage_data.scorecard_updated_at,
                    videos=videos_by_stage.get(stage_data.stage_number, []),
                )
            )
        new_stages.sort(key=lambda s: s.stage_number)
        self.stages = new_stages

        # Any placeholder videos whose stage_number didn't survive the import
        # land back in unassigned_videos -- the user can reassign manually.
        for stage_number, videos in videos_by_stage.items():
            if stage_number in scoreboard_numbers:
                continue
            for v in videos:
                v.role = "secondary"
                self.unassigned_videos.append(v)

    def register_video(
        self,
        source: Path,
        root: Path,
        *,
        link_mode: Literal["symlink", "copy"] = "symlink",
    ) -> StageVideo:
        """Register a video file with the project.

        The file is **referenced** -- a symlink (or copy as fallback on systems
        without symlink support) is placed under :meth:`raw_path` pointing at
        the original source. The original is never moved or duplicated by
        default. This works for USB-camera ingest: source on the cam, symlink
        in the project. When the cam is unplugged the symlink dangles
        temporarily but the project keeps working when it's plugged back in.

        The ``StageVideo`` is appended to ``unassigned_videos``; the caller is
        responsible for moving it onto a stage via :meth:`assign_video`. If a
        video at the same destination path is already registered, the existing
        entry is returned unchanged (idempotent).

        Raises ``FileNotFoundError`` if the source doesn't exist or isn't a
        video file (mp4 / mov / m4v).
        """
        source = source.expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"source video not found: {source}")
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"not a video file: {source}")

        raw_dir_abs = self.raw_path(root)
        raw_dir_abs.mkdir(parents=True, exist_ok=True)
        dest = raw_dir_abs / source.name

        # The path stored on the StageVideo is project-relative when raw_dir is
        # under the project root (the common case), absolute otherwise. This
        # keeps zip-and-share portable for default projects while letting
        # USB-cam / scratch-SSD setups still work.
        try:
            stored = dest.relative_to(root)
        except ValueError:
            stored = dest

        # Idempotency: if a video at this stored path is already registered,
        # return it.
        existing = self.find_video(stored)
        if existing is not None:
            return existing[1]

        # If something is already at the destination, leave it (don't clobber
        # what the user might have placed there themselves). Otherwise create
        # a symlink (preferred) or a copy.
        if not dest.exists():
            if link_mode == "symlink":
                try:
                    dest.symlink_to(source)
                except OSError:
                    # Fallback (Windows without dev mode, etc.): copy.
                    shutil.copy2(source, dest)
            else:
                shutil.copy2(source, dest)

        video = StageVideo(path=stored)
        self.unassigned_videos.append(video)
        return video

    def resolve_video_path(self, root: Path, video_path: Path) -> Path:
        """Resolve a ``StageVideo.path`` (which may be project-relative or
        absolute) to an absolute filesystem path."""
        return video_path if video_path.is_absolute() else root / video_path

    def assign_video(
        self,
        path: Path,
        *,
        to_stage_number: int | None,
        role: VideoRole = "secondary",
    ) -> StageVideo:
        """Move a video to a target stage (or back to unassigned if ``None``).

        - ``to_stage_number=None``: move to ``unassigned_videos`` regardless of
          ``role``.
        - ``to_stage_number=N, role="primary"``: there can be only one primary
          per stage; any existing primary is demoted to ``"secondary"``.

        Returns the moved ``StageVideo``. Raises ``KeyError`` if the video or
        stage doesn't exist.
        """
        located = self.find_video(path)
        if located is None:
            raise KeyError(f"video {path} not registered with project")
        current_stage, video = located

        # Detach from current location.
        if current_stage is None:
            self.unassigned_videos = [
                v for v in self.unassigned_videos if str(v.path) != str(video.path)
            ]
        else:
            current_stage.videos = [
                v for v in current_stage.videos if str(v.path) != str(video.path)
            ]

        # Reattach.
        if to_stage_number is None:
            video.role = "secondary"  # role is meaningless when unassigned
            self.unassigned_videos.append(video)
            return video

        target = self.stage(to_stage_number)
        if role == "primary":
            for v in target.videos:
                if v.role == "primary":
                    v.role = "secondary"
        video.role = role
        target.videos.append(video)
        return video

    def remove_video(
        self,
        path: Path,
        root: Path,
        *,
        reset_audit: bool = False,
    ) -> RemovalPlan:
        """Remove a registered video and return a :class:`RemovalPlan`.

        Drops the ``StageVideo`` from its stage or ``unassigned_videos``. The
        symlink under ``raw_dir`` is included in the plan so the caller can
        unlink it; the actual filesystem call is the caller's job (keeps this
        method pure-mutation on the model).

        ``reset_audit=True`` and the video was a primary clears the per-stage
        audit JSON path (``<root>/audit/stage<N>.json``) and resets the
        primary's ``processed`` flags. Default is ``False``: stage audit is
        preserved so a re-ingest of the same stage with a different file can
        pick up where the user left off.

        Raises ``KeyError`` if the video isn't registered with the project.
        """
        located = self.find_video(path)
        if located is None:
            raise KeyError(f"video {path} not registered with project")
        current_stage, video = located
        was_primary = video.role == "primary" and current_stage is not None
        stage_number = current_stage.stage_number if current_stage else None

        if current_stage is None:
            self.unassigned_videos = [
                v for v in self.unassigned_videos if str(v.path) != str(video.path)
            ]
        else:
            current_stage.videos = [
                v for v in current_stage.videos if str(v.path) != str(video.path)
            ]

        raw_link = self.resolve_video_path(root, video.path)

        audio_cache: Path | None = None
        trimmed_cache: Path | None = None
        if was_primary and stage_number is not None:
            audio_cache = self.audio_path(root) / f"stage{stage_number}_primary.wav"
            trimmed_cache = self.trimmed_path(root) / f"stage{stage_number}_trimmed.mp4"

        audit_path: Path | None = None
        audit_reset = False
        if reset_audit and stage_number is not None:
            audit_path = self.audit_path(root) / f"stage{stage_number}.json"
            audit_reset = True
            if was_primary and current_stage is not None:
                # No primary remains; reset processed flags on any other
                # primaries that may have been demoted (defensive -- the data
                # model only allows one primary at a time).
                for v in current_stage.videos:
                    v.processed = {"beep": False, "shot_detect": False, "trim": False}
                    v.beep_time = None
                    v.beep_source = None
                    v.beep_peak_amplitude = None
                    v.beep_duration_ms = None
                    v.beep_candidates = []

        return RemovalPlan(
            video_path=video.path,
            raw_link_path=raw_link,
            audio_cache_path=audio_cache,
            trimmed_cache_path=trimmed_cache,
            audit_path=audit_path,
            was_primary=was_primary,
            stage_number=stage_number,
            audit_reset=audit_reset,
        )

    def auto_match(
        self,
        root: Path,
        *,
        config: VideoMatchConfig | None = None,
    ) -> dict[int, Path]:
        """Run :func:`video_match.match_videos_to_stages` against unassigned videos
        and the project's stages.

        ``root`` is the project root directory; needed to resolve the videos'
        project-relative paths to real filesystem paths so ``os.stat`` works.

        Returns ``{stage_number: video_relative_path}`` for every confident
        match. **Does not mutate the project**; the caller decides whether to
        apply via :meth:`assign_video` (and with what role).
        """
        cfg = config or VideoMatchConfig()
        unassigned_abs: dict[Path, Path] = {}
        for v in self.unassigned_videos:
            # Resolve project-relative path against the project root, then
            # resolve symlinks so video_match.py's stat() reads the real file.
            abs_path = self.resolve_video_path(root, v.path).resolve()
            unassigned_abs[abs_path] = v.path
        if not unassigned_abs:
            return {}

        stage_data = [
            StageData(
                stage_number=s.stage_number,
                stage_name=s.stage_name,
                time_seconds=s.time_seconds,
                scorecard_updated_at=s.scorecard_updated_at or datetime.now(UTC),
            )
            for s in self.stages
            if s.scorecard_updated_at is not None
        ]
        if not stage_data:
            return {}

        result = match_videos_to_stages(list(unassigned_abs.keys()), stage_data, cfg)
        return {m.stage_number: unassigned_abs[m.video_path] for m in result.matches}


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
