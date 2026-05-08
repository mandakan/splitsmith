"""Renderer-agnostic timeline IR (issue #194).

Today ``fcpxml_gen.StageComposition`` is half a composition spec but lives
inside the FCPXML emitter and only describes what FCPXML happens to need.
This module extracts that into a Pydantic-style IR so the same composition
can drive multiple renderers (FCPXML today, FCP7 XML / ffmpeg next) and so
follow-on features (PiP / transitions / titles / templates) bind to a
stable shape instead of branching inside a 900-line emitter.

Layering:

    user inputs / UI request
        |
        v
    Composition (this module)         <-- IR boundary
        |
        v
    render_fcpxml(...)                <-- thin bridge for now;
        |                                 lowers IR to today's
        v                                 ``generate_match_fcpxml`` so
    fcpxml_gen.generate_match_fcpxml      output stays byte-identical.
        |                                 Future PRs migrate emission
        v                                 directly onto the IR.
    .fcpxml on disk

The IR is opinionated about *what* the user wants (primary, secondaries,
overlay, transitions, titles); it does not know about FCPXML-specific
plumbing (resource ids, lane numbers, attribute order). Renderers own
those choices.

Out of scope here, tracked in sibling issues:
- ``Transition`` shape exists but isn't emitted yet (#195).
- ``TitleCard`` / ``Segment`` exist but aren't emitted yet (#173, #196).
- A second renderer (#197 FCP7 XML, #174 ffmpeg) lands on top of this.
"""

from __future__ import annotations

from collections.abc import Sequence as SequenceProto
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import fcpxml_gen
from .config import OutputConfig, Shot, VideoMetadata


@dataclass(frozen=True)
class SequenceFormat:
    """The composition's shared sequence format.

    All stages render against this. The existing match composer requires
    a single shared frame rate across stages; the IR mirrors that
    constraint by carrying one ``SequenceFormat`` for the whole
    composition rather than one per stage.
    """

    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int

    @classmethod
    def from_video(cls, meta: VideoMetadata) -> SequenceFormat:
        return cls(
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
        )


@dataclass(frozen=True)
class Asset:
    """A single source media file with its probed metadata."""

    path: Path
    metadata: VideoMetadata


@dataclass(frozen=True)
class Transform:
    """Renderer-agnostic 2D transform on a connected clip.

    ``scale`` is a uniform multiplier (1.0 = native). ``position`` is in
    pixel-units relative to the sequence centre with +Y up -- the same
    coordinate system FCPXML's ``<adjust-transform position>`` uses, so a
    direct emit is a string format. ffmpeg / FCP7 XML renderers convert
    to their native conventions when they land.
    """

    scale: float = 1.0
    position: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class Marker:
    """A shot-time marker on the primary clip.

    The IR holds the raw ``Shot`` so renderers can format the label using
    their own config (split-colour thresholds etc.) instead of receiving
    a pre-formatted string. ``time_seconds`` is the source-time of the
    marked frame in the primary clip's local coordinates.
    """

    time_seconds: float
    shot: Shot


ConnectedRole = Literal["cam", "overlay"]


@dataclass(frozen=True)
class ConnectedClip:
    """A clip layered above the primary on a higher lane.

    ``role="cam"`` -> a secondary head/handheld cam. ``beep_offset_seconds``
    is the clip-local time of the beep so the renderer can align it to the
    primary's beep frame.

    ``role="overlay"`` -> the alpha overlay (audited shot times etc.). It
    sits on the topmost lane and ignores beep alignment (the renderer
    matches its head to the primary's visible head).
    """

    asset: Asset
    label: str
    role: ConnectedRole
    beep_offset_seconds: float | None = None
    transform: Transform | None = None


@dataclass(frozen=True)
class Stage:
    """One stage's contribution to the composition.

    ``title`` (issue #196) is an optional ``TitleCard`` rendered as a
    slate (pre-stage card on the spine) or lower-third (connected
    text clip overlaid on the primary). Renderers that don't support
    titles ignore the field.
    """

    name: str
    primary: Asset
    beep_offset_seconds: float
    head_pad_seconds: float
    tail_pad_seconds: float
    secondaries: tuple[ConnectedClip, ...] = ()
    overlay: ConnectedClip | None = None
    markers: tuple[Marker, ...] = ()
    title: "TitleCard | None" = None


TransitionKind = Literal["cross-dissolve", "dip-to-color"]


