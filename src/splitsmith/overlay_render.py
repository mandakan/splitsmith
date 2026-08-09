"""Pre-rendered overlay MOV (alpha) for FCP composite (issue #45).

Generates a transparent video per stage that drops onto the trimmed clip in
FCP as a connected clip on V2. The overlay matches the trim frame-for-frame:
same fps, resolution and duration; ProRes 4444 with an alpha channel.

Pipeline:
1. Probe the trimmed clip with ffprobe -- never trust user config; the
   overlay must mirror the source or it will drift off the timeline.
2. Build per-frame state from the audit JSON (which shots have fired by
   time t, the most recent split, the running total since the beep).
3. Run-length encode those states (``overlay_single.build_overlay_runs``)
   and rasterize one document per run through headless Chromium -- ~31
   browser renders for a 30-shot stage instead of one PIL draw per frame.
4. Pipe raw RGBA bytes to ``ffmpeg -f rawvideo ... -c:v prores_ks
   -profile:v 4444 -pix_fmt yuva444p10le`` writing the final MOV, plus a
   ``drawtext`` running clock -- the one element that genuinely changes
   every frame and so can never be a run.

The renderer is pure of detection: the audit JSON is the source of truth.
Stages without a completed audit cannot render an overlay -- callers MUST
gate on that before invoking :func:`render_overlay`.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from PIL import Image, ImageFont

from .config import VideoMetadata
from .fcpxml_gen import probe_video
from .overlay_clock import clock_common_options, clock_text, elapsed_text_option
from .overlay_html import single_html
from .overlay_layout import Anchor, CellScale, anchor_ffmpeg_expr
from .overlay_raster import (
    INSTALL_HINT,
    ChromiumRasterizer,
    Rasterizer,
    RasterizerUnavailableError,
)
from .overlay_single import build_overlay_runs, run_groups
from .overlay_text import OverlayRenderError, overlay_font_file, resolve_overlay_face
from .overlay_theme import ThemeName, load_theme
from .runtime import Runner, ffmpeg_capabilities, quote_filter_value

logger = logging.getLogger(__name__)

OverlayCodec = Literal["auto", "hevc-alpha", "prores-4444"]
"""Pluggable encoder for the alpha overlay MOV.

- ``"auto"`` (default): ``hevc-alpha`` on macOS when ``hevc_videotoolbox``
  is advertised by the running ``ffmpeg``, otherwise ``prores-4444``.
  Picks the smallest file the host can produce without losing alpha.
- ``"hevc-alpha"``: Apple's HEVC with alpha via ``hevc_videotoolbox``.
  ~10-20x smaller than ProRes 4444 for mostly-transparent text overlays.
  macOS only; FCP imports it natively.
- ``"prores-4444"``: original behaviour. Cross-platform, large files
  (~330 Mbit/s @ 1080p24); use as the archival / non-Mac fallback.
"""

OVERLAY_CODECS: tuple[OverlayCodec, ...] = ("auto", "hevc-alpha", "prores-4444")

#: The glyph set every clock filter draws -- digits and a period, never a
#: descender or a taller mark. :func:`_clock_filter_graph` measures its
#: ascender-to-ink gap once to reconcile ``drawtext``'s origin with the
#: sprite's; using the literal the pre-beep filter already prints keeps
#: the measured string and the drawn string the same thing.
CLOCK_SAMPLE_TEXT = "0.00"


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


def _ffmpeg_supports_encoder(ffmpeg_binary: str, encoder: str) -> bool:
    """``True`` when ``ffmpeg -encoders`` advertises ``encoder``.

    Mirrors the probe in :mod:`splitsmith.trim` -- a runtime check beats
    hard-coding macOS-only encoders, since users can install ffmpeg
    builds without VideoToolbox.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return encoder in proc.stdout


