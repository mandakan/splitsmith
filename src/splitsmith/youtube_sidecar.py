"""YouTube sidecar metadata for match exports (issue #204 layer 1).

Walks the :class:`composition.Composition` after the export has been
built and writes a JSON sidecar plus an optional ``.srt`` alongside
the main output. The sidecar carries everything the user would
otherwise paste into YouTube Studio by hand: title, description with
per-stage chapter timestamps, tags, and a captions reference.

YouTube auto-converts ``MM:SS Title`` lines in the description into
chapter markers as long as the first chapter is at ``0:00`` and there
are at least three of them. We pad with an "Intro" anchor at 0:00
when the user has an intro segment so the requirement holds even on
two-stage matches.

Layer 2 (codec-tuned MP4) and Layer 3 (Data-API direct upload) are
follow-ups; nothing here depends on them. The sidecar is renderer-
agnostic -- works whether the matched output is .fcpxml / .xml / .mp4.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .composition import Composition, Marker, Segment, Stage


@dataclass(frozen=True)
class Chapter:
    """A YouTube description chapter. Renders to ``MM:SS Title`` (or
    ``H:MM:SS Title`` for matches longer than an hour)."""

    start_seconds: float
    title: str


class YouTubeSidecar(BaseModel):
    """The full sidecar payload written to disk as JSON.

    Fields mirror what YouTube Studio asks for so the user can copy /
    paste each section verbatim or feed the JSON to a future Data API
    upload helper. ``description`` already contains the chapter
    timestamps embedded as ``MM:SS Title`` lines.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    chapters: list[dict[str, float | str]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str = "Sports"
    captions_path: str | None = None
    output_video: str | None = None  # relative path to the .mp4/.fcpxml when known


def build_sidecar(
    composition: Composition,
    *,
    title: str | None = None,
    description_lead: str | None = None,
    tags: list[str] | None = None,
    captions_path: Path | None = None,
    output_video: Path | None = None,
) -> YouTubeSidecar:
    """Build a :class:`YouTubeSidecar` from a composition.

    ``title`` defaults to the composition's project name; pass an
    explicit string to override (e.g. ``"<match> -- <date>"``).

    ``description_lead`` is prepended above the auto-generated
    chapter list -- room for a one-paragraph match summary, sponsor
    callouts, etc. Trailing whitespace is normalised so the chapter
    timestamps always land cleanly without YouTube munging.
    """
    chapters = _compute_chapters(composition)
    description = _format_description(
        chapters,
        lead=description_lead,
        sequence=composition,
    )
    return YouTubeSidecar(
        title=title or composition.project_name,
        description=description,
        chapters=[{"start_seconds": c.start_seconds, "title": c.title} for c in chapters],
        tags=list(tags or _default_tags(composition)),
        captions_path=str(captions_path) if captions_path is not None else None,
        output_video=str(output_video) if output_video is not None else None,
    )


def write_sidecar(sidecar: YouTubeSidecar, output_path: Path) -> None:
    """Write the sidecar JSON, indented for hand-editing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        sidecar.model_dump_json(indent=2),
        encoding="utf-8",
    )


def write_srt(composition: Composition, output_path: Path) -> None:
    """Emit shot markers as an ``.srt`` so YouTube can use them as
    closed captions. One short caption per shot at its spine time;
    duration is half a second so each label flashes during the shot
    rather than lingering."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    counter = 1
    cursor = _spine_offset_seconds_intro(composition)
    for stage_idx, stage in enumerate(composition.stages):
        stage_spine_start, stage_visible_head = _stage_spine_window(composition, stage_idx, cursor)
        effective = _effective_seconds(stage)
        for marker in stage.markers:
            local_offset = marker.time_seconds - stage_visible_head
            # Drop markers outside the visible window so the caption
            # track doesn't reference frames the timeline trimmed
            # away. Mirrors the FCPXML emitter's marker-drop check.
            if not 0.0 <= local_offset < effective:
                continue
            t = stage_spine_start + local_offset
            lines.append(_srt_block(counter, t, t + 0.5, _marker_caption(marker)))
            counter += 1
        cursor += _stage_spine_duration(composition, stage_idx)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


# --- internals -------------------------------------------------------------


def _compute_chapters(composition: Composition) -> list[Chapter]:
    """One chapter per stage; an "Intro" anchor at 0:00 when the
    composition has an intro segment so YouTube's three-chapter
    minimum is met for short matches."""
    chapters: list[Chapter] = []
    cursor = 0.0
    if composition.intro is not None:
        chapters.append(Chapter(start_seconds=0.0, title=_segment_label(composition.intro)))
        cursor += composition.intro.asset.metadata.duration_seconds
    for stage_idx, stage in enumerate(composition.stages):
        # Account for slate before the stage primary.
        if stage.title is not None and stage.title.style == "slate":
            cursor += stage.title.duration_seconds
        chapters.append(
            Chapter(
                start_seconds=cursor,
                title=stage.name or f"Stage {stage_idx + 1}",
            )
        )
        cursor += _effective_seconds(stage)
    if composition.outro is not None:
        chapters.append(
            Chapter(
                start_seconds=cursor,
                title=_segment_label(composition.outro),
            )
        )
    return chapters


def _format_description(
    chapters: list[Chapter],
    *,
    lead: str | None,
    sequence: Composition,
) -> str:
    """Assemble the description with the auto-generated chapter list.

    Chapters are appended *after* the lead with a blank line separator
    so YouTube's parser sees them on their own paragraph. Times use
    ``H:MM:SS`` past the hour mark; YouTube accepts both.
    """
    parts: list[str] = []
    if lead:
        parts.append(lead.rstrip())
        parts.append("")
    parts.append("Chapters:")
    for c in chapters:
        parts.append(f"{_format_chapter_time(c.start_seconds)} {c.title}")
    parts.append("")
    parts.append(f"-- Generated by splitsmith ({sequence.project_name})")
    return "\n".join(parts)


def _format_chapter_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _default_tags(composition: Composition) -> list[str]:
    """Tags that consistently apply to splitsmith exports. Users can
    extend / replace via the ``tags`` argument when generating."""
    return ["IPSC", "match", composition.project_name]


def _segment_label(segment: Segment) -> str:
    return segment.name or "Intro"


def _effective_seconds(stage: Stage) -> float:
    """Mirror the FCPXML emitter's per-stage trim math (in seconds).

    The renderer enforces frame alignment and we live with seconds
    here because YouTube chapters resolve at second granularity --
    sub-second precision wouldn't survive the description anyway.
    """
    duration = stage.primary.metadata.duration_seconds
    head_avail = max(0.0, stage.beep_offset_seconds)
    if stage.markers:
        last_local = max(m.time_seconds for m in stage.markers)
    else:
        last_local = stage.beep_offset_seconds
    tail_avail = max(0.0, duration - last_local)
    head_trim = max(0.0, head_avail - stage.head_pad_seconds)
    tail_trim = max(0.0, tail_avail - stage.tail_pad_seconds)
    return max(0.0, duration - head_trim - tail_trim)


def _stage_spine_duration(composition: Composition, stage_idx: int) -> float:
    """Total spine time the stage occupies (slate + effective)."""
    stage = composition.stages[stage_idx]
    extra = 0.0
    if stage.title is not None and stage.title.style == "slate":
        extra = stage.title.duration_seconds
    return extra + _effective_seconds(stage)


def _spine_offset_seconds_intro(composition: Composition) -> float:
    return composition.intro.asset.metadata.duration_seconds if composition.intro else 0.0


def _stage_spine_window(
    composition: Composition,
    stage_idx: int,
    cursor: float,
) -> tuple[float, float]:
    """Return (spine_offset, visible_head_in_source_time) for a stage.

    ``visible_head`` is the source-time of the stage's first visible
    frame -- i.e., where the per-stage head trim leaves off. The .srt
    walker subtracts this from each marker's source-time to land on
    the spine.
    """
    stage = composition.stages[stage_idx]
    spine_offset = cursor
    if stage.title is not None and stage.title.style == "slate":
        spine_offset += stage.title.duration_seconds
    head_avail = max(0.0, stage.beep_offset_seconds)
    head_trim = max(0.0, head_avail - stage.head_pad_seconds)
    return spine_offset, head_trim


def _srt_block(index: int, start: float, end: float, text: str) -> str:
    """Format a single .srt block. SRT timestamps are always
    ``HH:MM:SS,mmm``; YouTube and most browsers accept this."""
    return f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n"


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def _marker_caption(marker: Marker) -> str:
    shot = marker.shot
    n = getattr(shot, "shot_number", "?")
    split = getattr(shot, "split", None)
    if split is None:
        return f"Shot {n}"
    return f"Shot {n} ({split:.2f}s)"


__all__ = [
    "Chapter",
    "YouTubeSidecar",
    "build_sidecar",
    "write_sidecar",
    "write_srt",
]
