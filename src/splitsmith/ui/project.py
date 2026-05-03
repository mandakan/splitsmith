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

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from .. import video_match
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
    # The canonical recording-finished time used by the match heuristic
    # (``video_match.video_timestamp``: ``st_birthtime`` when available, else
    # ``st_mtime``; UTC-normalized). Captured at registration so the SPA
    # match-window timeline and the classifier see the same value, even after
    # the source goes offline (USB unplugged, drive moved). ``None`` for
    # projects registered before this field existed -- the UI degrades by
    # omitting the tick rather than guessing.
    match_timestamp: datetime | None = None
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def video_id(self) -> str:
        """Stable URL-safe identifier derived from ``path``.

        Used by per-video API endpoints (``/api/stages/{n}/videos/{video_id}/...``)
        and by per-video cache filenames (audio WAV, trimmed MP4) so each video
        on a stage gets its own cache slot. Hash is a 12-char blake2s digest of
        the stored path string -- stable across restarts, project reloads, and
        re-registration of the same source.

        Surfaced as a computed field on the wire so the SPA can route
        per-video requests without re-implementing the hash client-side.
        """
        return hashlib.blake2s(str(self.path).encode("utf-8"), digest_size=6).hexdigest()


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

    def find_video_by_id(self, video_id: str) -> StageVideo | None:
        """Locate a video on this stage by its :attr:`StageVideo.video_id`.

        Returns ``None`` when no video on this stage matches. Per-video beep
        endpoints use this to resolve the URL-bound id back to the stored
        ``StageVideo`` so they can mutate beep fields and trigger jobs.
        """
        for v in self.videos:
            if v.video_id == video_id:
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


class StageExportStatus(BaseModel):
    """Per-stage audit + export status for the Analysis & Export overview.

    Inferred (no explicit "Done" button per the v1 contract): a stage is
    ``ready_to_export`` when its primary has run beep + trim + shot detect
    and the audit JSON has at least one shot. ``has_exports`` flips when
    the user has clicked Generate at least once for this stage; the SPA
    uses it to badge stages with stale exports vs. fresh ones.
    """

    stage_number: int
    stage_name: str
    skipped: bool
    has_primary: bool
    primary_processed: dict[str, bool]
    audit_shot_count: int
    # Size of the detector's full candidate pool (``_candidates_pending_audit.
    # candidates`` in the audit JSON). NOT "pending"; once shot detection has
    # run, every candidate is either in ``shots[]`` (kept) or implicitly
    # rejected. The SPA renders this as "X shots audited from Y candidates"
    # so the math makes sense at a glance.
    total_candidate_count: int
    audit_path: Path | None
    # The video reference the SPA renders. Prefers the lossless trim under
    # ``exports/``; falls back to the audit-mode short-GOP copy from
    # ``trimmed/`` only when no lossless trim exists yet, so the user
    # always has *something* trimmed to inspect. ``lossless_trim_present``
    # disambiguates "this is the deliverable" vs "this is the scrub cache".
    trimmed_video_path: Path | None
    lossless_trim_present: bool = False
    csv_path: Path | None
    fcpxml_path: Path | None
    report_path: Path | None
    # Pre-rendered alpha overlay MOV (issue #45). ``None`` until the user
    # ticks the Overlay toggle on Generate at least once for this stage.
    # The FCPXML references this file as a connected clip on V2 when it
    # exists; absent, the FCPXML is unchanged.
    overlay_path: Path | None = None
    has_exports: bool
    last_export_at: datetime | None
    ready_to_export: bool
    # Whether the primary's source video resolves to a present file.
    # ``False`` typically means the symlink under ``raw/`` is dangling
    # because external storage is disconnected; the SPA badges the row
    # so the user knows Generate will degrade (CSV/report only) without
    # having to click first. ``None`` when the stage has no primary.
    source_reachable: bool | None = None