def _resolve_codec(codec: OverlayCodec, ffmpeg_binary: str) -> Literal["hevc-alpha", "prores-4444"]:
    """Resolve ``"auto"`` against the host. Concrete codecs pass through.

    ``hevc-alpha`` only makes sense on Apple platforms with VideoToolbox;
    elsewhere we fall back to ``prores-4444`` rather than fail the export.
    Callers asking for a concrete codec get exactly that -- failures are
    surfaced by ffmpeg itself, not silently rewritten.
    """
    if codec == "hevc-alpha" or codec == "prores-4444":
        return codec
    if codec != "auto":
        raise OverlayRenderError(f"unknown overlay codec {codec!r}; expected one of {OVERLAY_CODECS}")
    if platform.system() == "Darwin" and _ffmpeg_supports_encoder(ffmpeg_binary, "hevc_videotoolbox"):
        return "hevc-alpha"
    return "prores-4444"


def _scaled_dimensions(width: int, height: int, max_height: int | None) -> tuple[int, int]:
    """Aspect-preserving downscale to ``max_height``. Both outputs even.

    Even dims keep yuv420 / yuv444 chroma alignment happy across encoders;
    odd dims trip ``hevc_videotoolbox`` on some macOS builds. We never
    upscale -- a cap above the source is a no-op.
    """
    if max_height is None or max_height >= height:
        return width, height
    new_h = max(2, max_height)
    new_w = max(2, int(round(width * (new_h / height))))
    if new_w % 2:
        new_w -= 1
    if new_h % 2:
        new_h -= 1
    return new_w, new_h


def _capped_frame_rate(num: int, den: int, max_fps: float | None) -> tuple[int, int]:
    """Cap source ``num/den`` at ``max_fps`` while keeping a rational rate.

    Returns a ``(numerator, denominator)`` pair the FCPXML can quote
    literally. Strategy: divide the source by the smallest integer factor
    that brings it under the cap, and prefer that candidate when it lands
    within 5% of the requested cap -- this is what makes NTSC sources do
    the right thing (60000/1001 capped at 30 -> 30000/1001 instead of
    flattening to 30/1). When the integer-divisor candidate would be far
    below the cap (e.g. 60 capped at 24 -> 20 fps), fall back to quoting
    the cap as a rational so the user gets what they asked for.
    """
    src = Fraction(num, den)
    if max_fps is None or float(src) <= max_fps + 1e-9:
        return num, den
    target = Fraction(max_fps).limit_denominator(1000)
    if target <= 0:
        raise OverlayRenderError(f"max_fps must be > 0, got {max_fps}")
    factor = src / target
    # Smallest integer ``k`` such that ``src / k <= target``. The 1e-9 nudge
    # absorbs float rounding when ``factor`` is exactly an integer.
    k = max(1, math.ceil(float(factor) - 1e-9))
    candidate = src / k
    if candidate >= target * Fraction(95, 100):
        return candidate.numerator, candidate.denominator
    return target.numerator, target.denominator


