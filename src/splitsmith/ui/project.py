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
import logging
import os
import shutil
import tempfile
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, computed_field

from .. import video_match
from ..automation import AutomationOverride
from ..config import BeepCandidate, StageData, StageRounds, VideoMatchConfig
from ..storage import Storage
from ..video_match import match_videos_to_stages

logger = logging.getLogger(__name__)

# Camera-make heuristic for the default ``StageVideo.camera_mount``
# (issue #143). The fixture schema's full ``CameraMount`` enum is much
# richer; we just need a coarse default that makes the per-camera-class
# threshold dispatch in #137 do the right thing for the common cases:
# headcams stay headcam, phones become handheld. Anything we don't
# recognise stays ``None`` and the user can set it manually.
_MAKE_TO_MOUNT_HEURISTIC: tuple[tuple[str, str], ...] = (
    ("apple", "hand"),
    ("samsung", "hand"),
    ("google", "hand"),
    ("oneplus", "hand"),
    ("xiaomi", "hand"),
    ("insta360", "head"),
    ("gopro", "head"),
)


def _heuristic_mount_from_make(make: str | None) -> str | None:
    """Coarse default mount derived from QuickTime / EXIF make tag.

    Returns ``None`` for unknown vendors so the StageVideo stays
    explicitly "not classified" rather than silently mis-classified.
    Callers can override per-video via the API.
    """
    if not make:
        return None
    needle = make.strip().lower()
    for token, mount in _MAKE_TO_MOUNT_HEURISTIC:
        if token in needle:
            return mount
    return None


def _stage_rounds_from_info(stage: Any) -> StageRounds | None:
    """Extract ``StageRounds`` from a scoreboard ``StageInfo`` (or any object
    that exposes ``min_rounds`` / ``paper_targets`` / ``steel_targets``).

    Returns ``None`` when none of the fields are populated -- avoids
    sticking an empty ``StageRounds()`` block on every stage just to
    represent "not in the payload".
    """
    expected = getattr(stage, "min_rounds", None)
    paper = getattr(stage, "paper_targets", None)
    steel = getattr(stage, "steel_targets", None)
    if expected is None and paper is None and steel is None:
        return None
    return StageRounds(expected=expected, paper_targets=paper, steel_targets=steel)


def _stage_rounds_by_number(raw_stages: list[Any]) -> dict[int, StageRounds]:
    """Map ``stage_number -> StageRounds`` from a raw scoreboard top-level
    ``stages`` array of dicts.

    Used by the legacy ``import_scoreboard`` path which receives stage-
    card data alongside the per-competitor scores. Silently skips entries
    missing ``stage_number`` or with no rounds metadata.
    """
    out: dict[int, StageRounds] = {}
    for s in raw_stages:
        if not isinstance(s, dict):
            continue
        n = s.get("stage_number")
        if not isinstance(n, int):
            continue
        expected = s.get("min_rounds")
        paper = s.get("paper_targets")
        steel = s.get("steel_targets")
        if expected is None and paper is None and steel is None:
            continue
        out[n] = StageRounds(expected=expected, paper_targets=paper, steel_targets=steel)
    return out


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mts",
    ".m2ts",
    ".mkv",
    ".avi",
    ".mxf",
    ".lrv",
    ".360",
    ".webm",
}

PROJECT_FILE = "project.json"
SUBDIRS = ("raw", "audio", "trimmed", "audit", "exports", "scoreboard", "probes", "thumbs")

# Bumped when the on-disk schema changes in a backwards-incompatible way.
#
# Version history:
#   1 -- initial.
#   2 -- audio/trim caches keyed per-video (``stage<N>_cam_<video_id>.*``)
#        for every role. v1 projects had role-based legacy names
#        (``stage<N>_primary.wav`` / ``_audit.wav`` / ``_trimmed.mp4``)
#        which could alias to a previous primary's audio after a reassignment.
#        Migration deletes the legacy files so the next access re-extracts
#        under the new naming.
#   3 -- ``raw_videos[]`` added to MatchProject (doc 05). Migration
#        backfills one RawVideo per unique StageVideo.path across stages
#        + unassigned, aggregating covers_stages. Legacy entries get
#        ``sha256=None`` and ``size_bytes`` from disk when accessible (0
#        when the source is offline). The field is forward-compatible --
#        a v2-on-disk project loads cleanly into the v3 model with an
#        empty ``raw_videos``; the migration just populates it.
SCHEMA_VERSION = 3


