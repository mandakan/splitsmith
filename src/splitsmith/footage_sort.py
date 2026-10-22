"""Propose a shooter and stage for every clip of a shared footage folder.

Spec: ``docs/superpowers/specs/2026-10-01-footage-sort-design.md``.

Club mates hand over a folder each: phone clips of the other squad members,
their own clip filmed by someone else, head-cam clips. Neither the device nor
the folder says who is in a clip. What does: each competitor's scorecard is
typed shortly after their own run, so the first squad scorecard after a clip
starts names the run (shooter and stage). Scorecard times are a prior, not
truth (SSI bugs, scores typed after the fact), so every proposal carries its
confidence and evidence and the user confirms.

Clocks are per camera, not per shooter: one camera can film several
shooters. A camera's clock is ``trusted`` when its clips line up with the
scorecards as recorded about as well as under any correction; otherwise an
offset is ``fitted`` from the pattern of its clips against the whole squad's
scorecards, and when no single offset stands out the camera
``needs_anchor``: the user names one clip's run and that pins the offset
for the rest.

Pure: takes probed clip metadata and scorecards, returns a
:class:`SortProposal`. No file I/O, no ffprobe.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from .config import FootageSortConfig
from .footage_sort_names import RankEvidence, recording_rank, shooter_in_name, stage_in_name
from .match_project import _PRIMARY_MOUNT_RANK, _heuristic_mount_from_make

CameraClock = Literal["trusted", "fitted", "anchored", "needs_anchor", "no_timestamps"]
Confidence = Literal["high", "medium", "needs_you", "skipped"]
Issue = Literal[
    "ambiguous", "conflict", "no_candidate", "no_timestamp", "needs_anchor", "outside_match", "needs_order"
]
CameraShooterSource = Literal["user", "clip", "name", "folder"]


class SortClip(BaseModel):
    """One probed video of the scanned folder."""

    clip_id: str
    folder: str = ""
    filename: str
    start: datetime | None = None
    duration: float | None = None
    make: str | None = None
    model: str | None = None
    #: The file's modification time: the last resort for recording order.
    modified: datetime | None = None


class ShooterScorecards(BaseModel):
    """One shooter of the match and when each of their stages was scored."""

    key: str
    name: str
    scorecards: dict[int, datetime] = Field(default_factory=dict)
    #: Stage numbers in the order the shooter shot them (typed on Stage
    #: details, else the scorecard order); empty when unknown.
    order: list[int] = Field(default_factory=list)
    #: Stages the shooter's project already has footage on.
    covered: list[int] = Field(default_factory=list)


class Anchor(BaseModel):
    """The user's word on one clip's run; pins its camera's clock."""

    clip_id: str
    shooter: str
    stage: int


class CameraShooter(BaseModel):
    """The user's word on whose clips a camera holds, no stage committed."""

    camera_key: str
    shooter: str


class Override(BaseModel):
    """The user's decision for one clip: a (shooter, stage), or skip."""

    clip_id: str
    shooter: str | None = None
    stage: int | None = None
    skip: bool = False


class SortCamera(BaseModel):
    key: str
    folder: str
    model: str | None
    scheme: str
    clip_ids: list[str]
    clock: CameraClock
    # Seconds added to the camera's recorded starts.
    offset_seconds: float = 0.0
    #: Whose clips the camera holds, when known, and how that is known.
    shooter: str | None = None
    shooter_source: CameraShooterSource | None = None


class ClipReason(BaseModel):
    """The evidence behind a proposal; the page words it."""

    scorecard_at: datetime | None = None
    # Scorecard time minus the clip's corrected start.
    lead_seconds: float | None = None
    offset_seconds: float = 0.0
    issue: Issue | None = None
    # Ambiguous: the other run whose scorecard came as close.
    rival: str | None = None
    rival_stage: int | None = None
    # Clips in the same run (1 = this camera alone).
    run_size: int = 1
    # Placed by the shooter's shooting order or the file name (spec
    # 2026-10-22): the clip's place in its camera's recording order, the
    # stage's place in the shooter's order, and what ranked the clip.
    order_rank: int | None = None
    order_position: int | None = None
    order_evidence: RankEvidence | None = None
    stage_from_name: bool = False


class ClipProposal(BaseModel):
    clip_id: str
    camera_key: str
    shooter: str | None = None
    stage: int | None = None
    confidence: Confidence
    run_id: str | None = None
    role: Literal["primary", "secondary"] | None = None
    decided_by: Literal["engine", "user"] = "engine"
    reason: ClipReason = Field(default_factory=ClipReason)