@dataclass(frozen=True)
class Transition:
    """Stage-boundary transition (issue #195).

    ``from_stage_index`` / ``to_stage_index`` are positions in
    ``Composition.stages``; consecutive indices only for v1
    (``to_stage_index == from_stage_index + 1`` -- enforced by the
    FCPXML emitter, ignored by other renderers until they grow
    transition support).

    ``duration_seconds`` is split half-before / half-after the boundary
    per FCPXML semantics. The exporter validates that each adjacent
    stage's effective window contains at least ``duration_seconds / 2``
    of material before emitting the timeline.

    The FCPXML renderer (this PR) emits ``cross-dissolve`` and
    ``dip-to-color`` via FCP's built-in motion templates. The FCP7 XML
    and ffmpeg renderers ignore transitions for now -- they'll grow
    support in follow-up PRs.
    """

    from_stage_index: int
    to_stage_index: int
    kind: TransitionKind = "cross-dissolve"
    duration_seconds: float = 0.5
    color: str | None = None  # dip-to-color only


TitleStyle = Literal["slate", "lower-third"]


@dataclass(frozen=True)
class TitleCard:
    """Title overlay attached to a stage (issue #196).

    ``slate`` -- a short pre-stage card on the spine; extends the
    timeline by ``duration_seconds``. Lands before the stage's
    primary so the user reads the card, then the action begins.

    ``lower-third`` -- a connected text clip on the topmost lane that
    overlaps the start of the stage; doesn't extend the timeline.

    ``font_size`` is in FCPXML points at the timeline's native scale;
    bigger sequences may want larger sizes but Basic Title scales
    automatically across formats.

    The FCPXML renderer (this PR) emits both styles via FCP's Basic
    Title generator. FCP7 XML / ffmpeg renderers ignore titles for
    now -- they'll grow support in follow-up PRs.
    """

    text: str
    duration_seconds: float
    style: TitleStyle = "slate"
    font_size: int = 144
    font: str = "Helvetica"
    color: str = "1 1 1 1"


@dataclass(frozen=True)
class Segment:
    """Intro / outro segment (placeholder until #173 lands)."""

    asset: Asset
    title: TitleCard | None = None


@dataclass(frozen=True)
class Composition:
    """A complete renderer-agnostic timeline (#194)."""

    project_name: str
    sequence: SequenceFormat
    stages: tuple[Stage, ...]
    intro: Segment | None = None
    outro: Segment | None = None
    transitions: tuple[Transition, ...] = ()


# --- conversions -----------------------------------------------------------


def _pip_to_transform(
    pip: fcpxml_gen.PipPlacement,
    seq: SequenceFormat,
) -> Transform:
    """Resolve a ``PipPlacement`` (corner / scale / margin_pct) into a
    renderer-agnostic ``Transform`` by asking ``PipPlacement.resolve`` for
    pixel-space coordinates."""
    scale, (x, y) = pip.resolve(
        sequence_width=seq.width,
        sequence_height=seq.height,
    )
    return Transform(scale=scale, position=(x, y))


def from_stage_compositions(
    stages: SequenceProto[fcpxml_gen.StageComposition],
    *,
    project_name: str,
    transitions: SequenceProto[Transition] = (),
    titles: dict[int, TitleCard] | None = None,
) -> Composition:
    """Build a :class:`Composition` from today's ``StageComposition`` inputs.

    The shared sequence format is taken from ``stages[0]`` -- the existing
    composer requires same fps across stages, and we additionally require
    same width/height for a single shared sequence (FCP scales mismatched
    cams within a stage via per-cam formats; mismatched primaries across
    stages would need cross-fps timeline support which is out of scope).

    ``transitions`` (issue #195): optional stage-boundary transitions
    that the FCPXML renderer emits between consecutive stages. Each
    transition's ``from_stage_index`` / ``to_stage_index`` must point at
    valid stage positions; v1 only accepts consecutive pairs.

    ``titles`` (issue #196): optional mapping from stage index to a
    :class:`TitleCard`. The IR attaches each title to its
    :class:`Stage`; the FCPXML renderer emits slate / lower-third
    via FCP's Basic Title generator.
    """
    titles_map = dict(titles) if titles else {}
    if not stages:
        raise ValueError("Composition requires at least one stage")
    base = stages[0].video
    seq = SequenceFormat.from_video(base)

    ir_stages: list[Stage] = []
    for stage_idx, stage_comp in enumerate(stages):
        secondaries: list[ConnectedClip] = []
        for sec in stage_comp.secondaries:
            transform = _pip_to_transform(sec.pip, seq) if sec.pip is not None else None
            secondaries.append(
                ConnectedClip(
                    asset=Asset(path=sec.video_path, metadata=sec.video),
                    label=sec.label,
                    role="cam",
                    beep_offset_seconds=sec.beep_offset_seconds,
                    transform=transform,
                )
            )

        overlay: ConnectedClip | None = None
        if stage_comp.overlay_path is not None:
            overlay_meta = (
                stage_comp.overlay_video
                if stage_comp.overlay_video is not None
                else stage_comp.video
            )
            overlay = ConnectedClip(
                asset=Asset(path=stage_comp.overlay_path, metadata=overlay_meta),
                label="Splitsmith overlay",
                role="overlay",
            )

        markers = tuple(
            Marker(
                time_seconds=stage_comp.beep_offset_seconds + s.time_from_beep,
                shot=s,
            )
            for s in stage_comp.shots
        )

        ir_stages.append(
            Stage(
                name=stage_comp.stage_name,
                primary=Asset(path=stage_comp.video_path, metadata=stage_comp.video),
                beep_offset_seconds=stage_comp.beep_offset_seconds,
                head_pad_seconds=stage_comp.head_pad_seconds,
                tail_pad_seconds=stage_comp.tail_pad_seconds,
                secondaries=tuple(secondaries),
                overlay=overlay,
                markers=markers,
                title=titles_map.get(stage_idx),
            )
        )

    return Composition(
        project_name=project_name,
        sequence=seq,
        stages=tuple(ir_stages),
        transitions=tuple(transitions),
    )