def _migrate_v1_to_v2(root: Path, project: MatchProject) -> None:
    """Delete v1 legacy role-named caches so v2's per-video keys take over.

    v1 cached the primary's audio/trim under role-based names
    (``stage<N>_primary.wav``, ``stage<N>_audit.wav``,
    ``stage<N>_trimmed.mp4``) plus the matching ``.peaks-*.json`` and
    ``.params.json`` sidecars. After a primary swap or stage move those
    files could hold a previous primary's data with no way to tell from
    mtime alone, so we delete them outright and let the next access
    re-extract under ``stage<N>_cam_<video_id>.*``.

    Idempotent: missing files are ignored. Logs a one-line summary on
    completion so the upgrade is visible in the server log.
    """
    audio_dir = project.audio_path(root)
    trimmed_dir = project.trimmed_path(root)

    removed = 0
    patterns: list[tuple[Path, str]] = [
        (audio_dir, "stage*_primary.wav"),
        (audio_dir, "stage*_primary.peaks-*.json"),
        (audio_dir, "stage*_audit.wav"),
        (audio_dir, "stage*_audit.peaks-*.json"),
        (trimmed_dir, "stage*_trimmed.mp4"),
        (trimmed_dir, "stage*_trimmed.params.json"),
        (trimmed_dir, "stage*_trimmed.partial.mp4"),
    ]
    for directory, pattern in patterns:
        if not directory.exists():
            continue
        for victim in directory.glob(pattern):
            # The new naming uses ``stage<N>_cam_<id>_trimmed.mp4`` /
            # ``..._audit.wav``. The legacy globs above happen to also
            # match those (``stage*_trimmed.mp4`` matches per-cam too),
            # so guard against deleting the new artifacts.
            if "_cam_" in victim.name:
                continue
            try:
                victim.unlink()
                removed += 1
            except OSError:
                continue

    logger.info(
        "schema migration v1->v2 on %s: removed %d legacy audio/trim cache file(s)",
        project.name,
        removed,
    )


def _migrate_v2_to_v3(root: Path, project: MatchProject) -> None:
    """Backfill ``raw_videos[]`` from existing StageVideo entries.

    Groups every StageVideo (assigned + unassigned) by ``str(path)`` so a
    single source recording that's been added to multiple stages collapses
    to one RawVideo entry with ``covers_stages`` aggregated. ``size_bytes``
    is read from disk when the source is reachable; ``0`` otherwise so the
    migration is lossless even when the user's external drive is unplugged.
    ``sha256`` stays ``None`` (we never computed one); a future hosted-mode
    attach can fill it in via :meth:`MatchProject.attach_raw_video`.

    Idempotent: re-running against an already-populated ``raw_videos`` is a
    merge (via ``attach_raw_video``) rather than a duplicate-append, so the
    migration is safe to invoke on partial-v3 projects.
    """
    # storage_path -> {filename, covers_stages set, earliest_added_at}
    aggregated: dict[str, dict[str, Any]] = {}

    def _ingest(video: StageVideo, stage_number: int | None) -> None:
        key = str(video.path)
        entry = aggregated.setdefault(
            key,
            {
                "filename": Path(video.path).name,
                "covers_stages": set(),
                "earliest_added_at": video.added_at,
            },
        )
        if stage_number is not None:
            entry["covers_stages"].add(stage_number)
        if video.added_at < entry["earliest_added_at"]:
            entry["earliest_added_at"] = video.added_at

    for stage in project.stages:
        for sv in stage.videos:
            _ingest(sv, stage.stage_number)
    for sv in project.unassigned_videos:
        _ingest(sv, None)

    backfilled = 0
    for storage_path, info in aggregated.items():
        size_bytes = 0
        try:
            resolved = project.resolve_video_path(root, Path(storage_path))
            if resolved.exists():
                size_bytes = resolved.stat().st_size
        except OSError:
            # Source offline -- record 0 and let a future re-scan fill it.
            size_bytes = 0
        rv = RawVideo(
            original_filename=info["filename"],
            size_bytes=size_bytes,
            sha256=None,
            uploaded_at=info["earliest_added_at"],
            storage_path=storage_path,
            covers_stages=sorted(info["covers_stages"]),
        )
        project.attach_raw_video(rv)
        backfilled += 1

    logger.info(
        "schema migration v2->v3 on %s: backfilled %d raw_videos entry/entries",
        project.name,
        backfilled,
    )


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
    # via the ingest screen; ``"aligned"`` = secondary inferred via
    # cross-correlation against the primary's audio when in-stream beep
    # detection failed. Manual overrides survive subsequent auto-detect
    # attempts unless the user explicitly forces re-detection (issue #22).
    beep_source: Literal["auto", "manual", "aligned"] | None = None
    # Has the user explicitly listened to the detected beep and confirmed
    # it's correct (issue #71)? Auto-detected beeps default to ``False`` --
    # the SPA renders a "review" pill on the ingest screen until the user
    # flips this with ``POST /api/stages/{n}/videos/{vid}/beep/review``,
    # or via a manual ``beep_time`` entry (which implies they looked).
    # Always resets to False whenever ``beep_time`` changes (re-detect,
    # candidate switch, primary swap) since each new value is a fresh
    # claim that needs its own confirmation.
    beep_reviewed: bool = False
    # Diagnostic outputs from ``detect_beep`` -- not used by the pipeline but
    # surfaced in the UI to help the user judge auto-detection confidence.
    beep_peak_amplitude: float | None = None
    beep_duration_ms: float | None = None
    # Calibrated detector confidence in [0, 1] for the chosen beep (issue
    # #219 / #220 layer 3). Empirically validated on the labelled fixture
    # set: >=0.7 right ~95 % of the time, 0.5-0.7 lands in the HITL queue,
    # < 0.5 needs human attention. ``None`` for legacy projects from
    # before this field existed and for ``beep_source == "aligned"`` where
    # the detector confidence isn't a meaningful number for the secondary.
    # Manual entry (``beep_source == "manual"``) clamps to 1.0 -- the user
    # told us where the beep is.
    beep_confidence: float | None = None
    # Auto-detection ran and produced no candidate (e.g. iPhone secondary cam
    # where the buzzer wasn't audible / sustained, or recording started after
    # the beep). Distinct from "never detected" (``beep_source is None`` and
    # this False) so the SPA can surface "align manually" instead of an error
    # toast for secondaries. Cleared whenever ``beep_time`` is set or wiped.
    beep_auto_detect_failed: bool = False
    # Diagnostic confidence from ``cross_align.align_secondary_to_primary``.
    # Peak-to-runner-up ratio of the cross-correlation against the primary's
    # landmark audio. Populated whenever cross-align ran on a secondary --
    # both when in-stream detection failed AND we promoted the alignment
    # (``beep_source == "aligned"``), and as a sanity check when in-stream
    # succeeded (``beep_source == "auto"``) to catch the in-stream detector
    # locking onto a steel-strike-as-beep. >= 1.10 is the accept floor on
    # in-stream-failed pairs (calibrated empirically; see server.py).
    beep_alignment_confidence: float | None = None
    # Disagreement between in-stream detection and cross-correlation
    # alignment, in milliseconds. Populated when both ran successfully on
    # a secondary (``beep_source == "auto"`` AND cross-align cleared the
    # confidence floor). The SPA uses this to flag suspected steel-strike
    # mis-detections: if in-stream snapped to a non-beep transient, its
    # answer disagrees with the cross-correlation by hundreds of ms or
    # more. Null when only one of the two methods produced a result.
    beep_alignment_delta_ms: float | None = None
    # Ranked alternative candidates from the most recent auto-detection run
    # (silence-preference score, descending). The production UI offers these
    # as one-click alternatives to the auto-winner so the user rarely has to
    # type a timestamp by hand. Cleared on manual override / clear (issue #22).
    beep_candidates: list[BeepCandidate] = Field(default_factory=list)
    notes: str = ""
    # Camera mount classification (issue #143). Drives per-camera-class
    # threshold selection in the 4-voter ensemble. Stored as the bare
    # ``CameraMount`` string ("head", "hand", ...) to keep the project
    # JSON loose -- avoids a hard import dependency on ``fixture_schema``
    # and lets old projects upgrade without a migration. ``None`` means
    # "not yet determined" -- the detector probes the source on first
    # shot-detect and caches the result here. The user can override via
    # the project SPA / API at any time.
    camera_mount: str | None = None
    # Issue #304: ffprobed camera make/model surfaced at register time and
    # persisted on the project so the detector can dispatch a per-model
    # within-stage amplitude floor. ``None`` when ffprobe couldn't read the
    # tag (Vanguard glasses are the present example -- no QuickTime make/
    # model). The user can override via the videos PATCH endpoint, same as
    # ``camera_mount``.
    camera_make: str | None = None
    camera_model: str | None = None

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


