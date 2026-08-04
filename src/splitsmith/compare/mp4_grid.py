"""Direct-to-MP4 renderer for multi-shooter compare grids.

Sits beside :mod:`splitsmith.compare.emitter` (which emits FCPXML) and
consumes the same ``project_loader`` bundles and ``layout`` grid math.
Renders one ffmpeg call per stage -- scale + pad each tile to a uniform
cell, ``xstack`` them into the grid, map every shooter's audio as its
own output track -- then stitches the per-stage temps with the
``concat`` demuxer at ``-c copy``.

Phase 0 scope: no overlay, no transitions, no title cards. The overlay
lands in phase 1 as pre-rendered sprite PNGs; nothing here should make
that harder.

Determinism / testability: command construction is split into pure
functions (:func:`build_stage_command` / :func:`build_concat_command`)
with an injectable runner, mirroring :mod:`splitsmith.mp4_render` and
:mod:`splitsmith.trim`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .layout import Layout2Up, choose_grid, grid_shape
from .project_loader import CompareShooterBundle

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_CANVAS_WIDTH = 3840
DEFAULT_CANVAS_HEIGHT = 2160


class GridRenderError(RuntimeError):
    """ffmpeg refused to render a grid stage or the final stitch."""


@dataclass(frozen=True)
class GridCanvas:
    """Output geometry for the whole render.

    Pinned once and applied to every stage: ``concat -c copy`` rejects
    segments whose video parameters differ.
    """

    width: int = DEFAULT_CANVAS_WIDTH
    height: int = DEFAULT_CANVAS_HEIGHT
    frame_rate_num: int = 30000
    frame_rate_den: int = 1001

    @property
    def fps(self) -> float:
        return self.frame_rate_num / self.frame_rate_den


@dataclass(frozen=True)
class GridTile:
    """One shooter's cell in one stage.

    ``trim_path=None`` means the shooter has no trim for this stage: the
    cell renders black and contributes a silent audio track. The slot is
    never dropped -- doing so would shuffle the grid between stages and
    change the stream count, which breaks the concat stitch.
    """

    label: str
    trim_path: Path | None
    beep_offset_in_clip: float
    seek_seconds: float
    row: int
    col: int


@dataclass(frozen=True)
class GridStagePlan:
    """Everything one ffmpeg invocation needs for one stage."""

    stage_number: int
    stage_name: str
    tiles: tuple[GridTile, ...]
    duration_seconds: float
    audio_label: str
    rows: int
    cols: int


def build_stage_plans(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    head_pad_seconds: float,
    tail_pad_seconds: float,
    layout_2up: Layout2Up = "horizontal",
) -> tuple[GridStagePlan, ...]:
    """Plan one grid stage per stage number present on any shooter.

    Slots are alphabetical by label and stable across stages, matching
    ``compare/emitter.py``'s rule: a label always lands in the same cell
    and a missing trim becomes filler rather than reshuffling the grid.
    """
    labels = sorted(s.label for s in shooters)
    if audio_label not in labels:
        raise ValueError(f"audio_label={audio_label!r} matches no shooter. Labels: {', '.join(labels)}")

    by_label = {s.label: s for s in shooters}
    rows, cols = grid_shape(choose_grid(len(labels), layout_2up=layout_2up))

    stage_numbers = sorted({n for s in shooters for n in s.stages_by_number})

    plans: list[GridStagePlan] = []
    for stage_number in stage_numbers:
        tiles: list[GridTile] = []
        post_beep_spans: list[float] = []
        stage_name = ""
        for index, label in enumerate(labels):
            bundle = by_label[label].stages_by_number.get(stage_number)
            row, col = divmod(index, cols)
            if bundle is None:
                tiles.append(
                    GridTile(
                        label=label,
                        trim_path=None,
                        beep_offset_in_clip=0.0,
                        seek_seconds=0.0,
                        row=row,
                        col=col,
                    )
                )
                continue
            stage_name = stage_name or bundle.stage_name
            post_beep_spans.append(bundle.duration_seconds - bundle.beep_offset_in_clip)
            tiles.append(
                GridTile(
                    label=label,
                    trim_path=bundle.trim_path,
                    beep_offset_in_clip=bundle.beep_offset_in_clip,
                    seek_seconds=max(0.0, bundle.beep_offset_in_clip - head_pad_seconds),
                    row=row,
                    col=col,
                )
            )

        duration = head_pad_seconds + max(post_beep_spans, default=0.0) + tail_pad_seconds
        plans.append(
            GridStagePlan(
                stage_number=stage_number,
                stage_name=stage_name or f"Stage {stage_number}",
                tiles=tuple(tiles),
                duration_seconds=duration,
                audio_label=audio_label,
                rows=rows,
                cols=cols,
            )
        )
    return tuple(plans)