class StageMatchWindow(BaseModel):
    """One stage's match window for the SPA timeline (issue #13).

    The window itself is computed by :func:`video_match.match_window`; the
    SPA renders the band as ``[lower, upper]`` on its timeline. ``upper`` is
    always ``stage.scorecard_updated_at`` because the heuristic's window is
    asymmetric (scorecard typed *after* the run).
    """

    stage_number: int
    scorecard_updated_at: datetime | None
    tolerance_minutes: int
    lower: datetime | None
    upper: datetime | None


class VideoMatchAnalysisEntry(BaseModel):
    """Per-video classification against the project's stages.

    ``classification`` is one of ``in_window`` (lands in exactly one stage),
    ``contested`` (lands in multiple stages -- the SPA flags this), ``orphan``
    (lands in no stage's window -- likely warmup / neighbour-bay), or
    ``no_timestamp`` (the source was offline at registration; the row still
    renders, but no tick).
    """

    path: Path
    timestamp: datetime | None
    classification: str  # video_match.VideoClassification
    stage_numbers: list[int]


class MatchAnalysis(BaseModel):
    """Project-level match analysis exposed at GET /api/project/match-analysis.

    Single source of truth for the SPA's match-window timeline: tolerance,
    per-stage windows, and per-video classification all flow from
    :mod:`video_match`. Future improvements (per-stage tolerance, ML scorers,
    confidence bands) extend this model rather than duplicating policy in
    the SPA.
    """

    tolerance_minutes: int
    stages: list[StageMatchWindow]
    videos: list[VideoMatchAnalysisEntry]


