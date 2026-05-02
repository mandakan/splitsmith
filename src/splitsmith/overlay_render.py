"""Pre-rendered overlay MOV (alpha) for FCP composite (issue #45).

Generates a transparent video per stage that drops onto the trimmed clip in
FCP as a connected clip on V2. The overlay matches the trim frame-for-frame:
same fps, resolution and duration; ProRes 4444 with an alpha channel.

Pipeline:
1. Probe the trimmed clip with ffprobe -- never trust user config; the
   overlay must mirror the source or it will drift off the timeline.
2. Build per-frame state from the audit JSON (which shots have fired by
   time t, the most recent split, the running total since the beep).
3. PIL renders each RGBA frame.
4. Pipe raw RGBA bytes to ``ffmpeg -f rawvideo ... -c:v prores_ks
   -profile:v 4444 -pix_fmt yuva444p10le`` writing the final MOV.

A :class:`Template` ABC keeps the v1 layout pluggable: a future second
template is a subclass with its own ``draw_frame``, not a rewrite of the
renderer. v1 ships exactly one template -- :class:`DefaultTemplate`.

The renderer is pure of detection: the audit JSON is the source of truth.
Stages without a completed audit cannot render an overlay -- callers MUST
gate on that before invoking :func:`render_overlay`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import VideoMetadata
from .fcpxml_gen import probe_video


@dataclass(frozen=True)
class FrameState:
    """Per-frame overlay state derived from the audit JSON.

    All times are seconds in the trimmed clip's local timeline (i.e., from
    the clip's t=0). ``beep_time_in_clip`` is where the start beep lives in
    the same timeline -- typically equal to the trim's pre-buffer.
    """

    time_seconds: float
    beep_time_in_clip: float
    shot_count: int  # M -- total kept shots in the stage
    shots_fired: int  # N -- how many shots have been fired by ``time_seconds``
    last_split: float | None  # split of the most-recently-fired shot
    last_shot_time_in_clip: float | None  # for fade timing on the last-split label
    running_total: float  # max(0, time_seconds - beep_time_in_clip)


class OverlayRenderError(RuntimeError):
    """Raised when the audit JSON is missing / malformed, when ffmpeg
    blows up, or when the trimmed clip can't be probed."""


class Template(ABC):
    """Pluggable overlay layout.

    Subclasses mutate the supplied RGBA canvas in place to draw the overlay
    for one frame's :class:`FrameState`. v1 ships :class:`DefaultTemplate`;
    a second template lands as a subclass without touching the renderer.
    """

    @abstractmethod
    def draw_frame(self, canvas: Image.Image, state: FrameState) -> None:
        """Render one frame onto ``canvas`` (mode ``RGBA``)."""


def build_frame_states(
    *,
    shot_times_in_clip: list[float],
    beep_time_in_clip: float,
    fps: float,
    duration_seconds: float,
) -> list[FrameState]:
    """Pre-compute every frame's state. Pure -- no I/O.

    The result has exactly ``round(duration_seconds * fps)`` entries; entry
    ``i`` describes the frame at ``i / fps``. ``shot_times_in_clip`` is
    sorted before scanning so out-of-order audit JSONs don't bleed shots
    into the wrong frames.
    """
    n_frames = max(0, int(round(duration_seconds * fps)))
    shots_sorted = sorted(shot_times_in_clip)
    shot_count = len(shots_sorted)
    states: list[FrameState] = []
    cursor = 0  # index of the first shot whose time > current frame time
    for i in range(n_frames):
        t = i / fps
        while cursor < shot_count and shots_sorted[cursor] <= t:
            cursor += 1
        fired = cursor
        if fired == 0:
            last_shot = None
            last_split: float | None = None
        else:
            last_shot = shots_sorted[fired - 1]
            if fired == 1:
                # Shot 1's "split" is the draw -- its time from the beep.
                last_split = shots_sorted[0] - beep_time_in_clip
            else:
                last_split = shots_sorted[fired - 1] - shots_sorted[fired - 2]
        running_total = max(0.0, t - beep_time_in_clip)
        states.append(
            FrameState(
                time_seconds=t,
                beep_time_in_clip=beep_time_in_clip,
                shot_count=shot_count,
                shots_fired=fired,
                last_split=last_split,
                last_shot_time_in_clip=last_shot,
                running_total=running_total,
            )
        )
    return states