class StageStatus(StrEnum):
    """Per-stage lifecycle state, derived from project + audit-file state.

    Single source of truth -- the sidebar, the Home overview cards, the
    Shooters page, and the per-shooter chip strip all consume the same
    enum value computed by :func:`stage_audit_status`. Previously three
    independent classifiers (one Python, two TypeScript) drifted and
    labeled "stage has time + has primary video" as "audited"; this
    enum names the real stages so the labels stop lying.

    Order is roughly the workflow order:

    - ``todo``        -- no primary video assigned yet; nothing to do here
    - ``partial``     -- primary video assigned but no stage time (no
                         scoreboard import yet)
    - ``ready``       -- all prerequisites met (video + time + beep + trim
                         cache) but detection hasn't run
    - ``in_progress`` -- detection has run; operator hasn't hit Save yet
                         (the audit JSON has a ``shot_detect_run`` event
                         but no ``save`` event)
    - ``audited``     -- operator hit Save & next at least once (the audit
                         JSON's ``audit_events`` contains a ``save`` event)
    - ``skipped``     -- explicitly skipped; treated as terminal but
                         visually distinct from ``audited``
    """

    todo = "todo"
    partial = "partial"
    ready = "ready"
    in_progress = "in_progress"
    audited = "audited"
    skipped = "skipped"