class SortProposal(BaseModel):
    cameras: list[SortCamera]
    clips: list[ClipProposal]


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# How far before the first or after the last scorecard a clip may start and
# still be footage of the match.
_MATCH_DAY_SLACK = timedelta(hours=12)


@dataclass(frozen=True)
class _Card:
    at: datetime
    shooter: str
    stage: int


_SCHEMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("IMG", re.compile(r"^IMG_\d+", re.IGNORECASE)),
    ("PXL", re.compile(r"^PXL_\d{8}_\d+", re.IGNORECASE)),
    ("VID_datetime", re.compile(r"^VID_\d{8}_\d{6}", re.IGNORECASE)),
    ("datetime", re.compile(r"^\d{8}_\d{6}")),
    ("meta", re.compile(r"^video-\d+_singular_display", re.IGNORECASE)),
    ("GoPro", re.compile(r"^G[HX]\d{6}", re.IGNORECASE)),
)


def filename_scheme(filename: str) -> str:
    """The camera's naming scheme, or the extension for renamed files."""
    for name, pattern in _SCHEMES:
        if pattern.match(filename):
            return name
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def camera_key(clip: SortClip) -> str:
    """Folder, device and naming scheme: two identical phones in two club
    mates' folders are two cameras."""
    return "|".join([clip.folder, clip.make or "", clip.model or "", filename_scheme(clip.filename)])


def propose(
    clips: Sequence[SortClip],
    shooters: Sequence[ShooterScorecards],
    *,
    anchors: Iterable[Anchor] = (),
    overrides: Iterable[Override] = (),
    camera_shooters: Iterable[CameraShooter] = (),
    root_folder: str = "",
    config: FootageSortConfig | None = None,
) -> SortProposal:
    """Propose a (shooter, stage) for every clip. See the module docstring."""
    cfg = config or FootageSortConfig()
    cards = sorted(
        (_Card(at, s.key, stage) for s in shooters for stage, at in s.scorecards.items()),
        key=lambda c: c.at,
    )
    by_card = {(c.shooter, c.stage): c for c in cards}
    anchor_for = {a.clip_id: a for a in anchors}
    by_camera: dict[str, list[SortClip]] = defaultdict(list)
    for clip in clips:
        by_camera[camera_key(clip)].append(clip)

    # Cameras whose clocks need no help come first: their clips measure the
    # typical lead an anchored camera is placed with.
    clocks: dict[str, tuple[CameraClock, float]] = {}
    fits: dict[str, _ClockFit] = {}
    for key, members in by_camera.items():
        timed = [c for c in members if c.start is not None]
        if not timed:
            clocks[key] = ("no_timestamps", 0.0)
            continue
        if not cards:
            clocks[key] = ("needs_anchor", 0.0)
            continue
        fit = fits[key] = _clock_fit(timed, cards, cfg)
        # Trusted when the clock as recorded lines up about as well as any
        # correction does. Clips of nobody (warm-ups, another squad) lower
        # both counts alike, so they do not cost a good clock its trust.
        if fit.recorded >= 1 and fit.recorded >= cfg.trust_ratio * fit.best:
            clocks[key] = ("trusted", 0.0)
    trusted_leads = [
        lead
        for key, (clock, _) in clocks.items()
        if clock == "trusted"
        for clip in by_camera[key]
        if (lead := _tight_lead(clip, 0.0, cards, cfg)) is not None
    ]
    typical_lead = median(trusted_leads) if trusted_leads else cfg.default_lead_s

    for key, members in by_camera.items():
        timed = [c for c in members if c.start is not None]
        anchored = next((anchor_for[c.clip_id] for c in timed if c.clip_id in anchor_for), None)
        if anchored is not None and (anchored.shooter, anchored.stage) in by_card:
            clip = next(c for c in timed if c.clip_id == anchored.clip_id)
            clocks[key] = (
                "anchored",
                _anchor_offset(
                    clip, by_card[(anchored.shooter, anchored.stage)], timed, cards, typical_lead, cfg
                ),
            )
        elif key not in clocks:
            offset = fits[key].offset
            clocks[key] = ("fitted", offset) if offset is not None else ("needs_anchor", 0.0)

    proposals: list[ClipProposal] = []
    corrected: dict[str, datetime] = {}
    for key, members in by_camera.items():
        clock, offset = clocks[key]
        # One clip lining up is no proof of a clock: such a camera's
        # proposals are pre-checked only by the user.
        proven = key not in fits or fits[key].recorded >= cfg.fit_min_clips
        for clip in members:
            proposal = _assign(clip, key, clock, offset, cards, cfg)
            if proposal.confidence == "high" and not proven:
                proposal.confidence = "medium"
            proposals.append(proposal)
            if clip.start is not None:
                corrected[clip.clip_id] = clip.start + timedelta(seconds=offset)

    # An anchor is also the user's decision for its own clip.
    anchor_overrides = [
        Override(clip_id=a.clip_id, shooter=a.shooter, stage=a.stage) for a in anchor_for.values()
    ]
    _apply_overrides(proposals, [*anchor_overrides, *overrides], by_card)
    owners = _camera_shooters(by_camera, proposals, shooters, camera_shooters, root_folder)
    _order_pass(proposals, by_camera, clocks, owners, shooters)
    _group_runs(proposals, corrected, {c.clip_id: c for c in clips}, cfg)

    cameras = [
        SortCamera(
            key=key,
            folder=members[0].folder,
            model=members[0].model or members[0].make,
            scheme=filename_scheme(members[0].filename),
            clip_ids=[c.clip_id for c in members],
            clock=clocks[key][0],
            offset_seconds=clocks[key][1],
            shooter=owners[key][0] if key in owners else None,
            shooter_source=owners[key][1] if key in owners else None,
        )
        for key, members in by_camera.items()
    ]
    order = {c.clip_id: i for i, c in enumerate(clips)}
    proposals.sort(key=lambda p: order[p.clip_id])
    return SortProposal(cameras=cameras, clips=proposals)


