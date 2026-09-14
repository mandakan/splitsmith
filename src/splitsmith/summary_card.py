"""The per-stage summary card beside the overlay MOV (issue #972, option 1).

The single-shooter overlay is a transparent MOV matched frame-for-frame
to the trim, so it can neither extend the clip for a hold nor blur the
last frame. The result screen therefore ships as a separate artefact an
editor drops on the timeline after the stage: ``<base>_summary.png`` (the
composed still) and ``<base>_summary.mov`` (that still held for a few
seconds as ProRes 422 HQ, at the trim's own resolution and rate, video
only). The still is the same one the rendered MP4 holds on
(:func:`splitsmith.overlay_summary_cell.build_summary_still`), so the two
deliverables agree.

Degrades the way the rendered hold does: no usable browser keeps the
blurred freeze without text and records it; no frame *and* no text is a
:class:`SummaryCardError`, as is an encode ffmpeg refuses. ``export_stage``
records either as a skip reason rather than failing the stage.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import VideoMetadata
from .fcpxml_gen import probe_video
from .overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .overlay_summary_cell import build_summary_still
from .overlay_theme import ThemeName, load_theme
from .runtime import Runner, runtime
from .stage_summary_data import TileStageData

logger = logging.getLogger(__name__)

#: How far before the trim's end the backdrop grab starts reading; the
#: last decoded frame is kept. See ``overlay_summary._FREEZE_TAIL_WINDOW_SECONDS``
#: for why a seek straight to the last timestamp can come back empty.
_TAIL_WINDOW_SECONDS = 0.5

#: Default hold, matching the rendered MP4's ``SummaryHold``.
DEFAULT_SECONDS = 3.0


class SummaryCardError(RuntimeError):
    """The card could not be written: nothing to hold on, or ffmpeg refused."""


@dataclass(frozen=True)
class SummaryCardResult:
    png_path: Path
    mov_path: Path
    degradations: tuple[str, ...] = ()


def render_summary_card(
    *,
    data: TileStageData,
    label: str,
    trimmed_video_path: Path,
    png_path: Path,
    mov_path: Path,
    seconds: float = DEFAULT_SECONDS,
    theme: ThemeName = "splitsmith",
    ffmpeg_binary: str | None = None,
    runner: Runner = subprocess.run,
    rasterizer: Rasterizer | None = None,
    probe: Callable[[Path], VideoMetadata] | None = None,
) -> SummaryCardResult:
    """Write the stage's summary still and its held MOV next to the trim.

    ``rasterizer`` left ``None`` opens one
    :class:`~splitsmith.overlay_raster.ChromiumRasterizer` for the call; a
    browser that cannot launch degrades to the blurred freeze without
    text, reported on the result. ``runner`` and ``probe`` are the seams
    tests inject; both default to the real ffmpeg / ffprobe.
    """
    binary = ffmpeg_binary or runtime().ffmpeg_binary
    meta = (probe or probe_video)(trimmed_video_path)
    degradations: list[str] = []

    backdrop = _grab_last_frame(
        trimmed_video_path,
        meta,
        target=png_path.with_name(png_path.stem + "_backdrop.png"),
        ffmpeg_binary=binary,
        runner=runner,
    )
    try:
        active: Rasterizer | None = rasterizer
        owned: ChromiumRasterizer | None = None
        if rasterizer is None:
            owned = ChromiumRasterizer()
            try:
                active = owned.__enter__()
            except RasterizerUnavailableError as exc:
                owned = None
                active = None
                degradations.append(f"summary text skipped: {exc.detail}")
                logger.warning("%s", degradations[-1])
        try:
            still = build_summary_still(
                data,
                label,
                width=meta.width,
                height=meta.height,
                theme=load_theme(theme),
                rasterizer=active,
                backdrop=backdrop,
            )
        finally:
            if owned is not None:
                owned.__exit__(None, None, None)
    finally:
        if backdrop is not None:
            backdrop.unlink(missing_ok=True)

    if still is None:
        raise SummaryCardError(
            f"no frame could be read from {trimmed_video_path} and no browser could draw the text; "
            "nothing to hold on"
        )
    png_path.parent.mkdir(parents=True, exist_ok=True)
    still.save(png_path)

    cmd = _build_mov_command(png_path, seconds=seconds, meta=meta, output_path=mov_path, ffmpeg_binary=binary)
    try:
        completed = runner(list(cmd), capture_output=True)
    except FileNotFoundError as exc:
        raise SummaryCardError(f"ffmpeg binary not found: {binary}") from exc
    if completed.returncode != 0:
        raw = completed.stderr or completed.stdout or b""
        detail = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        raise SummaryCardError(
            f"ffmpeg exited {completed.returncode} writing {mov_path.name}: {detail.strip()[-2000:]}"
        )
    return SummaryCardResult(png_path=png_path, mov_path=mov_path, degradations=tuple(degradations))


def _grab_last_frame(
    trim: Path, meta: VideoMetadata, *, target: Path, ffmpeg_binary: str, runner: Runner
) -> Path | None:
    """The trim's last frame, or ``None`` when the grab produced no file."""
    target.unlink(missing_ok=True)
    seek = max(0.0, meta.duration_seconds - _TAIL_WINDOW_SECONDS)
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-ss",
        f"{seek:g}",
        "-t",
        f"{_TAIL_WINDOW_SECONDS:g}",
        "-i",
        str(trim),
        "-an",
        "-update",
        "1",
        str(target),
    ]
    try:
        completed = runner(cmd, capture_output=True)
    except Exception as exc:  # noqa: BLE001 -- the frame is a picture, not the card
        logger.warning("could not grab the last frame of %s (%s); the card composes flat", trim, exc)
        return None
    if completed.returncode != 0:
        return None
    try:
        return target if target.stat().st_size > 0 else None
    except OSError:
        return None


def _build_mov_command(
    png_path: Path, *, seconds: float, meta: VideoMetadata, output_path: Path, ffmpeg_binary: str
) -> tuple[str, ...]:
    """Hold the still for ``seconds`` as ProRes 422 HQ at the trim's rate.

    ProRes rather than H.264 because the clip is an editing intermediate
    that goes straight onto an FCP timeline next to the trim; a few
    seconds of it is small either way. Video only: the trim's own audio
    carries the stage, and a silent track would only need muting.
    """
    rate = f"{meta.frame_rate_num}/{meta.frame_rate_den}"
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-loop",
        "1",
        "-framerate",
        rate,
        "-i",
        str(png_path),
        "-t",
        f"{seconds:g}",
        "-an",
        "-vf",
        "format=yuv422p10le,setsar=1",
        "-r",
        rate,
        "-c:v",
        "prores_ks",
        "-profile:v",
        "3",
        str(output_path),
    )


__all__ = ["DEFAULT_SECONDS", "SummaryCardError", "SummaryCardResult", "render_summary_card"]