class MatchProject(BaseModel):
    """Top-level on-disk match project."""

    schema_version: int = SCHEMA_VERSION
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    competitor_name: str | None = None
    scoreboard_match_id: str | None = None
    # SSI ``content_type`` tier for the linked match (matches the integer the
    # ``ScoreboardClient`` Protocol expects). Populated when the project is
    # bootstrapped via the SSI v1 path (drop-JSON or live fetch); ``None`` for
    # legacy projects that were imported via the older ``examples/``-shaped
    # scoreboard JSON.
    scoreboard_content_type: int | None = None
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

    def export_overview(self, root: Path) -> list[StageExportStatus]:
        """Per-stage audit + export status for the Analysis & Export screen.

        Pure: stat-only inspection of the audit/exports directories; never
        re-runs detection. The returned list mirrors :attr:`stages` order so
        the SPA can iterate cards directly.
        """
        from . import exports as exports_mod  # local: avoid import cycle

        audit_dir = self.audit_path(root)
        exports_dir = self.exports_path(root)
        trimmed_dir = self.trimmed_path(root)

        out: list[StageExportStatus] = []
        for stage in self.stages:
            primary = stage.primary()
            audit_file = audit_dir / f"stage{stage.stage_number}.json"
            shot_count = 0
            total_candidates = 0
            if audit_file.exists():
                try:
                    raw = json.loads(audit_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}
                shots = raw.get("shots") if isinstance(raw, dict) else None
                if isinstance(shots, list):
                    shot_count = len(shots)
                cand_block = raw.get("_candidates_pending_audit") if isinstance(raw, dict) else None
                if isinstance(cand_block, dict):
                    cands = cand_block.get("candidates")
                    if isinstance(cands, list):
                        total_candidates = len(cands)

            base = f"stage{stage.stage_number}_{exports_mod._slugify(stage.stage_name)}"
            csv_p = exports_dir / f"{base}_splits.csv"
            fcpxml_p = exports_dir / f"{base}.fcpxml"
            report_p = exports_dir / f"{base}_report.txt"
            overlay_p = exports_dir / f"{base}_overlay.mov"
            # The "trimmed video" surfaced to the Export screen is the
            # lossless trim in exports/ (the FCP-bound deliverable), not
            # the audit-mode short-GOP scrub copy in <project>/trimmed/.
            # Reference trimmed_dir only when the lossless one is missing
            # so the row at least shows the user *something* trimmed.
            lossless_trim_p = exports_dir / f"{base}_trimmed.mp4"
            audit_trim_p = trimmed_dir / f"stage{stage.stage_number}_trimmed.mp4"
            trimmed_p = (
                lossless_trim_p
                if lossless_trim_p.exists()
                else (audit_trim_p if audit_trim_p.exists() else lossless_trim_p)
            )

            csv_exists = csv_p.exists()
            fcpxml_exists = fcpxml_p.exists()
            report_exists = report_p.exists()
            trim_exists = lossless_trim_p.exists()
            overlay_exists = overlay_p.exists()
            has_exports = (
                csv_exists or fcpxml_exists or report_exists or trim_exists or overlay_exists
            )
            last_export_at: datetime | None = None
            if has_exports:
                mtimes = [
                    p.stat().st_mtime
                    for p in (csv_p, fcpxml_p, report_p, lossless_trim_p, overlay_p)
                    if p.exists()
                ]
                if mtimes:
                    last_export_at = datetime.fromtimestamp(max(mtimes), tz=UTC)

            processed = (
                dict(primary.processed)
                if primary is not None
                else {"beep": False, "shot_detect": False, "trim": False}
            )
            ready_to_export = (
                primary is not None
                and processed.get("beep", False)
                and processed.get("trim", False)
                and processed.get("shot_detect", False)
                and shot_count > 0
            )
            source_reachable: bool | None = None
            if primary is not None:
                try:
                    src = self.resolve_video_path(root, primary.path)
                    source_reachable = src.exists()
                except OSError:
                    source_reachable = False

            out.append(
                StageExportStatus(
                    stage_number=stage.stage_number,
                    stage_name=stage.stage_name,
                    skipped=stage.skipped,
                    has_primary=primary is not None,
                    primary_processed=processed,
                    audit_shot_count=shot_count,
                    total_candidate_count=total_candidates,
                    audit_path=audit_file if audit_file.exists() else None,
                    trimmed_video_path=trimmed_p if trimmed_p.exists() else None,
                    csv_path=csv_p if csv_exists else None,
                    fcpxml_path=fcpxml_p if fcpxml_exists else None,
                    report_path=report_p if report_exists else None,
                    overlay_path=overlay_p if overlay_exists else None,
                    lossless_trim_present=trim_exists,
                    has_exports=has_exports,
                    last_export_at=last_export_at,
                    ready_to_export=ready_to_export,
                    source_reachable=source_reachable,
                )
            )
        return out

    def match_analysis(self, *, config: VideoMatchConfig | None = None) -> MatchAnalysis:
        """Run the canonical match heuristic over the project's stored
        timestamps and return per-stage windows + per-video classifications.

        Pure (no I/O): operates on ``StageVideo.match_timestamp`` captured at
        registration. Reuses :mod:`video_match` so any improvement to the
        heuristic flows into the SPA without duplicating logic. Stages
        without a ``scorecard_updated_at`` (placeholders) are surfaced with
        ``lower=upper=None`` -- the SPA renders the row but skips the band.
        """
        cfg = config or VideoMatchConfig()
        stage_data: list[StageData] = [
            StageData(
                stage_number=s.stage_number,
                stage_name=s.stage_name,
                time_seconds=s.time_seconds,
                scorecard_updated_at=s.scorecard_updated_at,
            )
            for s in self.stages
            if s.scorecard_updated_at is not None
        ]

        windows = [
            StageMatchWindow(
                stage_number=s.stage_number,
                scorecard_updated_at=s.scorecard_updated_at,
                tolerance_minutes=cfg.tolerance_minutes,
                lower=(
                    video_match.match_window(s.scorecard_updated_at, cfg.tolerance_minutes)[0]
                    if s.scorecard_updated_at is not None
                    else None
                ),
                upper=s.scorecard_updated_at,
            )
            for s in self.stages
        ]

        entries: list[VideoMatchAnalysisEntry] = []
        for video in self.all_videos():
            classification, stages = video_match.classify_video_against_stages(
                video.match_timestamp, stage_data, cfg.tolerance_minutes
            )
            entries.append(
                VideoMatchAnalysisEntry(
                    path=video.path,
                    timestamp=video.match_timestamp,
                    classification=classification,
                    stage_numbers=stages,
                )
            )

        return MatchAnalysis(
            tolerance_minutes=cfg.tolerance_minutes,
            stages=windows,
            videos=entries,
        )

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

    def populate_from_match_data(
        self,
        match_data: Any,
        *,
        overwrite: bool = False,
    ) -> None:
        """Populate ``stages`` (and metadata) from a parsed SSI v1 ``MatchData``.

        Used by the offline drop-JSON path and the online HTTP path -- both
        produce a ``MatchData`` that flows through here so the resulting
        ``MatchProject`` shape is identical regardless of source (acceptance
        criterion from issue #14).

        Per-competitor stage scores are not part of ``MatchData`` (the v1 API
        returns the match shell + competitor list only), so the new stages
        are flagged ``placeholder=True`` with ``time_seconds=0.0`` /
        ``scorecard_updated_at=None``. A subsequent legacy ``import_scoreboard``
        with a per-competitor stages payload overlays the timing data while
        preserving any video assignments by ``stage_number``.

        Raises :class:`ScoreboardImportConflictError` when real (non-placeholder)
        stages already exist and ``overwrite`` is ``False`` -- replacing them
        would orphan video assignments, so the default is to refuse.
        """
        # Local import to avoid a module-level cycle (scoreboard package
        # depends on the project layout for project-relative paths).
        from splitsmith.ui.scoreboard.local import _parse_ssi_url
        from splitsmith.ui.scoreboard.models import MatchData

        if not isinstance(match_data, MatchData):
            match_data = MatchData.model_validate(match_data)

        real_stages = [s for s in self.stages if not s.placeholder]
        if real_stages and not overwrite:
            raise ScoreboardImportConflictError(
                "project already has scoreboard-backed stages; pass "
                "overwrite=True to replace (this orphans current video "
                "assignments)"
            )

        self.name = match_data.name
        ct, mid = _parse_ssi_url(match_data.ssi_url)
        if mid is not None:
            self.scoreboard_match_id = str(mid)
        if ct is not None:
            self.scoreboard_content_type = ct
        if match_data.date:
            try:
                self.match_date = date.fromisoformat(match_data.date[:10])
            except ValueError:
                pass

        videos_by_stage: dict[int, list[StageVideo]] = {}
        if not real_stages:
            for s in self.stages:
                if s.videos:
                    videos_by_stage[s.stage_number] = list(s.videos)

        new_stages: list[StageEntry] = [
            StageEntry(
                stage_number=stage.stage_number,
                stage_name=stage.name,
                time_seconds=0.0,
                scorecard_updated_at=None,
                videos=videos_by_stage.get(stage.stage_number, []),
                placeholder=True,
            )
            for stage in match_data.stages
        ]
        new_stages.sort(key=lambda s: s.stage_number)
        self.stages = new_stages

        keep_numbers = {s.stage_number for s in new_stages}
        for stage_number, videos in videos_by_stage.items():
            if stage_number in keep_numbers:
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
        # return it (backfilling match_timestamp if missing -- old projects
        # didn't store it, and we'd rather populate the timeline tick on
        # re-scan than force the user to re-register from scratch).
        existing = self.find_video(stored)
        if existing is not None:
            video = existing[1]
            if video.match_timestamp is None:
                try:
                    video.match_timestamp = video_match.video_timestamp(source, prefer_ctime=True)
                except OSError:
                    pass
            return video

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

        try:
            match_ts = video_match.video_timestamp(source, prefer_ctime=True)
        except OSError:
            match_ts = None
        video = StageVideo(path=stored, match_timestamp=match_ts)
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

    def primary_swap_warns(self, root: Path, *, stage_number: int) -> bool:
        """Return True when swapping the current primary on ``stage_number``
        would discard audit work that the user explicitly produced.

        "Audit work" here means the per-stage audit JSON exists *and* records
        at least one shot (auto-detected candidate or manual placement). The
        bare ``_candidates_pending_audit`` block from a fresh detection run
        does not count -- detection re-runs after the swap and would produce
        a fresh candidate list anyway.
        """
        audit_file = self.audit_path(root) / f"stage{stage_number}.json"
        if not audit_file.exists():
            return False
        try:
            data = json.loads(audit_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Unreadable audit -> safer to warn than silently overwrite.
            return True
        shots = data.get("shots") if isinstance(data, dict) else None
        return bool(shots)

    def swap_primary(
        self,
        path: Path,
        *,
        root: Path,
        stage_number: int,
        backup_audit: bool = True,
    ) -> StageVideo:
        """Promote ``path`` to primary on ``stage_number`` with audit-safe
        side-effects. Differs from ``assign_video(role="primary")`` in two
        ways: (1) when the current primary's processed flags are set, those
        flags are cleared on the new primary so detection re-runs from
        scratch; (2) when ``backup_audit`` is True and an audit JSON exists,
        the file is renamed to ``stage<N>.json.bak`` so the user can recover.

        Returns the newly-promoted ``StageVideo``. Raises ``KeyError`` if the
        target stage or video doesn't exist.
        """
        target = self.stage(stage_number)
        located = self.find_video(path)
        if located is None:
            raise KeyError(f"video {path} not registered with project")

        # Move-then-promote uses the standard assign_video path. If the video
        # was already on this stage, it gets pulled out and re-attached; the
        # old primary is demoted inside assign_video. New primary's processed
        # flags are reset because the audio source changed.
        new_primary = self.assign_video(path, to_stage_number=stage_number, role="primary")
        new_primary.processed = {"beep": False, "shot_detect": False, "trim": False}
        new_primary.beep_time = None
        new_primary.beep_source = None
        new_primary.beep_peak_amplitude = None
        new_primary.beep_duration_ms = None
        new_primary.beep_candidates = []

        if backup_audit:
            audit_file = self.audit_path(root) / f"stage{stage_number}.json"
            if audit_file.exists():
                bak = audit_file.with_suffix(audit_file.suffix + ".bak")
                # If a previous .bak already exists, overwrite it: keeping
                # multiple generations is more confusing than helpful, and
                # the user's most recent audit is the most useful to recover.
                try:
                    if bak.exists():
                        bak.unlink()
                    audit_file.rename(bak)
                except OSError:
                    # Fall back to a copy-then-delete via best-effort; if
                    # even that fails, leave audit in place rather than
                    # destroying it. The caller can decide what to do.
                    pass

        # The fresh primary needs a re-trim too (audio source changed). The
        # caller's job is to nudge the worker; here we just clear the flags
        # so the UI / pipeline knows to re-process.
        for v in target.videos:
            if v.role == "secondary":
                # Secondaries' beep alignment was relative to the old
                # primary's audio. They now need re-detection too.
                v.processed["beep"] = False

        return new_primary

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

        # Cache files are keyed on role: primary keeps the legacy
        # ``stage<N>_primary.wav`` / ``stage<N>_trimmed.mp4`` filenames;
        # non-primary cameras live under ``stage<N>_cam_<video_id>.*`` so
        # each angle has its own slot. Either way the removal plan points
        # at the per-video files so the endpoint sweeps them on disk.
        audio_cache: Path | None = None
        trimmed_cache: Path | None = None
        if stage_number is not None:
            if was_primary:
                audio_cache = self.audio_path(root) / f"stage{stage_number}_primary.wav"
                trimmed_cache = self.trimmed_path(root) / f"stage{stage_number}_trimmed.mp4"
            else:
                vid = video.video_id
                audio_cache = self.audio_path(root) / f"stage{stage_number}_cam_{vid}.wav"
                trimmed_cache = (
                    self.trimmed_path(root) / f"stage{stage_number}_cam_{vid}_trimmed.mp4"
                )

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