class DefaultTemplate(Template):
    """v1 layout. Three pieces:

    - Top-left: ``N/M`` (current of total kept shots).
    - Top-right: running total elapsed since the beep. Holds at ``00.00``
      pre-beep; ticks up after.
    - Bottom-center: most recent split, held at full alpha for
      ``split_hold_seconds`` after each shot, then fades over
      ``split_fade_seconds``.

    Sizes scale to the source resolution so 1080p / 2K / 4K all look
    reasonable without re-tuning. Numerals use a monospaced TTF when one
    can be located; otherwise PIL's default bitmap font, which is ugly
    but always available (CI / minimal Linux).
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        font_path: Path | None = None,
        split_hold_seconds: float = 1.0,
        split_fade_seconds: float = 0.3,
    ) -> None:
        self.width = width
        self.height = height
        self.split_hold_seconds = split_hold_seconds
        self.split_fade_seconds = split_fade_seconds
        big = max(48, height // 14)
        try:
            self.font_big = _load_font(font_path, big)
        except OSError as exc:
            raise OverlayRenderError(f"failed to load font: {exc}") from exc
        self.pad = max(24, height // 36)

    def draw_frame(self, canvas: Image.Image, state: FrameState) -> None:
        d = ImageDraw.Draw(canvas)

        if state.shot_count > 0:
            shot_text = f"{state.shots_fired}/{state.shot_count}"
            _draw_text_with_shadow(
                d, (self.pad, self.pad), shot_text, self.font_big, (255, 255, 255, 255)
            )

        total_text = _format_running_total(state.running_total)
        bbox = d.textbbox((0, 0), total_text, font=self.font_big)
        tw = bbox[2] - bbox[0]
        _draw_text_with_shadow(
            d,
            (self.width - tw - self.pad, self.pad),
            total_text,
            self.font_big,
            (255, 255, 255, 255),
        )

        if state.last_split is not None and state.last_shot_time_in_clip is not None:
            since_shot = state.time_seconds - state.last_shot_time_in_clip
            alpha = _split_alpha(since_shot, self.split_hold_seconds, self.split_fade_seconds)
            if alpha > 0:
                split_text = f"{state.last_split:.2f}s"
                bbox = d.textbbox((0, 0), split_text, font=self.font_big)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                x = (self.width - tw) // 2
                y = self.height - th - self.pad * 2
                _draw_text_with_shadow(d, (x, y), split_text, self.font_big, (255, 220, 80, alpha))


def _split_alpha(since_shot: float, hold: float, fade: float) -> int:
    """0..255 alpha for the "last split" label given seconds-since-shot."""
    if since_shot < 0:
        return 0
    if since_shot <= hold:
        return 255
    if fade <= 0 or since_shot >= hold + fade:
        return 0
    t = (since_shot - hold) / fade
    return int(round(255 * (1.0 - t)))


def _format_running_total(seconds: float) -> str:
    """``SS.SS`` under a minute; ``M:SS.SS`` past it. Width-stable so the
    overlay doesn't jitter from frame to frame."""
    if seconds < 60:
        return f"{seconds:5.2f}"
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:d}:{s:05.2f}"


_FONT_FALLBACKS: tuple[str, ...] = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)