def to_stage_compositions(
    composition: Composition,
) -> list[fcpxml_gen.StageComposition]:
    """Lower a :class:`Composition` back to ``StageComposition`` inputs.

    The bridge that lets ``render_fcpxml`` reuse today's emitter without
    forking it. ``Transform`` -> ``PipPlacement`` is lossy (we don't
    recover the original corner / margin_pct), so we synthesise a
    ``PipPlacement`` carrying the absolute position via a custom corner
    that lowers to the same pixel coordinates. For now we only handle the
    single supported case (uniform scale, axis-aligned corner) by
    matching the IR's stored position back to a corner -- if the user
    constructed a ``Transform`` by hand at an arbitrary position, the
    bridge raises rather than silently snapping. Future PRs will replace
    this bridge with direct emission and the lossy step disappears.
    """
    out: list[fcpxml_gen.StageComposition] = []
    for stage in composition.stages:
        secondaries: list[fcpxml_gen.SecondaryClip] = []
        for sec in stage.secondaries:
            pip = (
                _transform_to_pip(sec.transform, composition.sequence)
                if sec.transform is not None
                else None
            )
            assert sec.beep_offset_seconds is not None  # cam role guarantees this
            secondaries.append(
                fcpxml_gen.SecondaryClip(
                    video_path=sec.asset.path,
                    video=sec.asset.metadata,
                    beep_offset_seconds=sec.beep_offset_seconds,
                    label=sec.label,
                    pip=pip,
                )
            )
        overlay_path = stage.overlay.asset.path if stage.overlay is not None else None
        overlay_video = stage.overlay.asset.metadata if stage.overlay is not None else None
        out.append(
            fcpxml_gen.StageComposition(
                stage_name=stage.name,
                video_path=stage.primary.path,
                video=stage.primary.metadata,
                shots=[m.shot for m in stage.markers],
                beep_offset_seconds=stage.beep_offset_seconds,
                head_pad_seconds=stage.head_pad_seconds,
                tail_pad_seconds=stage.tail_pad_seconds,
                overlay_path=overlay_path,
                overlay_video=overlay_video,
                secondaries=tuple(secondaries),
            )
        )
    return out