def _candidate(
    start: datetime, window_end: datetime, cards: Sequence[_Card], cfg: FootageSortConfig
) -> tuple[_Card | None, _Card | None]:
    """The first scorecard in ``(start + lead_min, window_end]`` and a rival:
    any other scorecard within ``tie_s`` of it. Scores typed back to back
    (another shooter's, or the same shooter's next stage entered after the
    fact) cannot say which run the clip is."""
    lo = start + timedelta(seconds=cfg.lead_min_s)
    first: _Card | None = None
    for card in cards:
        if card.at <= lo:
            continue
        if card.at > window_end:
            break
        if first is None:
            first = card
            continue
        if (card.at - first.at).total_seconds() <= cfg.tie_s:
            return first, card
        break
    return first, None


def _tight(clip: SortClip, offset: float, cards: Sequence[_Card], cfg: FootageSortConfig) -> _Card | None:
    """The clip's run by the tight window, or ``None`` when absent or tied."""
    assert clip.start is not None
    start = clip.start + timedelta(seconds=offset)
    card, rival = _candidate(start, start + timedelta(seconds=cfg.fit_lead_max_s), cards, cfg)
    return card if rival is None else None


def _tight_lead(
    clip: SortClip, offset: float, cards: Sequence[_Card], cfg: FootageSortConfig
) -> float | None:
    if clip.start is None:
        return None
    card = _tight(clip, offset, cards, cfg)
    if card is None:
        return None
    return (card.at - clip.start).total_seconds() - offset


def _lined_up(
    timed: Sequence[SortClip], offset: float, cards: Sequence[_Card], cfg: FootageSortConfig
) -> int:
    """How many clips get a clean run at ``offset``, each run used once
    (several clips of one run on this camera count, two runs do not)."""
    groups: dict[tuple[str, int], list[datetime]] = defaultdict(list)
    for clip in timed:
        card = _tight(clip, offset, cards, cfg)
        if card is not None:
            assert clip.start is not None
            groups[(card.shooter, card.stage)].append(clip.start)
    return sum(
        len(starts)
        for starts in groups.values()
        if (max(starts) - min(starts)).total_seconds() <= cfg.same_run_seconds
    )


def _grid(seconds: float, step: float) -> float:
    return round(seconds / step) * step


def _plateau_center(scores: dict[float, int], best: float, cfg: FootageSortConfig) -> float:
    """The middle of the run of grid offsets around ``best`` sharing its score."""
    top = scores[best]
    lo = hi = best
    while scores.get(lo - cfg.fit_grid_s) == top:
        lo -= cfg.fit_grid_s
    while scores.get(hi + cfg.fit_grid_s) == top:
        hi += cfg.fit_grid_s
    return _grid((lo + hi) / 2, cfg.fit_grid_s)