def _load_font(font_path: Path | None, size: int) -> ImageFont.ImageFont:
    if font_path is not None:
        return ImageFont.truetype(str(font_path), size=size)
    for candidate in _FONT_FALLBACKS:
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    """1-px black shadow under coloured text so the overlay stays legible
    over bright backgrounds. The shadow's alpha tracks the foreground so
    fades stay clean."""
    x, y = xy
    shadow_alpha = max(0, fill[3] - 64)
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, shadow_alpha))
    draw.text(xy, text, font=font, fill=fill)


def _shot_times_from_audit(audit_data: dict, *, beep_offset_seconds: float) -> list[float]:
    """Convert audit JSON shots to clip-local seconds. Skips shots without
    ``ms_after_beep`` -- those aren't placed on the timer's timeline yet."""
    raw_shots = audit_data.get("shots") if isinstance(audit_data, dict) else None
    out: list[float] = []
    if not isinstance(raw_shots, list):
        return out
    for raw in raw_shots:
        if not isinstance(raw, dict):
            continue
        ms = raw.get("ms_after_beep")
        if ms is None:
            continue
        try:
            out.append(beep_offset_seconds + float(ms) / 1000.0)
        except (TypeError, ValueError):
            continue
    return out


def render_overlay(
    *,
    audit_path: Path,
    trimmed_video_path: Path,
    output_path: Path,
    beep_offset_seconds: float,
    template: Template | None = None,
    ffmpeg_binary: str = "ffmpeg",
    probe: VideoMetadata | None = None,
) -> Path:
    """Render an alpha overlay MOV alongside a trimmed clip.

    ``audit_path``: ``stage<N>.json`` with the user's audited ``shots[]``.
        This is the source of truth -- raw detector output is not allowed
        to render anywhere.
    ``trimmed_video_path``: the lossless trim that the FCP timeline
        references. Probed for fps / width / height / duration so the
        overlay matches frame-for-frame.
    ``beep_offset_seconds``: where the beep lives in the trimmed clip.
        Audit ``ms_after_beep`` is converted to clip-local time as
        ``beep_offset + ms_after_beep / 1000``.
    ``template``: defaults to :class:`DefaultTemplate` sized to the probe.
    ``probe``: optional pre-computed metadata. When given, ``ffprobe`` is
        skipped -- useful from tests and to share one probe across the
        export's other steps.

    Returns the written ``output_path``.
    """
    if not audit_path.exists():
        raise OverlayRenderError(f"no audit JSON at {audit_path}; finish auditing this stage first")
    try:
        audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverlayRenderError(f"failed to read audit JSON {audit_path}: {exc}") from exc

    shot_times = _shot_times_from_audit(audit_data, beep_offset_seconds=beep_offset_seconds)
    if not shot_times:
        raise OverlayRenderError(
            f"audit JSON {audit_path} has no shots with ms_after_beep set; " "nothing to render"
        )

    if probe is None:
        probe = probe_video(trimmed_video_path)
    width = probe.width
    height = probe.height
    fps = probe.frame_rate_num / probe.frame_rate_den
    duration_seconds = probe.duration_seconds

    if template is None:
        template = DefaultTemplate(width=width, height=height)

    states = build_frame_states(
        shot_times_in_clip=shot_times,
        beep_time_in_clip=beep_offset_seconds,
        fps=fps,
        duration_seconds=duration_seconds,
    )

    if shutil.which(ffmpeg_binary) is None:
        raise OverlayRenderError(f"ffmpeg binary not found: {ffmpeg_binary}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    rate = f"{probe.frame_rate_num}/{probe.frame_rate_den}"
    cmd = [
        ffmpeg_binary,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        rate,
        "-i",
        "-",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        "-r",
        rate,
        str(output_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for state in states:
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            template.draw_frame(canvas, state)
            proc.stdin.write(canvas.tobytes())
        proc.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        proc.kill()
        proc.wait()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise OverlayRenderError(f"ffmpeg failed during render: {stderr or exc}") from exc

    rc = proc.wait()
    if rc != 0:
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr}")
    return output_path
