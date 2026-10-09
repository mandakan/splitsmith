"""The template HUD path of the overlay MOV (spec 2026-10-08).

``overlay_render.render_overlay`` calls :func:`render_hud_overlay` when a
template variant is asked for. The MOV is what Classic writes (same size,
rate, codec and alpha) so the MP4 compositing and the FCPXML never know
which path drew it. The difference is who draws: here the template draws
everything, clock included, one browser frame per live output frame
(``overlay_hud.hud_frame_plan``).

Any failure of the template's own (a script error, a timeout, a crashed
page) is a :class:`HudFallbackError`: the caller draws Classic and says why. A
failure that would sink Classic too (ffmpeg missing or failing) stays an
``OverlayRenderError``.

The cache is the render segment cache. The key is the encode argv with
its stdin standing for the template digest, so a hit is found before the
browser renders a frame, and a hit leaves an identical output file alone:
the MP4 segment cache keys on the overlay file's mtime.
"""

from __future__ import annotations

import filecmp
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from .audit_data import read_audit_data
from .config import StageEvent
from .events import confirmed_from_doc
from .look_template import TemplateContext, engine_block, shared_url, template_digest, theme_tokens
from .overlay_html import single_css
from .overlay_hud import (
    HUD_KEY_VERSION,
    HudFrame,
    HudOptions,
    declared_positions,
    hud_frame_plan,
    hud_options_data,
    hud_page_size,
    hud_stage_data,
    resolve_position,
)
from .overlay_layout import CellScale
from .overlay_raster import Rasterizer, TemplateScriptError
from .overlay_text import OverlayRenderError
from .overlay_theme import OverlayTheme
from .segment_cache import SegmentCache
from .stage_summary_data import load_stage_shots

logger = logging.getLogger(__name__)

_CACHEABLE_SUFFIXES = (".mov", ".mp4")


class HudFallbackError(Exception):
    """The template could not draw this stage; draw Classic instead."""


def _confirmed_regions(audit_path: Path) -> list[StageEvent]:
    """The stage's confirmed regions. A corrupt events list must not fail a
    render (one bad doc, a 12-stage export): it draws with no regions, as
    the Coach GET tolerates a legacy doc. Thin path wrapper over
    ``events.confirmed_from_doc``, the shared tolerant reader."""
    return confirmed_from_doc(read_audit_data(audit_path), log_context=audit_path.name)


