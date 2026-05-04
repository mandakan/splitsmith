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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
        # Freeze the timer once the last shot has fired -- the running total
        # is the stage time, not the clip duration. Pre-beep frames clamp at
        # 0; everything between ticks; everything after the last shot holds
        # at the final stage time.
        if shot_count > 0 and fired == shot_count:
            running_total = max(0.0, shots_sorted[-1] - beep_time_in_clip)
        else:
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
        font_name: str | None = None,
        split_hold_seconds: float = 1.0,
        split_fade_seconds: float = 0.3,
        stroke_width_px: int | None = None,
        shadow_blur_px: int | None = None,
        shadow_offset_px: int | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.split_hold_seconds = split_hold_seconds
        self.split_fade_seconds = split_fade_seconds
        big = max(48, height // 14)
        try:
            self.font_big = _load_font(font_path, big, font_name=font_name)
        except OSError as exc:
            raise OverlayRenderError(f"failed to load font: {exc}") from exc
        self.pad = max(24, height // 36)
        # Legibility defaults scale with the type size so 1080p / 4K stay
        # consistent. Stroke at ~6% of the cap-height reads as crisp without
        # turning the glyphs into blobs; shadow blur slightly larger than
        # offset gives a soft halo rather than a hard duplicate.
        self.stroke_width_px = stroke_width_px if stroke_width_px is not None else max(2, big // 18)
        self.shadow_offset_px = (
            shadow_offset_px if shadow_offset_px is not None else max(2, big // 24)
        )
        self.shadow_blur_px = shadow_blur_px if shadow_blur_px is not None else max(3, big // 12)

    def draw_frame(self, canvas: Image.Image, state: FrameState) -> None:
        d = ImageDraw.Draw(canvas)

        if state.shot_count > 0:
            shot_text = f"{state.shots_fired}/{state.shot_count}"
            self._draw(canvas, d, (self.pad, self.pad), shot_text, (255, 255, 255, 255))

        total_text = _format_running_total(state.running_total)
        bbox = d.textbbox((0, 0), total_text, font=self.font_big)
        tw = bbox[2] - bbox[0]
        self._draw(
            canvas,
            d,
            (self.width - tw - self.pad, self.pad),
            total_text,
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
                self._draw(canvas, d, (x, y), split_text, (255, 220, 80, alpha))

    def _draw(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        xy: tuple[int, int],
        text: str,
        fill: tuple[int, int, int, int],
    ) -> None:
        _draw_text_with_shadow(
            draw,
            canvas,
            xy,
            text,
            self.font_big,
            fill,
            stroke_width=self.stroke_width_px,
            shadow_offset=self.shadow_offset_px,
            shadow_blur=self.shadow_blur_px,
        )


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


# Named font presets the user can select without hunting for a path.
# Order inside each tuple is preferred-first (bold variants beat regular for
# legibility against busy backgrounds). Unknown / missing files fall through
# to the generic fallback list below.
_FONT_PRESETS: dict[str, tuple[str, ...]] = {
    "menlo": ("/System/Library/Fonts/Menlo.ttc",),
    "monaco": ("/System/Library/Fonts/Monaco.ttf",),
    "sf-mono": (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/SF-Mono-Bold.otf",
        "/Library/Fonts/SF-Mono-Regular.otf",
    ),
    "sf-pro": (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
    ),
    "helvetica": ("/System/Library/Fonts/Helvetica.ttc",),
    "dejavu-mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ),
    "consolas": (
        "C:/Windows/Fonts/consolab.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ),
    "courier": (
        "C:/Windows/Fonts/courbd.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ),
}

_FONT_FALLBACKS: tuple[str, ...] = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    # Windows: Consolas ships with Vista+, Courier New / Lucida Console are
    # always present. PIL accepts forward slashes here on Windows too.
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/lucon.ttf",
    "C:/Windows/Fonts/cour.ttf",
)


def available_font_names() -> tuple[str, ...]:
    """Preset font names accepted by :func:`_load_font` / template kwargs.
    Exposed so a future template config UI can offer a real picker."""
    return tuple(_FONT_PRESETS.keys())


def _load_font(
    font_path: Path | None,
    size: int,
    *,
    font_name: str | None = None,
) -> ImageFont.ImageFont:
    if font_path is not None:
        return ImageFont.truetype(str(font_path), size=size)
    if font_name is not None:
        key = font_name.lower()
        if key not in _FONT_PRESETS:
            raise OverlayRenderError(
                f"unknown font_name {font_name!r}; "
                f"available: {', '.join(available_font_names())}"
            )
        for candidate in _FONT_PRESETS[key]:
            p = Path(candidate)
            if p.exists():
                return ImageFont.truetype(str(p), size=size)
        # Named preset asked for but no file found -- fall through to the
        # generic discovery list rather than crashing the export.
    for candidate in _FONT_FALLBACKS:
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    stroke_width: int = 2,
    shadow_offset: int = 3,
    shadow_blur: int = 6,
) -> None:
    """Stroke + soft drop shadow so text reads on bright/busy backgrounds.

    The shadow is rendered into a tight per-text scratch layer (textbbox
    plus padding for the blur kernel) and composited onto ``canvas`` --
    cheaper than a full-frame blur and identical visually. The foreground
    glyph is then drawn with a crisp black stroke. Shadow alpha tracks
    the foreground alpha so the last-split fade stays clean.
    """
    x, y = xy
    fg_alpha = fill[3]
    if fg_alpha <= 0:
        return
    shadow_alpha = int(fg_alpha * 0.65)

    if shadow_alpha > 0:
        bbox = draw.textbbox(xy, text, font=font, stroke_width=stroke_width)
        pad = max(1, shadow_blur * 2 + shadow_offset + stroke_width)
        sx0, sy0 = bbox[0] - pad, bbox[1] - pad
        sx1, sy1 = bbox[2] + pad, bbox[3] + pad
        sw, sh = sx1 - sx0, sy1 - sy0
        if sw > 0 and sh > 0:
            shadow_img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow_img)
            sd.text(
                (x - sx0 + shadow_offset, y - sy0 + shadow_offset),
                text,
                font=font,
                fill=(0, 0, 0, shadow_alpha),
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, shadow_alpha),
            )
            if shadow_blur > 0:
                shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(shadow_blur))
            canvas.alpha_composite(shadow_img, (sx0, sy0))

    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, fg_alpha),
    )


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
    font_name: str | None = None,
    font_path: Path | None = None,
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
    ``font_name`` / ``font_path``: passed to the default template when
        ``template`` isn't supplied. ``font_name`` accepts a preset from
        :func:`available_font_names`; ``font_path`` is an explicit override.
        Ignored when the caller passes a fully-built ``template``.
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
        template = DefaultTemplate(
            width=width, height=height, font_path=font_path, font_name=font_name
        )

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