def _transform_to_pip(
    transform: Transform,
    seq: SequenceFormat,
) -> fcpxml_gen.PipPlacement:
    """Recover a ``PipPlacement`` from an absolute ``Transform``.

    Inverts ``_pip_to_transform``: given the stored scale + pixel
    position, find the corner / margin_pct that would have produced
    those values for this sequence size. Raises ``ValueError`` if the
    transform doesn't match a corner-aligned PiP (the only kind today's
    emitter knows how to produce).
    """
    px, py = transform.position
    scale = transform.scale
    half_w = seq.width / 2.0
    half_h = seq.height / 2.0
    clip_half_w = half_w * scale
    clip_half_h = half_h * scale
    # |abs(px) - (half_w - clip_half_w - margin_x)| ~ 0
    # -> margin_x = half_w - clip_half_w - abs(px)
    margin_x = half_w - clip_half_w - abs(px)
    margin_y = half_h - clip_half_h - abs(py)
    margin_pct_x = margin_x / seq.width * 100.0
    margin_pct_y = margin_y / seq.height * 100.0
    if abs(margin_pct_x - margin_pct_y) > 1e-6:
        raise ValueError(
            "Transform does not match a corner-aligned PiP "
            f"(margin_pct mismatch: x={margin_pct_x}, y={margin_pct_y})"
        )
    if px >= 0 and py >= 0:
        corner: fcpxml_gen.PipCorner = "top-right"
    elif px < 0 and py >= 0:
        corner = "top-left"
    elif px >= 0 and py < 0:
        corner = "bottom-right"
    else:
        corner = "bottom-left"
    return fcpxml_gen.PipPlacement(
        corner=corner,
        scale=scale,
        margin_pct=margin_pct_x,
    )


# --- renderers -------------------------------------------------------------


def render_fcpxml(
    composition: Composition,
    *,
    output_path: Path,
    config: OutputConfig,
) -> None:
    """Render ``composition`` as FCPXML 1.10.

    v1 implementation: lower the IR to ``StageComposition`` and call the
    existing ``generate_match_fcpxml``. This guarantees byte-identical
    output to today's path; a sibling test fixture compares the IR-driven
    output against the legacy emitter on every supported input shape.

    Future PRs will replace this bridge with direct emission off the IR
    so transitions / titles / new renderers can reach the wire.
    """
    legacy_stages = to_stage_compositions(composition)
    has_titles = any(s.title is not None for s in composition.stages)
    if (
        len(legacy_stages) == 1
        and composition.intro is None
        and composition.outro is None
        and not composition.transitions
        and not has_titles
    ):
        # Single stage with no intro/outro/transitions/titles mirrors
        # today's per-stage path. Use generate_fcpxml so the file is
        # byte-identical to a single-stage export.
        stage = legacy_stages[0]
        fcpxml_gen.generate_fcpxml(
            video_path=stage.video_path,
            video=stage.video,
            shots=stage.shots,
            beep_offset_seconds=stage.beep_offset_seconds,
            output_path=output_path,
            project_name=composition.project_name,
            config=config,
            secondaries=list(stage.secondaries),
            overlay_path=stage.overlay_path,
            overlay_video=stage.overlay_video,
        )
        return
    fcpxml_gen.generate_match_fcpxml(
        stages=legacy_stages,
        output_path=output_path,
        project_name=composition.project_name,
        config=config,
        transitions=_lower_transitions(composition.transitions),
        titles=_lower_titles(composition),
    )


def _lower_transitions(
    transitions: tuple[Transition, ...],
) -> list[fcpxml_gen.StageTransition]:
    """Lower IR transitions to ``fcpxml_gen.StageTransition`` for the
    legacy emitter. v1: only consecutive transitions
    (``to_index == from_index + 1``) are accepted."""
    out: list[fcpxml_gen.StageTransition] = []
    for t in transitions:
        if t.to_stage_index != t.from_stage_index + 1:
            raise ValueError(
                "non-consecutive transitions are not supported "
                f"(from_stage_index={t.from_stage_index}, "
                f"to_stage_index={t.to_stage_index})"
            )
        out.append(
            fcpxml_gen.StageTransition(
                after_stage_index=t.from_stage_index,
                kind=t.kind,
                duration_seconds=t.duration_seconds,
                color=t.color,
            )
        )
    return out


def _lower_titles(composition: Composition) -> list[fcpxml_gen.StageTitle]:
    """Lower per-stage IR titles to ``fcpxml_gen.StageTitle`` for the
    legacy emitter (issue #196)."""
    out: list[fcpxml_gen.StageTitle] = []
    for idx, stage in enumerate(composition.stages):
        if stage.title is None:
            continue
        out.append(
            fcpxml_gen.StageTitle(
                stage_index=idx,
                text=stage.title.text,
                style=stage.title.style,
                duration_seconds=stage.title.duration_seconds,
                font_size=stage.title.font_size,
                font=stage.title.font,
                color=stage.title.color,
            )
        )
    return out


__all__ = [
    "Asset",
    "Composition",
    "ConnectedClip",
    "ConnectedRole",
    "Marker",
    "Segment",
    "SequenceFormat",
    "Stage",
    "TitleCard",
    "TitleStyle",
    "Transform",
    "Transition",
    "TransitionKind",
    "from_stage_compositions",
    "render_fcpxml",
    "to_stage_compositions",
]