def hud_context(
    *,
    stage: dict[str, Any],
    options: dict[str, Any],
    theme: OverlayTheme,
    width: int,
    height: int,
    fps: float,
) -> TemplateContext:
    """``window.splitsmith`` for a HUD: the stage and the options as data,
    the theme's tokens, and the engine stylesheet for its font faces."""
    return TemplateContext(
        theme=theme_tokens(theme),
        data={"stage": stage, "options": options},
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(
            css=single_css(width=width, height=height, scale=CellScale.for_cell(height), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def _install(source: Path, output_path: Path) -> None:
    """Put a cached MOV at ``output_path`` unless it is already there byte
    for byte (an untouched file keeps its mtime, and with it the MP4
    segment key)."""
    if output_path.exists() and filecmp.cmp(source, output_path, shallow=False):
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f".{output_path.name}.part")
    shutil.copyfile(source, partial)
    partial.replace(output_path)


def render_hud_overlay(
    *,
    template: Path,
    audit_path: Path,
    output_path: Path,
    beep_offset_seconds: float,
    duration_seconds: float,
    width: int,
    height: int,
    rate_num: int,
    rate_den: int,
    codec: Literal["hevc-alpha", "prores-4444"],
    ffmpeg_binary: str,
    theme: OverlayTheme,
    options: HudOptions,
    rasterizer: Rasterizer,
    segment_cache: SegmentCache | None,
) -> Path:
    """Render the stage's HUD through ``template`` into ``output_path``."""
    # Deferred: overlay_render imports this module, and the encoder argv
    # and the partial-output rule are its own, shared by both paths.
    from .overlay_render import _build_ffmpeg_cmd, _discard_partial_output

    shots = load_stage_shots(audit_path)
    if not shots:
        raise HudFallbackError("no readable shots in the audit")
    fps = rate_num / rate_den
    frame_count = max(0, int(round(duration_seconds * fps)))
    page_width, page_height = hud_page_size(width, height)
    stage = hud_stage_data(shots, beep_in_clip=beep_offset_seconds, events=_confirmed_regions(audit_path))
    position = resolve_position(options.position, declared_positions(template))
    context = hud_context(
        stage=stage,
        options=hud_options_data(options, position),
        theme=theme,
        width=page_width,
        height=page_height,
        fps=fps,
    )
    scale = None if (page_width, page_height) == (width, height) else f"scale={width}:{height}:flags=lanczos"
    rate = f"{rate_num}/{rate_den}"
    use_cache = segment_cache is not None and output_path.suffix in _CACHEABLE_SUFFIXES

    with tempfile.TemporaryDirectory(prefix="splitsmith-hud-") as work:
        key: str | None = None
        target = output_path
        if use_cache:
            assert segment_cache is not None
            probe_argv = _build_ffmpeg_cmd(
                ffmpeg_binary=ffmpeg_binary,
                codec=codec,
                width=page_width,
                height=page_height,
                rate=rate,
                output_path=output_path,
                clock_filter=scale,
            )
            digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
            key = segment_cache.key(
                tuple(probe_argv),
                output_path=output_path,
                work_dir=Path(work),
                virtual_inputs={"-": f"hud-v{HUD_KEY_VERSION}:{digest}:frames={frame_count}"},
            )
            hit = segment_cache.lookup(key, suffix=output_path.suffix)
            if hit is not None:
                _install(hit, output_path)
                return output_path
            target = segment_cache.partial_path(key, suffix=output_path.suffix)

        cmd = _build_ffmpeg_cmd(
            ffmpeg_binary=ffmpeg_binary,
            codec=codec,
            width=page_width,
            height=page_height,
            rate=rate,
            output_path=target,
            clock_filter=scale,
        )
        plans: list[tuple[HudFrame, ...]] = []

        def plan(settle: float) -> list[float]:
            frames = hud_frame_plan(
                frame_count=frame_count,
                fps=fps,
                beep=beep_offset_seconds,
                last_shot=stage["shots"][-1]["t"],
                settle=settle,
            )
            plans.append(frames)
            return [frame.seek for frame in frames]

        try:
            rendered = rasterizer.render_template_timeline(
                template, context=context, width=page_width, height=page_height, plan=plan
            )
        except TemplateScriptError as exc:
            raise HudFallbackError(str(exc)) from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None
        piped = False
        try:
            for buffer, frame in zip(rendered.frames, plans[0], strict=True):
                for _ in range(frame.count):
                    proc.stdin.write(buffer)
                    piped = True
            proc.stdin.close()
        except BaseException as exc:
            # Kill and reap before reading stderr (see overlay_render's
            # Classic loop for why the order matters), and throw away what
            # was written: a truncated MOV must never reach the timeline.
            proc.kill()
            proc.wait()
            _discard_partial_output(target, piped_frames=piped)
            if isinstance(exc, TemplateScriptError):
                raise HudFallbackError(str(exc)) from exc
            if not isinstance(exc, Exception):
                raise
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise OverlayRenderError(f"ffmpeg failed during render: {stderr or exc}") from exc
        finally:
            rendered.close()
        rc = proc.wait()
        stderr_text = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if rc != 0:
            _discard_partial_output(target, piped_frames=piped)
            raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr_text}")

        if use_cache:
            assert segment_cache is not None and key is not None
            final = segment_cache.commit(target, key, suffix=output_path.suffix)
            segment_cache.evict(keep={key})
            _install(final, output_path)
    return output_path


__all__ = ["HudFallbackError", "hud_context", "render_hud_overlay"]