def _scan_offsets(
    timed: Sequence[SortClip],
    cards: Sequence[_Card],
    seeds: Iterable[float],
    cfg: FootageSortConfig,
) -> dict[float, int]:
    """Score every grid offset within ``fit_distinct_s`` of each seed."""
    scores: dict[float, int] = {}
    reach = int(cfg.fit_distinct_s // cfg.fit_grid_s)
    for seed in seeds:
        for step in range(-reach, reach + 1):
            offset = _grid(seed + step * cfg.fit_grid_s, cfg.fit_grid_s)
            if offset not in scores:
                scores[offset] = _lined_up(timed, offset, cards, cfg)
    return scores


@dataclass(frozen=True)
class _ClockFit:
    """How a camera's clips line up with the squad's scorecards."""

    # Clips lined up as recorded (offset 0).
    recorded: int
    # The most clips any offset lines up.
    best: int
    # The one offset achieving ``best`` by a clear margin, else ``None``.
    offset: float | None


def _clock_fit(timed: Sequence[SortClip], cards: Sequence[_Card], cfg: FootageSortConfig) -> _ClockFit:
    """Score the recorded clock against every plausible correction."""
    # Seeds: each clip placed so that some scorecard follows it by a
    # plausible lead; plus the recorded clock itself.
    leads = (cfg.lead_min_s + 20, cfg.fit_lead_max_s / 2, cfg.fit_lead_max_s - 20)
    seeds = {0.0} | {
        _grid((card.at - clip.start).total_seconds() - lead, cfg.fit_grid_s)
        for clip in timed
        if clip.start is not None
        for card in cards
        for lead in leads
    }
    scores = _scan_offsets(timed, cards, seeds, cfg)
    best = max(scores, key=lambda o: (scores[o], -abs(o)))
    runner_up = max(
        (score for offset, score in scores.items() if abs(offset - best) > cfg.fit_distinct_s), default=0
    )
    clear = scores[best] >= cfg.fit_min_clips and scores[best] - runner_up >= cfg.fit_margin
    return _ClockFit(
        recorded=scores[0.0],
        best=scores[best],
        offset=_plateau_center(scores, best, cfg) if clear else None,
    )


def _anchor_offset(
    clip: SortClip,
    card: _Card,
    timed: Sequence[SortClip],
    cards: Sequence[_Card],
    typical_lead: float,
    cfg: FootageSortConfig,
) -> float:
    """The offset that puts ``clip`` on ``card``'s run and lines up most of
    the rest of the camera; the typical lead places it when nothing else
    decides."""
    assert clip.start is not None
    placed = _grid((card.at - clip.start).total_seconds() - typical_lead, cfg.fit_grid_s)

    def lands_on_card(offset: float) -> bool:
        # The anchor may itself be a tied clip (two stages scored seconds
        # apart): then landing on either half of the tie is consistent, and
        # the rest of the camera decides the offset.
        assert clip.start is not None
        start = clip.start + timedelta(seconds=offset)
        first, rival = _candidate(start, start + timedelta(seconds=cfg.fit_lead_max_s), cards, cfg)
        return card in (first, rival)

    seeds = [placed, placed - typical_lead, placed + typical_lead]
    scores = {
        offset: score
        for offset, score in _scan_offsets(timed, cards, seeds, cfg).items()
        if lands_on_card(offset)
    }
    if not scores:
        return placed
    best = max(scores, key=lambda o: (scores[o], -abs(o - placed)))
    return _plateau_center(scores, best, cfg)


def _assign(
    clip: SortClip,
    key: str,
    clock: CameraClock,
    offset: float,
    cards: Sequence[_Card],
    cfg: FootageSortConfig,
) -> ClipProposal:
    reason = ClipReason(offset_seconds=offset)
    if clip.start is None:
        reason.issue = "no_timestamp"
        return ClipProposal(clip_id=clip.clip_id, camera_key=key, confidence="needs_you", reason=reason)
    if clock == "needs_anchor":
        reason.issue = "needs_anchor"
        return ClipProposal(clip_id=clip.clip_id, camera_key=key, confidence="needs_you", reason=reason)
    start = clip.start + timedelta(seconds=offset)
    end = start + timedelta(seconds=(clip.duration or 0.0) + cfg.tail_max_s)
    card, rival = _candidate(start, end, cards, cfg)
    if (
        card is None
        and cards
        and not (cards[0].at - _MATCH_DAY_SLACK <= start <= cards[-1].at + _MATCH_DAY_SLACK)
    ):
        # Recorded nowhere near the match: a camera whose clock is off by
        # days, not footage of nobody.
        reason.issue = "outside_match"
        return ClipProposal(clip_id=clip.clip_id, camera_key=key, confidence="needs_you", reason=reason)
    if card is None:
        reason.issue = "no_candidate"
        # On a trusted clock no run means the clip shows none of these
        # shooters (warm-up, another squad). On a corrected clock it may
        # mean the correction is wrong for this clip: the user looks.
        confidence_none: Confidence = "skipped" if clock == "trusted" else "needs_you"
        return ClipProposal(clip_id=clip.clip_id, camera_key=key, confidence=confidence_none, reason=reason)
    reason.scorecard_at = card.at
    reason.lead_seconds = (card.at - start).total_seconds()
    confidence: Confidence = "high" if clock == "trusted" else "medium"
    if rival is not None:
        reason.issue = "ambiguous"
        reason.rival = rival.shooter
        reason.rival_stage = rival.stage
        confidence = "needs_you"
    return ClipProposal(
        clip_id=clip.clip_id,
        camera_key=key,
        shooter=card.shooter,
        stage=card.stage,
        confidence=confidence,
        reason=reason,
    )


def _apply_overrides(
    proposals: list[ClipProposal], overrides: Iterable[Override], by_card: dict[tuple[str, int], _Card]
) -> None:
    index = {p.clip_id: p for p in proposals}
    for override in overrides:
        proposal = index.get(override.clip_id)
        if proposal is None:
            continue
        proposal.decided_by = "user"
        proposal.reason.issue = None
        proposal.reason.rival = proposal.reason.rival_stage = None
        if override.skip or override.shooter is None or override.stage is None:
            proposal.shooter = proposal.stage = None
            proposal.confidence = "skipped"
            continue
        proposal.shooter, proposal.stage = override.shooter, override.stage
        proposal.confidence = "high"
        card = by_card.get((override.shooter, override.stage))
        proposal.reason.scorecard_at = card.at if card else None


def _group_runs(
    proposals: list[ClipProposal],
    corrected: dict[str, datetime],
    clips: dict[str, SortClip],
    cfg: FootageSortConfig,
) -> None:
    """Cluster each (shooter, stage)'s clips into runs; two runs on one
    stage put the engine's clips there in front of the user."""
    groups: dict[tuple[str, int], list[ClipProposal]] = defaultdict(list)
    for p in proposals:
        if p.shooter is not None and p.stage is not None and p.confidence != "skipped":
            groups[(p.shooter, p.stage)].append(p)
    for (shooter, stage), members in groups.items():
        members.sort(key=lambda p: (p.clip_id not in corrected, corrected.get(p.clip_id, _EPOCH), p.clip_id))
        runs: list[list[ClipProposal]] = []
        for p in members:
            at = corrected.get(p.clip_id)
            first = runs[-1][0] if runs else None
            first_at = corrected.get(first.clip_id) if first else None
            if (
                at is not None
                and first_at is not None
                and (at - first_at).total_seconds() <= cfg.same_run_seconds
            ):
                runs[-1].append(p)
            else:
                runs.append([p])
        for i, run in enumerate(runs):
            ranked = sorted(
                run,
                key=lambda p: _PRIMARY_MOUNT_RANK.get(_heuristic_mount_from_make(clips[p.clip_id].make), 1),
            )
            for p in run:
                p.run_id = f"{shooter}:{stage}:{i}"
                p.role = "primary" if p is ranked[0] else "secondary"
                p.reason.run_size = len(run)
                if len(runs) > 1 and p.decided_by == "engine":
                    p.confidence = "needs_you"
                    p.reason.issue = "conflict"


def _camera_shooters(
    by_camera: dict[str, list[SortClip]],
    proposals: list[ClipProposal],
    shooters: Sequence[ShooterScorecards],
    camera_shooters: Iterable[CameraShooter],
    root_folder: str,
) -> dict[str, tuple[str, CameraShooterSource]]:
    """Whose clips each camera holds, where something says so: the user's
    choice, then the user's decided clips (all one shooter), then a shooter
    named in every named file, then the folder's name."""
    known = {s.key for s in shooters}
    people = [(s.key, s.name) for s in shooters]
    chosen = {c.camera_key: c.shooter for c in camera_shooters if c.shooter in known}
    index = {p.clip_id: p for p in proposals}
    out: dict[str, tuple[str, CameraShooterSource]] = {}
    for key, members in by_camera.items():
        if key in chosen:
            out[key] = (chosen[key], "user")
            continue
        decided = {
            index[c.clip_id].shooter
            for c in members
            if index[c.clip_id].decided_by == "user" and index[c.clip_id].shooter is not None
        }
        if len(decided) == 1:
            out[key] = (next(iter(decided)), "clip")
            continue
        named = {shooter_in_name(c.filename, people) for c in members}
        named.discard(None)
        if len(named) == 1:
            out[key] = (next(iter(named)), "name")  # type: ignore[arg-type]
            continue
        folder = members[0].folder or root_folder
        by_folder = shooter_in_name(folder, people)
        if by_folder is not None:
            out[key] = (by_folder, "folder")
    return out


def _order_pass(
    proposals: list[ClipProposal],
    by_camera: dict[str, list[SortClip]],
    clocks: dict[str, tuple[CameraClock, float]],
    owners: dict[str, tuple[str, CameraShooterSource]],
    shooters: Sequence[ShooterScorecards],
) -> None:
    """Place a clockless camera's undecided clips by the file names and
    the shooter's shooting order (spec 2026-10-22). Conservative: clips
    already placed are fixed points, and a stretch between two of them is
    proposed one clip per open stage only when the counts match. Every
    proposal here waits for the user (``needs_you``) or, named in its
    file, is ``medium``; nothing is pre-checked."""
    index = {p.clip_id: p for p in proposals}
    by_key = {s.key: s for s in shooters}
    for key, members in by_camera.items():
        if clocks[key][0] not in ("needs_anchor", "no_timestamps") or key not in owners:
            continue
        shooter = by_key.get(owners[key][0])
        if shooter is None:
            continue
        stages = set(shooter.order) | set(shooter.scorecards)
        undecided = [
            c
            for c in members
            if index[c.clip_id].shooter is None and index[c.clip_id].confidence != "skipped"
        ]
        # A stage named in the file places the clip outright.
        for clip in undecided:
            n = stage_in_name(clip.filename)
            if n is not None and (not stages or n in stages):
                p = index[clip.clip_id]
                p.shooter, p.stage, p.confidence = shooter.key, n, "medium"
                p.reason.issue = None
                p.reason.stage_from_name = True
        if not shooter.order:
            for clip in members:
                p = index[clip.clip_id]
                if p.shooter is None and p.confidence != "skipped":
                    p.reason.issue = "needs_order"
            continue
        ranked = recording_rank(
            [c.filename for c in members],
            [c.start for c in members],
            [c.modified for c in members],
            clock_ok=False,
        )
        if ranked is None:
            continue
        order_idx, evidence = ranked
        _align([members[i] for i in order_idx], index, shooter, evidence, owners[key][0])


def _align(
    ranked: list[SortClip],
    index: dict[str, ClipProposal],
    shooter: ShooterScorecards,
    evidence: RankEvidence,
    owner: str,
) -> None:
    position = {stage: i for i, stage in enumerate(shooter.order)}
    covered = set(shooter.covered)
    # Fixed points: (rank, order position) of clips already placed for the
    # shooter on a stage in the order. A clip placed for someone else, or
    # skipped, is neither fixed nor open.
    fixed: list[tuple[int, int]] = []
    open_clips: list[tuple[int, SortClip]] = []
    for rank, clip in enumerate(ranked):
        p = index[clip.clip_id]
        if p.confidence == "skipped":
            continue
        if p.shooter is None:
            open_clips.append((rank, clip))
        elif p.shooter == owner and p.stage in position:
            fixed.append((rank, position[p.stage]))  # type: ignore[index]
    if any(b[1] <= a[1] for a, b in zip(fixed, fixed[1:], strict=False)):
        return  # placed clips disagree with the order: leave it to the user
    taken = {pos for _, pos in fixed}
    bounds = [(-1, -1), *fixed, (len(ranked), len(shooter.order))]
    for (lo_rank, lo_pos), (hi_rank, hi_pos) in zip(bounds, bounds[1:], strict=False):
        clips = [c for r, c in open_clips if lo_rank < r < hi_rank]
        stages = [
            shooter.order[i]
            for i in range(lo_pos + 1, hi_pos)
            if i not in taken and shooter.order[i] not in covered
        ]
        if not clips or len(clips) != len(stages):
            continue
        for clip, stage in zip(clips, stages, strict=True):
            p = index[clip.clip_id]
            p.shooter, p.stage, p.confidence = shooter.key, stage, "needs_you"
            p.reason.issue = None
            p.reason.order_rank = ranked.index(clip) + 1
            p.reason.order_position = position[stage] + 1
            p.reason.order_evidence = evidence