def stage_audit_status(
    stage: StageEntry,
    audit_dir: Path,
    *,
    has_trim: bool | None = None,
) -> StageStatus:
    """Compute the lifecycle status of a single stage.

    ``audit_dir`` is :meth:`MatchProject.audit_path` for the owning
    project. ``has_trim`` can short-circuit a trim-cache existence check
    when the caller already knows it (e.g. it just ran trim). When
    omitted we don't require a trim cache for ``ready`` -- ``ready``
    only means "all logical prerequisites met"; the absence of a trim
    cache will surface as the Audit page's PrereqGate, not as a
    different status here. Keeping this stat-light keeps the overview
    cheap to render.
    """
    if stage.skipped:
        return StageStatus.skipped
    primary = stage.primary()
    if primary is None:
        return StageStatus.todo
    if stage.time_seconds <= 0:
        return StageStatus.partial
    audit_file = audit_dir / f"stage{stage.stage_number}.json"
    if not audit_file.exists():
        # Has primary + has time, but no audit JSON means detection
        # hasn't run yet. Beep absence falls under "ready" too -- the
        # PrereqGate handles that distinction in-page.
        return StageStatus.ready
    try:
        payload = json.loads(audit_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt audit JSON -- treat as ready so the operator can re-run
        # detection. The Audit page will surface the read error itself.
        return StageStatus.ready
    events = payload.get("audit_events") or []
    saved = any(isinstance(e, dict) and e.get("kind") == "save" for e in events)
    if saved:
        return StageStatus.audited
    return StageStatus.in_progress


class StageEntry(BaseModel):
    """A stage in the match: scoreboard data + assigned videos + audit status."""

    stage_number: int
    stage_name: str
    time_seconds: float
    scorecard_updated_at: datetime | None = None
    videos: list[StageVideo] = Field(default_factory=list)
    skipped: bool = False
    # User-entered stage duration when no scoreboard data is available.
    # Preserved across scoreboard imports so a manual value isn't clobbered
    # by a later sync. The trim/shot-detect gates only look at
    # ``time_seconds > 0`` -- this flag exists for auditability and to
    # tell ``import_scoreboard`` / ``apply_stage_time_results`` to skip.
    time_seconds_manual: bool = False
    # Placeholder stages are created without a scoreboard ("I shot 6 stages,
    # let me start ingesting"). They carry stage_number + a generic name so the
    # rest of the pipeline works; importing a real scoreboard later overlays
    # the proper metadata while preserving any video assignments. See
    # MatchProject.init_placeholder_stages and import_scoreboard.
    placeholder: bool = False
    # Per-stage round count + target breakdown from SSI Scoreboard
    # (``min_rounds`` / ``paper_targets`` / ``steel_targets``). Drives the
    # ensemble's adaptive Voter C top-(K+slack) and apriori boost when
    # populated -- without it the detector falls back to global thresholds
    # and runs much hotter on phone-cam audio. ``None`` for placeholders
    # and for projects imported before this field existed; the next
    # scoreboard import will populate it.
    stage_rounds: StageRounds | None = None

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


class SecondaryExportStatus(BaseModel):
    """Per-secondary-cam export status surfaced on the Analysis & Export
    overview (issue #54).

    Each entry maps a secondary :class:`StageVideo` on the stage to (a) its
    eligibility to ride the multi-cam FCPXML (needs a beep + a reachable
    source) and (b) the lossless trim that the last Generate produced for
    it. The SPA renders one checkbox row per entry so the user can include
    or exclude individual cams from the next export.
    """

    video_id: str
    path: Path
    label: str
    has_beep: bool
    beep_reviewed: bool
    source_reachable: bool
    # ``stage<N>_<slug>_cam_<video_id>_trimmed.mp4`` under ``exports/``,
    # populated only when the file is actually present on disk; ``None``
    # before the user runs Generate (or when the cam was excluded last
    # time). The SPA uses this to drive the per-cam Reveal button.
    trim_path: Path | None
    trim_present: bool


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
    # Multi-cam roster (issue #54). One entry per secondary :class:`StageVideo`
    # on the stage (``role == "secondary"``), regardless of beep / source
    # state -- the SPA renders disabled rows for cams that can't ship and
    # explains why. Empty when the stage is single-cam.
    secondaries: list[SecondaryExportStatus] = Field(default_factory=list)


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


class RawVideo(BaseModel):
    """One uploaded raw video file attached to a match (doc 05).

    A raw video is the original camera recording the user uploaded once;
    a single 30-minute head-cam clip typically ``covers_stages = [1, 2, 3, 4]``.
    ``StageVideo`` entries are the per-stage references that point at this
    raw via ``storage_path`` -- the relationship is N:1 (many StageVideos
    can resolve to the same RawVideo when one source covers multiple
    stages).

    ``storage_path`` is the canonical key. In hosted mode it is a
    storage-relative path under the user's tenant prefix (e.g.
    ``raw/GH010023.mp4``); in local mode it is the project-relative or
    absolute path on disk -- either way it round-trips through the active
    ``Storage`` backend.

    ``sha256`` is optional on legacy backfilled entries (we never computed
    one) and populated on hosted uploads via ``X-Content-SHA256``. Future
    content-addressable dedup keys off this field; for now ``storage_path``
    is the primary identity.
    """

    original_filename: str
    size_bytes: int = 0
    sha256: str | None = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    storage_path: str
    covers_stages: list[int] = Field(default_factory=list)


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
    # Pinned shooter id from the SSI shooter index. Globally stable across
    # matches; persisted on the project so re-opening doesn't ask again.
    # Note: this is project state, not user state -- a project shared with
    # a friend will pin to *your* id. We picked project-scoped because the
    # next layer up (per-stage times) is per-(match, competitor), and the
    # competitor id only makes sense in the match's context. See #64.
    selected_shooter_id: int | None = None
    # Pinned per-match competitor id (``competitors[].id`` from
    # ``MatchData``). This is the id ``get_stage_times`` actually wants;
    # the shooter id is the global anchor the user picks. Both are stored
    # because we want to display the shooter id in the UI and use the
    # competitor id for HTTP calls.
    selected_competitor_id: int | None = None
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
    # Layered automation overrides (issue #215). Each field is
    # optional; ``None`` means "inherit from the global default."
    # Resolved at call time via :func:`splitsmith.automation.resolve_automation`
    # with the CLI (if any) and the global settings; the resolved
    # value drives per-event behaviour like "auto-fire shot detect
    # after the user marks the beep reviewed."
    automation: AutomationOverride = Field(default_factory=AutomationOverride)
    # Per-project dismissals for the SPA's audit-pending nudge (#218
    # phase 4). Each entry is a stage_number whose "Stage X has N
    # detected shots awaiting your review" reminder the user has
    # explicitly closed. The dismissal sticks across reloads but is
    # cleared automatically when the stage transitions to a different
    # state (audit completed, candidates re-detected) -- the SPA
    # decides re-emission, the project just remembers what was
    # dismissed.
    nudges_dismissed_stages: list[int] = Field(default_factory=list)
    # Uploaded raw videos for this match (doc 05). One entry per source
    # recording -- a single head-cam clip that covers stages 1-4 is one
    # entry with ``covers_stages = [1, 2, 3, 4]``. StageVideo entries
    # reference these by ``storage_path``. Empty on legacy and local-mode
    # projects until first upload/attach; backfilled from existing
    # StageVideos on v2->v3 schema migration.
    raw_videos: list[RawVideo] = Field(default_factory=list)

    # Worker-side ``Storage`` handle, set by ``state.shooter_project``
    # after load in hosted mode. Not persisted: it's request-scope state,
    # not project-on-disk state, and a ``MatchProject`` round-tripped
    # through ``model_dump`` / ``model_validate`` must stay identical.
    # Local mode leaves this ``None`` and :meth:`resolve_video_path`
    # falls back to the legacy path-only resolution -- desktop behavior
    # is unchanged.
    _storage: Storage | None = PrivateAttr(default=None)
    # Per-project scope under the user's storage prefix. Used to key
    # derived artifact caches (audio WAVs today; trim outputs in the
    # future) so two shooters in different matches can't collide on
    # the same ``video_id``. Typical value:
    # ``matches/<match_id>/shooters/<slug>``. ``None`` when storage
    # is unbound (local mode) or when there's no match scope yet.
    _storage_scope: str | None = PrivateAttr(default=None)

    def bind_storage(self, storage: Storage | None, *, scope: str | None = None) -> None:
        """Attach a per-request ``Storage`` so resolvers can mirror
        hosted-mode artifacts into the project's local cache on first
        access.

        ``scope`` -- per-project prefix for derived-artifact caches
        (audio, trims) so two shooters in different matches can't
        collide on the same ``video_id``. ``None`` disables derived
        caching even when ``storage`` is bound; the raw-video
        resolver still works because it keys off the
        user-prefix-relative ``StageVideo.path`` directly.

        Idempotent; ``None``/``None`` clears the binding.
        """
        self._storage = storage
        self._storage_scope = scope

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shooter_token(self) -> str | None:
        """Public, PII-free token for the pinned shooter.

        Mirrors :func:`splitsmith.lab.core.shooter_token` so the SPA can
        build fixture slugs that include the same suffix the promote
        endpoint stamps on the JSON. ``None`` when no SSI shooter is
        pinned -- the SPA hides promote affordances in that case.
        """
        if self.selected_shooter_id is None:
            return None
        from ..lab.core import shooter_token as _token

        return _token(self.selected_shooter_id)

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
        """Load the project from ``root``. Raises ``FileNotFoundError`` if missing.

        Runs forward-only schema migrations when the on-disk
        ``schema_version`` lags :data:`SCHEMA_VERSION`. The project file is
        rewritten with the new version after a successful migration so the
        upgrade is a one-shot cost on first open.
        """
        path = root / PROJECT_FILE
        if not path.exists():
            raise FileNotFoundError(f"no project.json in {root}")
        data = json.loads(path.read_text(encoding="utf-8"))
        on_disk_version = int(data.get("schema_version", 1))
        project = cls.model_validate(data)
        if on_disk_version < 2:
            _migrate_v1_to_v2(root, project)
        if on_disk_version < 3:
            _migrate_v2_to_v3(root, project)
        if on_disk_version < SCHEMA_VERSION:
            project.schema_version = SCHEMA_VERSION
            project.save(root)
        return project

    def save(self, root: Path) -> None:
        """Atomically persist the project to ``root/project.json``.

        In hosted mode (storage + a match scope bound via
        :meth:`bind_storage`) the saved ``project.json`` is also pushed to
        S3. It is the worker<->API channel for a shooter's state
        (``beep_time``, ``processed`` flags): the worker writes it during a
        job, the API reads it back. S3 is authoritative, so every save
        pushes and the load path (``state.shooter_project``) pulls fresh.
        Local desktop leaves ``_storage`` ``None`` and this is a no-op.
        """
        self.updated_at = datetime.now(UTC)
        atomic_write_json(root / PROJECT_FILE, self.model_dump(mode="json"))
        if self._storage is not None and self._storage_scope is not None:
            self._storage.write_bytes(
                f"{self._storage_scope}/{PROJECT_FILE}",
                (root / PROJECT_FILE).read_bytes(),
            )

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

    def stage_statuses(self, root: Path) -> dict[int, StageStatus]:
        """Compute :class:`StageStatus` for every stage on this project.

        One-shot dict so the GET project endpoint can enrich every
        stage's serialized dict with its status without making the
        client recompute. Reads one audit JSON per stage (cheap; ~12
        stages typical) -- callers that only need ``audited`` counts
        can use :meth:`audited_count` instead, which is the same work.
        """
        audit_dir = self.audit_path(root)
        return {s.stage_number: stage_audit_status(s, audit_dir) for s in self.stages}

    def audited_count(self, root: Path) -> int:
        """Number of stages that are ``audited`` (Save has been hit).

        Backend-side single source of truth for "how many stages did
        this shooter complete?" -- replaces the old "time_seconds > 0
        or skipped" heuristic that overcounted every set-up stage.
        Skipped stages do not count as audited; they're a terminal
        state but represent operator intent to ignore, not work done.
        """
        audit_dir = self.audit_path(root)
        return sum(1 for s in self.stages if stage_audit_status(s, audit_dir) == StageStatus.audited)

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
            audit_trim_p: Path | None = (
                trimmed_dir / f"stage{stage.stage_number}_cam_{primary.video_id}_trimmed.mp4"
                if primary is not None
                else None
            )
            audit_trim_exists = audit_trim_p is not None and audit_trim_p.exists()
            trimmed_p = (
                lossless_trim_p
                if lossless_trim_p.exists()
                else (audit_trim_p if audit_trim_exists else lossless_trim_p)
            )

            csv_exists = csv_p.exists()
            fcpxml_exists = fcpxml_p.exists()
            report_exists = report_p.exists()
            trim_exists = lossless_trim_p.exists()
            overlay_exists = overlay_p.exists()
            # Per-cam lossless trims (issue #54) live next to the primary's
            # trim under the same base; surface them to ``has_exports`` /
            # ``last_export_at`` so a stage that's only had its secondaries
            # generated still reads as exported.
            sec_trim_paths = [
                exports_dir / f"{base}_cam_{sv.video_id}_trimmed.mp4"
                for sv in stage.videos
                if sv.role == "secondary"
            ]
            sec_trim_existing = [p for p in sec_trim_paths if p.exists()]
            has_exports = (
                csv_exists
                or fcpxml_exists
                or report_exists
                or trim_exists
                or overlay_exists
                or bool(sec_trim_existing)
            )
            last_export_at: datetime | None = None
            if has_exports:
                mtimes = [
                    p.stat().st_mtime
                    for p in (csv_p, fcpxml_p, report_p, lossless_trim_p, overlay_p)
                    if p.exists()
                ]
                mtimes.extend(p.stat().st_mtime for p in sec_trim_existing)
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

            secondaries_status: list[SecondaryExportStatus] = []
            for sv in stage.videos:
                if sv.role != "secondary":
                    continue
                sec_trim = exports_dir / f"{base}_cam_{sv.video_id}_trimmed.mp4"
                sec_trim_exists = sec_trim.exists()
                try:
                    sec_src = self.resolve_video_path(root, sv.path)
                    sec_reachable = sec_src.exists()
                except OSError:
                    sec_reachable = False
                secondaries_status.append(
                    SecondaryExportStatus(
                        video_id=sv.video_id,
                        path=sv.path,
                        label=sv.path.name,
                        has_beep=sv.beep_time is not None,
                        beep_reviewed=sv.beep_reviewed,
                        source_reachable=sec_reachable,
                        trim_path=sec_trim if sec_trim_exists else None,
                        trim_present=sec_trim_exists,
                    )
                )

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
                    secondaries=secondaries_status,
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
                "project already has scoreboard-backed stages; " "clear them or import via overwrite first"
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
        # Top-level "stages" array carries stage-card metadata
        # (min_rounds / paper_targets / steel_targets) keyed by
        # ``stage_number``; per-competitor entries don't always have it.
        # Index it once so the loop below can pick up rounds without
        # re-scanning per stage.
        rounds_by_number = _stage_rounds_by_number(raw.get("stages") or [])
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
                    stage_rounds=rounds_by_number.get(stage_data.stage_number),
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
                stage_rounds=_stage_rounds_from_info(stage),
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

    def merge_stage_rounds(self, match_data: Any) -> int:
        """Backfill ``stage_rounds`` from a parsed ``MatchData`` without
        disturbing any other stage state.

        Used to upgrade existing projects to the post-issue-#143 schema
        without forcing a full ``populate_from_match_data(overwrite=True)``
        (which would orphan video assignments). Only fills stages whose
        ``stage_rounds`` is ``None`` -- never overwrites a value already
        set, so a user-edited override survives a re-fetch.

        Returns the count of stages updated.
        """
        from splitsmith.ui.scoreboard.models import MatchData

        if not isinstance(match_data, MatchData):
            match_data = MatchData.model_validate(match_data)
        stages_by_number = {s.stage_number: s for s in self.stages}
        updated = 0
        for info in match_data.stages:
            stage = stages_by_number.get(info.stage_number)
            if stage is None or stage.stage_rounds is not None:
                continue
            rounds = _stage_rounds_from_info(info)
            if rounds is None:
                continue
            stage.stage_rounds = rounds
            updated += 1
        return updated

    def merge_stage_times(self, results: Any) -> int:
        """Overlay per-stage timing onto existing stages keyed by ``stage_number``.

        Used after a successful ``get_stage_times`` call (offline-richer
        drop or live API once ssi-scoreboard#400 ships). Updates each
        ``StageEntry`` in place: ``time_seconds``, ``scorecard_updated_at``,
        and flips ``placeholder=False`` *only* when both fields populate
        (a partial scorecard shouldn't claim the stage is fully real).

        Returns the count of stages updated. Unknown ``stage_number``
        values in the result are silently dropped -- the project's stage
        list was decided by ``populate_from_match_data`` and shouldn't
        grow as a side effect of a stage-times overlay.
        """
        from splitsmith.ui.scoreboard.models import CompetitorStageResults

        if not isinstance(results, CompetitorStageResults):
            results = CompetitorStageResults.model_validate(results)

        stages_by_number = {s.stage_number: s for s in self.stages}
        updated = 0
        for r in results.stages:
            stage = stages_by_number.get(r.stage_number)
            if stage is None:
                continue
            time_set = False
            if r.time_seconds is not None and not stage.time_seconds_manual:
                stage.time_seconds = float(r.time_seconds)
                time_set = True
            scorecard_set = False
            if r.scorecard_updated_at:
                try:
                    stage.scorecard_updated_at = datetime.fromisoformat(r.scorecard_updated_at)
                    scorecard_set = True
                except ValueError:
                    pass
            if time_set and scorecard_set:
                stage.placeholder = False
                updated += 1
        return updated

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

        # Heuristic camera mount stamp (issue #143). Probe is best-effort:
        # if ffprobe fails or the make is unknown, ``camera_mount`` stays
        # ``None`` and the detector falls back to the default class. The
        # user can correct via the videos PATCH endpoint.
        from ..fixture_schema import probe_camera_metadata

        probed_make: str | None = None
        probed_model: str | None = None
        try:
            probe = probe_camera_metadata(source)
            mount_default = _heuristic_mount_from_make(probe.make)
            probed_make = probe.make
            probed_model = probe.model
        except Exception:
            mount_default = None

        video = StageVideo(
            path=stored,
            match_timestamp=match_ts,
            camera_mount=mount_default,
            camera_make=probed_make,
            camera_model=probed_model,
        )
        self.unassigned_videos.append(video)
        return video

    def resolve_video_path(self, root: Path, video_path: Path) -> Path:
        """Resolve a ``StageVideo.path`` to an absolute filesystem path,
        mirroring from hosted storage on first access when needed.

        Behaviour matrix:

        - **Absolute path** -- returned as-is (legacy local-mode flow:
          external drive, FCP scratch).
        - **Relative path, no storage bound** -- returned as
          ``root / video_path``. Identical to pre-PR-4 behavior; the
          desktop UI lands here.
        - **Relative path, storage bound, local mirror exists** -- the
          cached mirror at ``root / video_path`` wins. Subsequent
          detection jobs on the same project re-use the same local
          copy without a second download.
        - **Relative path, storage bound, no local mirror** -- streams
          the object from storage into ``root / video_path`` via a
          temp + atomic-rename, then returns the local path. A
          missing key is left to surface as a downstream
          ``FileNotFoundError`` -- callers already handle that for
          offline-source cases.

        ``storage`` is set by :meth:`bind_storage`, which the hosted
        boot calls inside ``state.shooter_project`` so every project
        load that goes through that accessor is automatically wired up.
        """
        if video_path.is_absolute():
            return video_path
        local = root / video_path
        if self._storage is not None and not local.exists():
            self._mirror_from_storage(self._storage, str(video_path), local)
        return local

    @staticmethod
    def _mirror_from_storage(storage: Storage, key: str, dest: Path) -> None:
        """Stream ``key`` from ``storage`` into ``dest`` via temp+rename.

        Best-effort: a missing key is a no-op (the caller's existing
        ``not source.exists()`` checks already handle that). Network
        / IO errors propagate so the worker fails the job loudly --
        a half-downloaded mirror would be worse than a failed detect.
        """
        if not storage.exists(key):
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=dest.name + ".",
            suffix=".tmp",
            dir=dest.parent,
        )
        try:
            with storage.open_stream(key) as src, os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(src, out)
            Path(tmp_name).replace(dest)
        except Exception:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass
            raise

    def find_raw_video(self, storage_path: str) -> RawVideo | None:
        """Return the ``RawVideo`` with this ``storage_path``, or ``None``.

        ``storage_path`` is the canonical identity key -- see RawVideo's
        docstring for the local-vs-hosted semantics.
        """
        for rv in self.raw_videos:
            if rv.storage_path == storage_path:
                return rv
        return None

    def attach_raw_video(self, rv: RawVideo) -> RawVideo:
        """Register a raw video on this project, merging into an existing
        entry when one shares the same ``storage_path``.

        Merge rules when an entry already exists:

        - ``covers_stages`` -- union of existing + new (stable insertion
          order; sorted ascending so the SPA renders deterministically).
        - ``size_bytes`` -- adopt the new value when the existing one is 0
          (legacy backfill placeholder).
        - ``sha256`` -- adopt the new value when the existing one is
          ``None`` (legacy backfill).
        - ``original_filename`` / ``uploaded_at`` -- existing wins (we don't
          rewrite the historical record once it's set).

        Returns the canonical entry on the project (either the merged
        existing one or the newly appended ``rv``).
        """
        existing = self.find_raw_video(rv.storage_path)
        if existing is None:
            self.raw_videos.append(rv)
            return rv
        merged = sorted(set(existing.covers_stages) | set(rv.covers_stages))
        existing.covers_stages = merged
        if existing.size_bytes == 0 and rv.size_bytes:
            existing.size_bytes = rv.size_bytes
        if existing.sha256 is None and rv.sha256:
            existing.sha256 = rv.sha256
        return existing

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
        - ``to_stage_number=N, role="secondary"`` on a stage with no primary
          yet: auto-upgrade to ``"primary"``. Matches user expectation that
          the first video assigned to a stage becomes the primary -- and is
          the only way to bootstrap a primary in placeholder-mode projects
          where ``auto_match`` has no scoreboard timestamps to anchor on.
          Pass ``role="ignored"`` explicitly to opt out of the upgrade.

        Returns the moved ``StageVideo``. Raises ``KeyError`` if the video or
        stage doesn't exist.
        """
        located = self.find_video(path)
        if located is None:
            raise KeyError(f"video {path} not registered with project")
        current_stage, video = located

        # Detach from current location.
        if current_stage is None:
            self.unassigned_videos = [v for v in self.unassigned_videos if str(v.path) != str(video.path)]
        else:
            current_stage.videos = [v for v in current_stage.videos if str(v.path) != str(video.path)]

        # Reattach.
        if to_stage_number is None:
            video.role = "secondary"  # role is meaningless when unassigned
            self.unassigned_videos.append(video)
            return video

        target = self.stage(to_stage_number)
        # Auto-upgrade: a "secondary" assignment to a stage with no primary
        # yet becomes the primary. Skipped for "ignored" (explicit opt-out)
        # and a no-op when the caller already passed "primary".
        effective_role: VideoRole = role
        if role == "secondary" and not any(v.role == "primary" for v in target.videos):
            effective_role = "primary"
        if effective_role == "primary":
            for v in target.videos:
                if v.role == "primary":
                    v.role = "secondary"
        video.role = effective_role
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
        new_primary.beep_reviewed = False
        new_primary.beep_auto_detect_failed = False
        new_primary.beep_alignment_confidence = None
        new_primary.beep_alignment_delta_ms = None

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
            self.unassigned_videos = [v for v in self.unassigned_videos if str(v.path) != str(video.path)]
        else:
            current_stage.videos = [v for v in current_stage.videos if str(v.path) != str(video.path)]

        raw_link = self.resolve_video_path(root, video.path)

        # Cache files are keyed per-video for every role (stage<N>_cam_<id>.*)
        # so swapping a primary cannot alias to a previous primary's cache.
        audio_cache: Path | None = None
        trimmed_cache: Path | None = None
        if stage_number is not None:
            vid = video.video_id
            audio_cache = self.audio_path(root) / f"stage{stage_number}_cam_{vid}.wav"
            trimmed_cache = self.trimmed_path(root) / f"stage{stage_number}_cam_{vid}_trimmed.mp4"

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
                    v.beep_reviewed = False
                    v.beep_auto_detect_failed = False
                    v.beep_alignment_confidence = None
                    v.beep_alignment_delta_ms = None

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