def _clock_filter_graph(
    *,
    width: int,
    height: int,
    scale: CellScale,
    font_path: Path,
    beep_offset_seconds: float,
    last_shot_in_clip: float,
    ink: tuple[int, int, int],
    stroke: tuple[int, int, int],
) -> str:
    """The running clock, as three mutually exclusive ``drawtext`` filters.

    The grid needs two -- a ticking window and a held final value. This
    path needs a third because it has always drawn ``0.00`` before the
    beep (``build_frame_states`` clamps ``running_total`` to zero there,
    and the PIL template drew it unconditionally), where the grid draws
    nothing until its beep. Keeping that costs one filter and keeps the
    clock consistent with the counter, which also reads ``0/M`` from
    frame zero.

    The windows are ``lt`` / ``gte`` rather than a ``between``, for the
    reason ``mp4_grid._clock_filters`` documents: ``between(t,a,b)`` and
    ``gte(t,b)`` are both true at exactly ``b``, and a frame landing
    there draws two numbers over each other. Verified for these three
    windows by rendering: at each boundary frame exactly one filter
    draws, and the composite is byte-identical to that filter alone.

    The clock freezes at the last shot rather than running on to the end
    of the clip -- the running total is the stage time, not the clip
    duration, which is what ``build_frame_states`` does in Python for the
    sprite half.

    **The y expression carries an ascender correction and must.**
    ``drawtext`` puts the top of the *drawn ink* at ``y``; CSS puts the
    top of the *line box* there and the ink starts an ascender-to-cap gap
    lower, and PIL's default ``"la"`` anchor -- what the pre-port
    :class:`DefaultTemplate` used for both corners -- does the same. So
    the same string at the same size in the same face lands in two
    different places. Measured on a real 1920x1080 frame, both corners
    drawing ``0.00``: the sprite's ink began at y=52 and the clock's at
    y=30. Adding the face's own gap to the clock is what puts them back
    on one baseline, and it is deliberately the *clock* that moves: 52 is
    where the PIL renderer drew both corners (verified against it at 720,
    1080 and 2160), so moving the sprite instead would fix the alignment
    by breaking issue #684's "the output must not move" rule.

    The gap is read off ``font_path`` rather than hard-coded as an em
    fraction, so it follows whatever face
    :func:`~splitsmith.overlay_text.resolve_overlay_face` actually
    resolved -- including a system fallback on a host with no bundled
    font. ffmpeg cannot compute it: ``drawtext``'s own ``ascent``
    variable is the maximum ink above the baseline **of the glyphs it is
    rendering**, so for a digits-only clock ``ascent - th`` is zero
    (measured: it moves the 1080 clock by 1px, not 22).
    """
    x_expr, y_expr = anchor_ffmpeg_expr(
        Anchor.TOP_RIGHT, col=0, row=0, cell_w=width, cell_h=height, pad=scale.pad
    )
    face = ImageFont.truetype(str(font_path), size=scale.live_primary)
    y_expr = f"{y_expr}+{face.getbbox(CLOCK_SAMPLE_TEXT)[1]}"
    common = clock_common_options(
        font_path=font_path,
        font_size=scale.live_primary,
        ink=ink,
        stroke=stroke,
        x_expr=x_expr,
        y_expr=y_expr,
    )
    start = f"{beep_offset_seconds:g}"
    freeze = f"{last_shot_in_clip:g}"
    held = quote_filter_value(clock_text(max(0.0, last_shot_in_clip - beep_offset_seconds)))
    return ",".join(
        (
            f"drawtext={common}:text='{CLOCK_SAMPLE_TEXT}':enable='lt(t\\,{start})'",
            f"drawtext={common}:{elapsed_text_option(start)}:" f"enable='gte(t\\,{start})*lt(t\\,{freeze})'",
            f"drawtext={common}:text={held}:enable='gte(t\\,{freeze})'",
        )
    )


def _discard_partial_output(output_path: Path) -> None:
    """Remove a half-written MOV so nothing downstream mistakes it for a render.

    ffmpeg opens its output with ``-y`` the moment it starts, so from the
    first piped frame there is always a file at ``output_path`` -- and a
    render that dies in the middle leaves a truncated one. ``ui/exports.py``
    treats an existing overlay file as a "stale render from a prior run"
    and wires it into the FCPXML rather than dropping it, so leaving the
    truncation behind is worse than leaving nothing: the user gets a
    timeline referencing a clip that stops partway through the stage.
    Whatever was there before this call is already gone either way; ``-y``
    truncated it before the first frame.
    """
    with contextlib.suppress(OSError):
        output_path.unlink(missing_ok=True)


@contextlib.contextmanager
def _rasterizer_for(supplied: Rasterizer | None) -> Iterator[Rasterizer]:
    """Yield a rasterizer, launching one only when the caller has none.

    A missing browser is a hard failure here, not a degradation. The
    grid can lose its sprites and still hand back an MP4 worth watching;
    this renderer's entire output is the sprites plus a clock, and a
    clock-only MOV is a file that looks like a success until it reaches
    the Final Cut timeline. ``ui/exports.py`` already turns
    ``OverlayRenderError`` into a visible skip reason.
    """
    if supplied is not None:
        yield supplied
        return
    owned = ChromiumRasterizer()
    try:
        active = owned.__enter__()
    except RasterizerUnavailableError as exc:
        raise OverlayRenderError(
            f"cannot render the overlay: {exc.detail} Install it with '{INSTALL_HINT}'."
        ) from exc
    try:
        yield active
    finally:
        owned.__exit__(None, None, None)


