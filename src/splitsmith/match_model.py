"""Match-as-object data model (issue #320).

A *match* is the persistence unit going forward. One physical IPSC match =
one folder = one ``Match``. N shooters live as ``Shooter`` subdirectories
under ``<match>/shooters/<slug>/``, each with their own raw/audio/audit/...
trees. The match folder owns the shared stage definitions, scoreboard
linkage, and match-date metadata.

On-disk layout::

    <match-root>/
      match.json                        # Match: shared metadata + stage defs + shooter slugs
      scoreboard/                       # shared scoreboard cache (if any)
      shooters/
        <slug>/
          shooter.json                  # Shooter: name + scoreboard ids + per-stage data
          raw/  audio/  trimmed/        # the heavy data, same as legacy projects
          audit/  exports/  thumbs/
          probes/  scoreboard/

Legacy single-shooter projects (those with ``project.json`` at the root and
no ``shooters/`` subdirectory) keep working unmodified. ``Match.load`` can
adapt a legacy project to a one-shooter ``Match`` *in memory* via the
:meth:`Match.from_legacy_project` shim -- no disk migration is forced.
Physical consolidation happens via ``splitsmith match merge``.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from .async_bridge import run_sync
from .config import StageRounds
from .ui.project import (
    PROJECT_FILE,
    SUBDIRS,
    MatchProject,
    StageVideo,
    _stage_rounds_from_info,
    atomic_write_json,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MATCH_FILE = "match.json"
SHOOTER_FILE = "shooter.json"
SHOOTERS_DIR = "shooters"
MATCH_SUBDIRS = ("scoreboard",)
SHOOTER_SUBDIRS = SUBDIRS  # raw / audio / trimmed / audit / exports / scoreboard / probes / thumbs

#: Schema version for ``match.json`` and ``shooter.json``. Continues from
#: the legacy MatchProject schema (which tops out at 2) so the version space
#: is shared and "is this a redesign-era match or a legacy project?" can be
#: answered from a single integer.
#:
#: v3 -> v4 adds ``match_id`` (issue #353 Phase 3). Pre-v4 matches get a
#: deterministic id assigned on first load (derived from name + created_at
#: so a re-open from the same disk always lands on the same id).
MATCH_SCHEMA_VERSION = 4


def _slugify(name: str) -> str:
    """Kebab-case ``name`` for use inside a URL-safe identifier.

    Strips diacritics down to ASCII via ``unicodedata.normalize``, lowers,
    keeps ``[a-z0-9]`` runs, joins with ``-``. Bounded to 32 chars so the
    full ``<slug>-<hash>`` id stays under typical URL-segment limits.
    Empty result (e.g. all-symbol name) falls back to ``"match"``.
    """
    import re
    import unicodedata

    normalised = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    parts = re.findall(r"[a-z0-9]+", normalised.lower())
    slug = "-".join(parts)[:32].strip("-")
    return slug or "match"


def generate_match_id(name: str, created_at: datetime) -> str:
    """Deterministic, URL-safe match id from immutable identity fields.

    ``<slug-of-name>-<10-char hash>``. The hash mixes name + ``created_at``
    so two matches with the same name (different timestamps) get distinct
    ids; once assigned the id is frozen in ``match.json`` and a later rename
    does not invalidate links.
    """
    import hashlib

    digest = hashlib.sha1(f"{name}\x00{created_at.isoformat()}".encode()).hexdigest()[:10]
    return f"{_slugify(name)}-{digest}"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class MatchStageDefinition(BaseModel):
    """Stage as a property of the *match*: identity + rounds prior.

    Per-shooter data (their time, their videos, their audit) lives on the
    ``Shooter`` side. The fields here are the ones every shooter shares
    because they describe the same physical stage.
    """

    stage_number: int
    stage_name: str
    stage_rounds: StageRounds | None = None
    #: True when the stage was created before any scoreboard sync (manual
    #: ingest of "I shot N stages, let me start now"). Mirrors the legacy
    #: ``StageEntry.placeholder`` flag.
    placeholder: bool = False


class ShooterStageData(BaseModel):
    """A single stage's per-shooter data.

    Mirrors the legacy ``StageEntry`` minus the fields that have moved to
    ``MatchStageDefinition``. ``time_seconds`` is per-shooter (each shooter
    runs the stage at their own pace) so it belongs here, not on the match
    stage definition.
    """

    stage_number: int  # FK into Match.stages
    time_seconds: float = 0.0
    time_seconds_manual: bool = False
    scorecard_updated_at: datetime | None = None
    skipped: bool = False
    videos: list[StageVideo] = Field(default_factory=list)


class Shooter(BaseModel):
    """A shooter's data within a match (lives at ``<match>/shooters/<slug>/shooter.json``).

    Slug is the directory name and the stable in-match identifier. The
    full display name + scoreboard linkage live in the model so the SPA can
    render rosters without round-tripping to the match.
    """

    schema_version: int = MATCH_SCHEMA_VERSION
    slug: str
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    selected_shooter_id: int | None = None
    selected_competitor_id: int | None = None
    stages: list[ShooterStageData] = Field(default_factory=list)
    unassigned_videos: list[StageVideo] = Field(default_factory=list)
    last_scanned_dir: str | None = None
    # Per-shooter dir overrides. Resolved against the shooter root, not the
    # match root, so a shooter can keep heavy intermediates off the project
    # drive without affecting the rest of the match.
    raw_dir: str | None = None
    audio_dir: str | None = None
    trimmed_dir: str | None = None
    exports_dir: str | None = None
    probes_dir: str | None = None
    thumbs_dir: str | None = None
    trim_pre_buffer_seconds: float = 5.0
    trim_post_buffer_seconds: float = 5.0
    trim_audit_encoder: str = "auto"
    nudges_dismissed_stages: list[int] = Field(default_factory=list)

    def stage(self, stage_number: int) -> ShooterStageData | None:
        """Return this shooter's data for a stage, or ``None`` if absent."""
        for s in self.stages:
            if s.stage_number == stage_number:
                return s
        return None

    @classmethod
    def load(cls, shooter_root: Path) -> Shooter:
        """Load shooter.json from ``shooter_root``. Raises FileNotFoundError if missing."""
        path = shooter_root / SHOOTER_FILE
        if not path.exists():
            raise FileNotFoundError(f"no {SHOOTER_FILE} in {shooter_root}")
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, shooter_root: Path) -> None:
        """Atomically persist this shooter to ``<shooter_root>/shooter.json``."""
        self.updated_at = datetime.now(UTC)
        atomic_write_json(shooter_root / SHOOTER_FILE, self.model_dump(mode="json"))