def _build_ffmpeg_cmd(
    *,
    ffmpeg_binary: str,
    codec: Literal["hevc-alpha", "prores-4444"],
    width: int,
    height: int,
    rate: str,
    output_path: Path,
    clock_filter: str | None = None,
) -> list[str]:
    """Encoder-specific argv. RGBA raw input is identical across codecs;
    only the output side differs."""
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
    ]
    if clock_filter is not None:
        cmd += ["-vf", clock_filter]
    if codec == "hevc-alpha":
        # ``alpha_quality`` ranges 0.0..1.0; 0.75 is a good legibility/size
        # tradeoff for sharp text on transparent. ``hvc1`` tag is what FCP
        # / QuickTime expect; the default ``hev1`` works in ffmpeg but is
        # rejected by some Apple importers. ``yuva420p`` is the only alpha
        # pixel format ``hevc_videotoolbox`` accepts.
        cmd += [
            "-c:v",
            "hevc_videotoolbox",
            "-allow_sw",
            "1",
            "-alpha_quality",
            "0.75",
            "-pix_fmt",
            "yuva420p",
            "-tag:v",
            "hvc1",
            "-r",
            rate,
            str(output_path),
        ]
    else:  # prores-4444
        cmd += [
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
    return cmd


def render_overlay(
    *,
    audit_path: Path,
    trimmed_video_path: Path,
    output_path: Path,
    beep_offset_seconds: float,
    ffmpeg_binary: str = "ffmpeg",
    probe: VideoMetadata | None = None,
    codec: OverlayCodec = "auto",
    max_height: int | None = None,
    max_fps: float | None = None,
    theme: ThemeName = "splitsmith",
    rasterizer: Rasterizer | None = None,
    probe_runner: Runner = subprocess.run,
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
    ``probe``: optional pre-computed metadata. When given, ``ffprobe`` is
        skipped -- useful from tests and to share one probe across the
        export's other steps.
    ``codec``: encoder preset; see :data:`OVERLAY_CODECS`. ``"auto"``
        produces the smallest file the host can write without losing alpha.
    ``max_height``: cap output height; aspect-preserving downscale. The
        FCPXML emits a separate format element so FCP scales it back up
        over the timeline.
    ``max_fps``: cap output frame rate. Source rate is preserved when it
        already fits under the cap.
    ``theme``: palette preset. ``"splitsmith"`` (default) pulls colors
        from the web UI's @theme tokens so the overlay matches the brand.
        ``"clean"`` is the neutral white-on-amber alternative.
    ``rasterizer``: injected :class:`~splitsmith.overlay_raster.Rasterizer`.
        Defaults to launching one headless Chromium for this call. A
        caller rendering several stages should supply one so the browser
        starts once (measured: 0.40 s of startup against 3.93 s of
        rasterizing for a 31-run stage). Without a usable browser this
        raises -- see :func:`_rasterizer_for`.
    ``probe_runner``: how the ffmpeg capability probe shells out, the same
        seam ``compare.mp4_grid.render_match_grid`` carries and for the
        same two reasons. It is deliberately not the encoder's own
        ``subprocess.Popen``: a unit test that stubs the encode has
        stubbed ``Popen`` module-wide, and ``subprocess.run`` opens one
        internally -- so without this the probe would be answered by the
        encoder's stub. And a fake here is the only way to exercise a
        build without ``drawtext``, which no ffmpeg on any machine in
        this project actually is.

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

    if shutil.which(ffmpeg_binary) is None:
        raise OverlayRenderError(f"ffmpeg binary not found: {ffmpeg_binary}")

    resolved_codec = _resolve_codec(codec, ffmpeg_binary)
    width, height = _scaled_dimensions(probe.width, probe.height, max_height)
    rate_num, rate_den = _capped_frame_rate(probe.frame_rate_num, probe.frame_rate_den, max_fps)
    fps = rate_num / rate_den
    duration_seconds = probe.duration_seconds

    scale = CellScale.for_cell(height)
    palette = load_theme(theme)

    states = build_frame_states(
        shot_times_in_clip=shot_times,
        beep_time_in_clip=beep_offset_seconds,
        fps=fps,
        duration_seconds=duration_seconds,
    )
    runs = build_overlay_runs(states)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rate = f"{rate_num}/{rate_den}"

    # ``drawtext`` opens the font file itself, long after this call, so
    # it has to be a real path that outlives the encode -- not a temp
    # file from ``importlib.resources.as_file``.
    with tempfile.TemporaryDirectory(prefix="splitsmith-overlay-") as work:
        # One bundled face for every theme. A theme decides colour, never
        # the typeface -- see ``compare.overlay_sprites.theme_font_face``
        # for the measurement behind that: only one of the overlay's two
        # halves could ever honour a per-theme face deterministically.
        font_path = overlay_font_file(resolve_overlay_face("splitsmith-mono"), Path(work))
        capabilities = ffmpeg_capabilities(ffmpeg_binary, font_path=font_path, runner=probe_runner)
        clock_filter: str | None = None
        if capabilities.drawtext:
            clock_filter = _clock_filter_graph(
                width=width,
                height=height,
                scale=scale,
                font_path=font_path,
                beep_offset_seconds=beep_offset_seconds,
                # Clamped, not just taken: an audit can carry a negative
                # ``ms_after_beep`` (a shot the auditor placed before the
                # beep), which would put ``freeze`` below ``start`` and
                # leave the pre-beep filter and the held filter BOTH
                # enabled over ``[freeze, start)`` -- two numbers stacked
                # at the same x/y, the exact failure the lt/gte windows
                # exist to prevent. Clamping collapses the ticking window
                # to nothing instead, which is right: a stage whose last
                # shot precedes its beep has no elapsed time to tick.
                last_shot_in_clip=max(beep_offset_seconds, max(shot_times)),
                ink=palette.ink,
                stroke=palette.stroke,
            )
        else:
            logger.warning(
                "%s (ffmpeg %s) has no usable drawtext, so the overlay's running clock is "
                "omitted; the shot counter and split labels still render.",
                capabilities.binary,
                capabilities.version,
            )

        cmd = _build_ffmpeg_cmd(
            ffmpeg_binary=ffmpeg_binary,
            codec=resolved_codec,
            width=width,
            height=height,
            rate=rate,
            output_path=output_path,
            clock_filter=clock_filter,
        )

        with _rasterizer_for(rasterizer) as active:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdin is not None
            try:
                for run in runs:
                    png = active.png(
                        single_html(
                            run_groups(run),
                            width=width,
                            height=height,
                            scale=scale,
                            theme=palette,
                        ),
                        width=width,
                        height=height,
                    )
                    # Decode once per run and write the same buffer for
                    # every frame it spans. The draw is the expensive
                    # part; the pipe is not, and repeating the buffer is
                    # what keeps the output frame-for-frame with the trim
                    # without a concat list to quantize.
                    frame = Image.open(io.BytesIO(png)).convert("RGBA").tobytes()
                    for _ in range(run.frame_count):
                        proc.stdin.write(frame)
                proc.stdin.close()
            except BaseException as exc:
                # Anything, not just the pipe's own BrokenPipeError/OSError.
                # ``active.png`` is a Playwright call: a browser timeout or
                # crash raises ``playwright.sync_api.Error``, which is
                # neither. Uncaught, the child is never killed and stdin is
                # never closed, so ffmpeg blocks on an open pipe until the
                # GC drops this Popen and then flushes a truncated MOV to
                # ``output_path`` -- and since the escaping exception is not
                # an OverlayRenderError, ``ui/exports.py`` does not catch it
                # either, so one bad stage aborts a whole multi-stage export
                # instead of becoming that stage's skip reason. Kill, reap,
                # and throw the fragment away before anything can find it.
                proc.kill()
                proc.wait()
                _discard_partial_output(output_path)
                if not isinstance(exc, Exception):
                    # Ctrl-C / SystemExit: the encoder is still owed a
                    # teardown, but the interrupt keeps unwinding as itself.
                    raise
                # Read stderr only after the wait above -- the pipe does not
                # reach EOF while the child is alive, so reading first on a
                # child blocked on stdin would hang here forever.
                stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                raise OverlayRenderError(f"ffmpeg failed during render: {stderr or exc}") from exc

            rc = proc.wait()
            stderr_text = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc != 0:
        _discard_partial_output(output_path)
        raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr_text}")
    return output_path