class Match(BaseModel):
    """Top-level on-disk match (lives at ``<match-root>/match.json``).

    Holds the match identity, scoreboard linkage, shared stage definitions,
    and the list of shooter slugs. Shooter heavy data is loaded lazily via
    :meth:`load_shooter` so opening a 4-shooter match doesn't read 4x
    everything up front.
    """

    schema_version: int = MATCH_SCHEMA_VERSION
    name: str
    #: Stable, URL-safe match identifier (issue #353 Phase 3). Persisted in
    #: ``match.json`` so SPA routes can carry the id without leaking the
    #: filesystem path. Optional on the model to allow the v3 -> v4 load-time
    #: migration; :meth:`Match.load` assigns + saves it when missing so
    #: in-memory ``Match`` instances always have one set.
    match_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    scoreboard_match_id: str | None = None
    scoreboard_content_type: int | None = None
    match_date: date | None = None
    stages: list[MatchStageDefinition] = Field(default_factory=list)
    #: Ordered list of shooter slugs (= subdir names under ``shooters/``).
    shooters: list[str] = Field(default_factory=list)

    # Hosted-mode state-doc binding (state refactor). When set, ``save()``
    # persists the match doc to the ``state_docs`` table via the bound
    # ``ProjectStateStore`` under optimistic locking instead of writing
    # ``match.json``. ``state.match()`` binds these after loading the doc
    # from Postgres; creation sites bind with ``version=0``. Not persisted.
    _state_store: Any = PrivateAttr(default=None)
    _state_match_id: str | None = PrivateAttr(default=None)
    _state_version: int = PrivateAttr(default=0)

    def bind_state(self, store: Any, *, match_id: str, version: int) -> None:
        """Bind a ``ProjectStateStore`` so ``save()`` round-trips the match
        doc through Postgres (hosted mode). ``version`` is the
        optimistic-lock version the doc was loaded at (0 to INSERT)."""
        self._state_store = store
        self._state_match_id = match_id
        self._state_version = version

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def init(cls, root: Path, *, name: str) -> Match:
        """Create a fresh empty match at ``root``.

        Idempotent: if ``match.json`` already exists, load and return it.
        Subdirectories (``scoreboard/``, ``shooters/``) are created if
        missing.
        """
        root.mkdir(parents=True, exist_ok=True)
        for sub in MATCH_SUBDIRS:
            (root / sub).mkdir(exist_ok=True)
        (root / SHOOTERS_DIR).mkdir(exist_ok=True)
        existing = root / MATCH_FILE
        if existing.exists():
            return cls.load(root)
        match = cls(name=name)
        match.match_id = generate_match_id(match.name, match.created_at)
        match.save(root)
        return match

    def stages_from_match_data(self, match_data: Any) -> None:
        """Populate match-level stage definitions from a scoreboard shell.

        The match-side mirror of
        :meth:`MatchProject.populate_from_match_data`. A scoreboard-created
        match must carry its own stage list: a shooter ADDED LATER gets a
        project mirrored from ``match.stages`` (see the add-shooter path),
        so an empty list would hand them zero stages -- and per-shooter
        coverage counts that divide by ``len(match.stages)`` would read 0.
        Stages are flagged ``placeholder`` exactly as the per-shooter
        import flags them; per-competitor timing arrives separately.
        """
        self.stages = [
            MatchStageDefinition(
                stage_number=s.stage_number,
                stage_name=s.name,
                stage_rounds=_stage_rounds_from_info(s),
                placeholder=True,
            )
            for s in sorted(match_data.stages, key=lambda s: s.stage_number)
        ]

    @classmethod
    def load(cls, root: Path) -> Match:
        """Load ``match.json`` from ``root``.

        Raises ``FileNotFoundError`` if ``root`` is neither a match folder
        nor a legacy project folder. Use :meth:`is_match_folder` or
        :meth:`from_path` to inspect a path without raising.

        Side effect: pre-v4 matches (no ``match_id`` on disk) get one
        assigned deterministically and the file is re-saved. The id is
        derived from immutable identity fields so subsequent loads land on
        the same value; once persisted the id is frozen.
        """
        path = root / MATCH_FILE
        if not path.exists():
            raise FileNotFoundError(f"no {MATCH_FILE} in {root}")
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        match = cls.model_validate(data)
        if not match.match_id:
            match.match_id = generate_match_id(match.name, match.created_at)
            match.save(root)
        return match

    def save(self, root: Path) -> None:
        """Persist the match.

        Hosted mode (a state store bound via :meth:`bind_state`): write the
        match doc to the ``state_docs`` table under optimistic locking; no
        file is touched. A stale version raises ``StateConflictError`` (->
        409). Local desktop: atomic write to ``<root>/match.json``.
        """
        self.updated_at = datetime.now(UTC)
        if self._state_store is not None:
            self._state_version = run_sync(
                self._state_store.save_match(
                    self._state_match_id,
                    self.model_dump(mode="json"),
                    expected_version=self._state_version,
                )
            )
            return
        atomic_write_json(root / MATCH_FILE, self.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Path resolvers
    # ------------------------------------------------------------------

    @staticmethod
    def shooter_root(match_root: Path, slug: str) -> Path:
        return match_root / SHOOTERS_DIR / slug

    # ------------------------------------------------------------------
    # Shooter access
    # ------------------------------------------------------------------

    def load_shooter(self, match_root: Path, slug: str) -> Shooter:
        """Load a shooter's ``shooter.json`` by slug.

        Raises ``KeyError`` if the slug isn't registered on this match,
        ``FileNotFoundError`` if the directory exists but lacks
        ``shooter.json``.
        """
        if slug not in self.shooters:
            raise KeyError(f"no shooter {slug!r} in match {self.name!r}")
        return Shooter.load(self.shooter_root(match_root, slug))

    def add_shooter(
        self,
        match_root: Path,
        shooter: Shooter,
    ) -> None:
        """Register a shooter on this match and persist their shooter.json.

        Creates the shooter's subdirectory tree if missing. The shooter's
        slug must be unique within the match; raises ``ValueError`` if it
        collides with an existing slug.
        """
        if shooter.slug in self.shooters:
            raise ValueError(f"shooter slug {shooter.slug!r} already registered on match {self.name!r}")
        shooter_root = self.shooter_root(match_root, shooter.slug)
        shooter_root.mkdir(parents=True, exist_ok=True)
        for sub in SHOOTER_SUBDIRS:
            (shooter_root / sub).mkdir(exist_ok=True)
        shooter.save(shooter_root)
        self.shooters.append(shooter.slug)
        self.save(match_root)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def stage(self, stage_number: int) -> MatchStageDefinition:
        """Return the stage definition for ``stage_number``. Raises KeyError."""
        for s in self.stages:
            if s.stage_number == stage_number:
                return s
        raise KeyError(f"no stage {stage_number} in match {self.name!r}")


# ---------------------------------------------------------------------------
# Path inspection + legacy adaptation
# ---------------------------------------------------------------------------


def is_match_folder(path: Path) -> bool:
    """True if ``path`` has a ``match.json`` (redesign-era match)."""
    return (path / MATCH_FILE).is_file()


def is_legacy_project_folder(path: Path) -> bool:
    """True if ``path`` has a ``project.json`` (legacy single-shooter project)."""
    return (path / PROJECT_FILE).is_file()


def from_path(path: Path) -> tuple[str, Path]:
    """Classify a path as ``"match"`` or ``"legacy"`` and return both.

    Returns ``("match", path)`` for a redesign-era match folder,
    ``("legacy", path)`` for a legacy single-shooter project folder.
    Raises ``FileNotFoundError`` if ``path`` is neither.
    """
    if is_match_folder(path):
        return "match", path
    if is_legacy_project_folder(path):
        return "legacy", path
    raise FileNotFoundError(f"{path} has neither {MATCH_FILE} nor {PROJECT_FILE}; not a splitsmith project")


def legacy_to_match_view(project: MatchProject) -> tuple[Match, Shooter]:
    """Adapt a legacy ``MatchProject`` into ``(Match, Shooter)`` in memory.

    No disk writes. The legacy project is rendered as a one-shooter match
    so call sites that expect the new model can consume it without
    forcing the user to migrate. The shooter slug defaults to a kebab-cased
    competitor name (or ``"unknown"`` when ``competitor_name`` is empty).

    Used by:
      - ``splitsmith compare export`` to read merged matches and legacy
        projects through one code path
      - the FastAPI endpoint refactor (#321) when it lands, so the SPA can
        speak the new URL space even against legacy projects on disk
    """
    # legacy_to_match_view: synthesise a Match view for a legacy single-
    # shooter project. The slug is opaque (no PII leak) but deterministic
    # for a given project root so callers that build URLs against the
    # same project across reloads see a stable id.
    slug = _legacy_view_slug(project)
    match = Match(
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
        scoreboard_match_id=project.scoreboard_match_id,
        scoreboard_content_type=project.scoreboard_content_type,
        match_date=project.match_date,
        stages=[
            MatchStageDefinition(
                stage_number=s.stage_number,
                stage_name=s.stage_name,
                stage_rounds=s.stage_rounds,
                placeholder=s.placeholder,
            )
            for s in project.stages
        ],
        shooters=[slug],
    )
    shooter = Shooter(
        slug=slug,
        name=project.competitor_name or "Unknown shooter",
        created_at=project.created_at,
        updated_at=project.updated_at,
        selected_shooter_id=project.selected_shooter_id,
        selected_competitor_id=project.selected_competitor_id,
        stages=[
            ShooterStageData(
                stage_number=s.stage_number,
                time_seconds=s.time_seconds,
                time_seconds_manual=s.time_seconds_manual,
                scorecard_updated_at=s.scorecard_updated_at,
                skipped=s.skipped,
                videos=list(s.videos),
            )
            for s in project.stages
        ],
        unassigned_videos=list(project.unassigned_videos),
        last_scanned_dir=project.last_scanned_dir,
        raw_dir=project.raw_dir,
        audio_dir=project.audio_dir,
        trimmed_dir=project.trimmed_dir,
        exports_dir=project.exports_dir,
        probes_dir=project.probes_dir,
        thumbs_dir=project.thumbs_dir,
        trim_pre_buffer_seconds=project.trim_pre_buffer_seconds,
        trim_post_buffer_seconds=project.trim_post_buffer_seconds,
        trim_audit_encoder=project.trim_audit_encoder,
        nudges_dismissed_stages=list(project.nudges_dismissed_stages),
    )
    return match, shooter


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _legacy_view_slug(project: MatchProject) -> str:
    """Deterministic opaque slug for the synthetic view over a legacy
    single-shooter project. The legacy disk layout has no
    ``shooters/<slug>/`` dir so the slug is purely a routing token; we
    hash the project name + scoreboard ids so the same project gets the
    same slug across reloads without leaking the competitor name.
    """
    import hashlib

    seed = "|".join(
        str(x or "")
        for x in (
            project.name,
            project.scoreboard_content_type,
            project.scoreboard_match_id,
            project.selected_shooter_id,
        )
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"s_{digest}"


def mint_shooter_slug(taken: set[str] | None = None) -> str:
    """Mint a fresh opaque shooter slug.

    Shape: ``s_<8 lowercase hex>``. Random, never derived from the
    shooter's name, so URLs / on-disk paths / server logs don't leak
    competitor PII. ``taken`` is consulted to avoid the 1-in-4-billion
    chance of collision within a match (and to keep tests deterministic
    when they mock the RNG).
    """
    import secrets

    while True:
        candidate = f"s_{secrets.token_hex(4)}"
        if not taken or candidate not in taken:
            return candidate


def slugify_filename(name: str) -> str:
    """Kebab-case a string for filesystem-safe filenames.

    Used for stage / match filenames where readability matters and the
    name is not PII. ``slugify`` (the old shooter-slug helper) used to
    do this with the same impl; it now mints opaque shooter slugs, so
    callers that want the old kebab-case behavior must use this helper
    explicitly.
    """
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_only.lower()
    slugged = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    return slugged or "name"


# ---------------------------------------------------------------------------
# Merge planning
# ---------------------------------------------------------------------------


class MergeConflictError(Exception):
    """Raised when input projects can't be merged into a single match safely."""


class MergePlan(BaseModel):
    """Description of a planned merge.

    Returned by :func:`plan_merge` so callers (the CLI ``--dry-run`` flag,
    the future GUI wizard, tests) can inspect what *would* happen without
    executing. Each shooter entry records the source project path, the
    proposed slug, and the destination subdirectory.
    """

    output_root: Path
    name: str
    scoreboard_match_id: str | None
    scoreboard_content_type: int | None
    match_date: date | None
    stages: list[MatchStageDefinition]
    shooter_moves: list[ShooterMove]


class ShooterMove(BaseModel):
    """One source project -> shooter slot in the merged match."""

    source_root: Path
    slug: str
    destination_root: Path
    competitor_name: str


def plan_merge(
    inputs: list[Path],
    output_root: Path,
    *,
    name: str | None = None,
) -> MergePlan:
    """Validate inputs and produce a ``MergePlan``.

    All inputs must be legacy single-shooter project folders (``project.json``
    present). They must share ``scoreboard_match_id`` when set; if unset on
    every input, ``name`` must be passed explicitly so the merged match has
    a well-defined identity.

    Stage definitions across inputs must be consistent: same stages with
    same names + same ``stage_rounds``. Diverging values raise
    :class:`MergeConflictError` -- the user should reconcile by editing
    the source projects before retrying.

    No filesystem changes are performed.
    """
    if not inputs:
        raise ValueError("plan_merge requires at least one input project")

    projects: list[tuple[Path, MatchProject]] = []
    for src in inputs:
        if not is_legacy_project_folder(src):
            raise MergeConflictError(f"{src} is not a legacy single-shooter project (no {PROJECT_FILE})")
        projects.append((src, MatchProject.load(src)))

    # Validate identity.
    scoreboard_ids = {p.scoreboard_match_id for _, p in projects if p.scoreboard_match_id}
    if len(scoreboard_ids) > 1:
        raise MergeConflictError(f"inputs disagree on scoreboard_match_id: {sorted(scoreboard_ids)}")
    sb_id = next(iter(scoreboard_ids), None)

    content_types = {p.scoreboard_content_type for _, p in projects if p.scoreboard_content_type is not None}
    if len(content_types) > 1:
        raise MergeConflictError(f"inputs disagree on scoreboard_content_type: {sorted(content_types)}")
    sb_ct = next(iter(content_types), None)

    names = {p.name for _, p in projects}
    if name is None:
        if len(names) > 1:
            raise MergeConflictError(f"inputs have different names {sorted(names)}; pass --name explicitly")
        name = next(iter(names))

    dates = {p.match_date for _, p in projects if p.match_date}
    if len(dates) > 1:
        raise MergeConflictError(f"inputs disagree on match_date: {sorted(str(d) for d in dates)}")
    match_date = next(iter(dates), None)

    # Merge stage definitions.
    stages_by_number: dict[int, MatchStageDefinition] = {}
    for src, proj in projects:
        for s in proj.stages:
            existing = stages_by_number.get(s.stage_number)
            candidate = MatchStageDefinition(
                stage_number=s.stage_number,
                stage_name=s.stage_name,
                stage_rounds=s.stage_rounds,
                placeholder=s.placeholder,
            )
            if existing is None:
                stages_by_number[s.stage_number] = candidate
                continue
            # An already-merged stage exists; reconcile.
            if existing.stage_name != candidate.stage_name:
                # Tolerate placeholder names ("Stage 1") losing to real ones.
                if existing.placeholder and not candidate.placeholder:
                    stages_by_number[s.stage_number] = candidate
                    continue
                if candidate.placeholder and not existing.placeholder:
                    continue
                raise MergeConflictError(
                    f"stage {s.stage_number}: name disagreement "
                    f"{existing.stage_name!r} vs {candidate.stage_name!r} (in {src})"
                )
            if (
                existing.stage_rounds is not None
                and candidate.stage_rounds is not None
                and existing.stage_rounds != candidate.stage_rounds
            ):
                raise MergeConflictError(f"stage {s.stage_number}: stage_rounds disagreement (in {src})")
            # Prefer the one that has stage_rounds populated.
            if existing.stage_rounds is None and candidate.stage_rounds is not None:
                stages_by_number[s.stage_number] = candidate

    stage_defs = [stages_by_number[k] for k in sorted(stages_by_number)]

    # Assign opaque slugs so on-disk paths / URLs / logs don't leak names.
    taken: set[str] = set()
    moves: list[ShooterMove] = []
    for src, proj in projects:
        slug = mint_shooter_slug(taken)
        taken.add(slug)
        moves.append(
            ShooterMove(
                source_root=src,
                slug=slug,
                destination_root=Match.shooter_root(output_root, slug),
                competitor_name=proj.competitor_name or "Unknown shooter",
            )
        )

    return MergePlan(
        output_root=output_root,
        name=name,
        scoreboard_match_id=sb_id,
        scoreboard_content_type=sb_ct,
        match_date=match_date,
        stages=stage_defs,
        shooter_moves=moves,
    )


def execute_merge(
    plan: MergePlan,
    *,
    move: bool = False,
) -> Match:
    """Execute a :class:`MergePlan` on disk.

    Creates ``<output_root>/match.json`` + ``shooters/<slug>/`` for each
    shooter. Copies (default) or moves (``move=True``) the per-shooter
    heavy data from each source project into the destination subdir, and
    writes the resulting ``shooter.json`` with redundant match-level fields
    stripped.

    Returns the freshly-created :class:`Match`. Idempotent guard: if
    ``output_root`` already contains a ``match.json``, raises
    ``FileExistsError`` -- the caller should pick a fresh path or remove
    the existing match folder manually.
    """
    import shutil

    if (plan.output_root / MATCH_FILE).exists():
        raise FileExistsError(f"{plan.output_root} already contains {MATCH_FILE}; refusing to overwrite")

    plan.output_root.mkdir(parents=True, exist_ok=True)
    for sub in MATCH_SUBDIRS:
        (plan.output_root / sub).mkdir(exist_ok=True)
    (plan.output_root / SHOOTERS_DIR).mkdir(exist_ok=True)

    # Materialize each shooter.
    for mv in plan.shooter_moves:
        src = mv.source_root
        dst = mv.destination_root
        if dst.exists():
            raise FileExistsError(f"{dst} already exists; refusing to overwrite")
        dst.parent.mkdir(parents=True, exist_ok=True)

        op = shutil.move if move else _copytree
        op(src, dst)

        # Write shooter.json (new match-aware form) AND keep project.json
        # (legacy compat shim) in sync. The bulk of the server still speaks
        # the legacy MatchProject schema -- if we deleted project.json the
        # ingest/audit/beep endpoints would all fall over with FileNotFound
        # the first time the SPA opened a shooter inside a merged match.
        # Match-level fields are stripped from the legacy file so match.json
        # stays authoritative.
        legacy_project = MatchProject.load(dst)
        _match_view, shooter_view = legacy_to_match_view(legacy_project)
        shooter_view.slug = mv.slug
        shooter_view.save(dst)
        legacy_project.scoreboard_match_id = None
        legacy_project.scoreboard_content_type = None
        legacy_project.match_date = None
        legacy_project.save(dst)

        # Note: any pre-existing scoreboard cache stays under the shooter's
        # scoreboard/ subdir. The match-level scoreboard/ at the top is
        # reserved for future shared caches; today it stays empty.
        logger.info("merge: %s -> %s (%s)", src, dst, "moved" if move else "copied")

    # Write the match.json last, after every shooter dir is in place, so a
    # crashed merge leaves either no match.json (nothing committed) or a
    # valid match.json with every shooter ready (full commit). Either state
    # is safe to retry against.
    match = Match(
        name=plan.name,
        scoreboard_match_id=plan.scoreboard_match_id,
        scoreboard_content_type=plan.scoreboard_content_type,
        match_date=plan.match_date,
        stages=plan.stages,
        shooters=[mv.slug for mv in plan.shooter_moves],
    )
    match.save(plan.output_root)
    return match


def _copytree(src: Path, dst: Path) -> None:
    """Copy a directory tree preserving symlinks (raw/ holds them).

    Wraps :func:`shutil.copytree` with ``symlinks=True``. We keep the raw
    symlinks intact rather than dereferencing them, otherwise a merge would
    duplicate every gigabyte of source video into the destination.
    """
    import shutil

    shutil.copytree(src, dst, symlinks=True)


# ---------------------------------------------------------------------------
# Convenience: anything-shooter loader for code that wants a uniform API.
# ---------------------------------------------------------------------------


def load_match_or_legacy(
    path: Path,
) -> tuple[Match, dict[str, Path]]:
    """Load a path as a Match + mapping of shooter slug -> shooter_root.

    Works for both layouts:
      - Redesign-era match folder: reads ``match.json`` and the
        ``shooters/<slug>/`` directory listing.
      - Legacy single-shooter project: returns a one-shooter Match view
        via :func:`legacy_to_match_view`; the shooter "root" is the
        project path itself (where ``project.json`` and the heavy dirs
        live), with the legacy ``project.json`` still authoritative for
        the per-shooter data.

    Callers that need the full ``Shooter`` model should use
    :meth:`Match.load_shooter` (redesign-era) or
    :func:`legacy_to_match_view` (legacy) directly.
    """
    kind, root = from_path(path)
    if kind == "match":
        match = Match.load(root)
        roots: dict[str, Path] = {slug: Match.shooter_root(root, slug) for slug in match.shooters}
        return match, roots
    # legacy
    project = MatchProject.load(root)
    match, shooter = legacy_to_match_view(project)
    return match, {shooter.slug: root}


__all__ = [
    "MATCH_FILE",
    "MATCH_SCHEMA_VERSION",
    "SHOOTERS_DIR",
    "SHOOTER_FILE",
    "Match",
    "MatchStageDefinition",
    "MergeConflictError",
    "MergePlan",
    "Shooter",
    "ShooterMove",
    "ShooterStageData",
    "execute_merge",
    "from_path",
    "is_legacy_project_folder",
    "is_match_folder",
    "legacy_to_match_view",
    "load_match_or_legacy",
    "mint_shooter_slug",
    "plan_merge",
    "slugify_filename",
]


# Silence Pyright for module-level `Any` import we don't use; keep for forward refs.
_ = Any
