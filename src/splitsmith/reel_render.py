"""Rendering a highlight reel (spec 2026-10-18-highlight-reel-design).

Every item is one segment encoded like a stage (``mp4_render._encode_args``,
48 kHz stereo audio) so the stitch stream-copies the video. A clip is cut
from its source, centre-cropped to the canvas aspect, conformed to the
reel's rate, slowed by ``setpts`` and held on its last frame by ``tpad``.
SFX are lavfi sources in the argv, so the segment cache keys them and no
audio file ships.
"""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from PIL import Image

from .composition import SequenceFormat
from .config import VideoMetadata
from .look_motion import motion_overlay_filters, write_motion_clip
from .looks import load_look
from .mp4_render import (
    FFmpegError,
    Runner,
    _build_concat_command,
    _encode_args,
    _has_audio_stream,
    _rate_string,
    _run,
)
from .overlay_card import card_backdrop, compose_card, first_frame_image
from .overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .reel_cards import reel_card_motion
from .reel_plan import FrameRef, ReelCard, ReelClip, ReelItem, SfxKind
from .segment_cache import SegmentCache

logger = logging.getLogger(__name__)

AUDIO_NORMALIZE = "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo"
#: How far a callout's frozen frame is dimmed under its text.
CALLOUT_DIM = 0.35


def reel_format(primaries: Sequence[VideoMetadata], *, upright: bool) -> SequenceFormat:
    """The reel's canvas: 1080 lines on the short side, 720 when every
    primary is 720p or less (the hosted web rendition), at the most common
    primary rate."""
    rates = Counter((m.frame_rate_num, m.frame_rate_den) for m in primaries) or Counter({(30, 1): 1})
    (num, den), _ = rates.most_common(1)[0]
    short = 720 if primaries and all(min(m.width, m.height) <= 720 for m in primaries) else 1080
    long = short * 16 // 9
    width, height = (short, long) if upright else (long, short)
    return SequenceFormat(width, height, num, den)


def _fit(sequence: SequenceFormat) -> str:
    """Centre-crop to the canvas aspect, then scale to it."""
    aspect = sequence.width / sequence.height
    return (
        f"crop='min(iw,ih*{aspect:.4g})':'min(ih,iw/{aspect:.4g})',"
        f"scale={sequence.width}:{sequence.height},setsar=1"
    )


SILENCE = "anullsrc=channel_layout=stereo:sample_rate=48000"

_SFX_DIR = Path(str(resources.files("splitsmith") / "data" / "sfx"))
#: Each stem (``data/sfx``, sources in its ``LICENSES.md``) and where its hit
#: sits in it: the mix starts the stem that much before its cue, so the hit
#: lands on the cut (a riser's "hit" is its last sample, a sting's after its
#: build). Volumes are relative to the footage's own sound.
SFX_STEMS: dict[SfxKind, tuple[Path, float]] = {
    "impact": (_SFX_DIR / "impact.m4a", 1.2),
    "braam": (_SFX_DIR / "braam.m4a", 1.2),
    "whoosh": (_SFX_DIR / "whoosh.m4a", 1.0),
    "slowmo": (_SFX_DIR / "slowmo.m4a", 0.0),
    "riser": (_SFX_DIR / "riser.m4a", 2.2),
    "sting": (_SFX_DIR / "sting.m4a", 6.2),
}
SFX_VOLUME = 0.7


def beep_source(seconds: float) -> str:
    """The timer's beep, a 3 kHz tone, padded to ``seconds``."""
    return (
        "sine=frequency=3000:sample_rate=48000:duration=0.3,volume=0.4,"
        f"pan=stereo|c0=c0|c1=c0,apad=whole_dur={seconds:g}"
    )


def clip_command(
    clip: ReelClip,
    *,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    source_audio: bool = True,
) -> tuple[str, ...]:
    """One clip. The source's own audio at real speed, cut from an unseeked
    second read by ``atrim`` (``_build_stage_command`` explains why input
    ``-ss`` is not trusted for audio); a slowed clip is silent, its stem
    is laid over the stitched reel."""
    out_seconds = clip.out_seconds
    rate = _rate_string(sequence)
    freeze = f",tpad=stop_mode=clone:stop_duration={clip.freeze_seconds:g}" if clip.freeze_seconds > 0 else ""
    video = (
        f"[0:v]{_fit(sequence)},setpts=(PTS-STARTPTS)/{clip.speed:g},fps={rate}{freeze},format=yuv420p[final]"
    )
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    args += ["-ss", f"{clip.start:g}", "-t", f"{clip.seconds:g}", "-i", str(clip.source)]
    if source_audio and clip.speed == 1.0:
        args += ["-i", str(clip.source)]
        audio = (
            f"[1:a]atrim=start={clip.start:g}:duration={clip.seconds:g},asetpts=PTS-STARTPTS,"
            f"{AUDIO_NORMALIZE},apad=whole_dur={out_seconds:g}[aout]"
        )
    else:
        args += ["-f", "lavfi", "-i", SILENCE]
        audio = f"[1:a]{AUDIO_NORMALIZE},atrim=duration={out_seconds:g}[aout]"
    args += ["-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += ["-t", f"{out_seconds:g}", *_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


def card_command(
    backdrop_png: Path,
    clip: Path | None,
    *,
    seconds: float,
    sfx: tuple[SfxKind, ...],
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """A card held ``seconds`` over its backdrop: a canvas-sized PNG (the
    composed still, or the backdrop under ``clip``, the template's alpha
    MOV, last frame held). Silent but for the timer's beep; its stems are
    laid over the stitched reel."""
    rate = _rate_string(sequence)
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y", "-loop", "1", "-framerate", rate]
    args += ["-i", str(backdrop_png)]
    if clip is not None:
        args += ["-i", str(clip)]
        parts, label = motion_overlay_filters(1, rate=rate, seconds=seconds, source_label="0:v")
        video = ";".join([*parts, f"[{label}]format=yuv420p,setsar=1[final]"])
        audio_index = 2
    else:
        video = "[0:v]format=yuv420p,setsar=1[final]"
        audio_index = 1
    args += ["-f", "lavfi", "-i", beep_source(seconds) if "beep" in sfx else SILENCE]
    audio = f"[{audio_index}:a]{AUDIO_NORMALIZE}[aout]"
    args += ["-t", f"{seconds:g}", "-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += [*_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


def sfx_cues(items: Sequence[ReelItem]) -> tuple[tuple[SfxKind, float], ...]:
    """Every stem cue as (kind, seconds into the stitched reel): each item's
    stems at the item's start. The beep is not a stem; its card draws it."""
    cues: list[tuple[SfxKind, float]] = []
    at = 0.0
    for item in items:
        cues.extend((kind, round(at, 6)) for kind in item.sfx if kind in SFX_STEMS)
        at += item.out_seconds if isinstance(item, ReelClip) else item.seconds
    return tuple(cues)


def sfx_mix_command(
    stitched: Path,
    cues: Sequence[tuple[SfxKind, float]],
    *,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...] | None:
    """Lay every cue's stem over the stitched reel, its hit on the cue, with
    its whole tail; the video is copied. ``None`` when there is no cue."""
    if not cues:
        return None
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y", "-i", str(stitched)]
    parts: list[str] = []
    labels = ["[0:a]"]
    for index, (kind, at) in enumerate(cues, start=1):
        path, hit = SFX_STEMS[kind]
        args += ["-i", str(path)]
        start = at - hit
        if start >= 0:
            ms = round(start * 1000)
            chain = f"[{index}:a]adelay={ms}|{ms}"
        else:
            chain = f"[{index}:a]atrim=start={-start:g},asetpts=PTS-STARTPTS"
        parts.append(f"{chain},{AUDIO_NORMALIZE},volume={SFX_VOLUME:g}[s{index}]")
        labels.append(f"[s{index}]")
    parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,alimiter=limit=0.9[aout]"
    )
    args += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]"]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
    return tuple(args)


@dataclass(frozen=True)
class ReelRenderResult:
    output_path: Path
    duration_seconds: float
    degradations: tuple[str, ...]


def render_reel(
    items: Sequence[ReelItem],
    *,
    sequence: SequenceFormat,
    output_path: Path,
    work_dir: Path,
    look_name: str = "splitsmith",
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    rasterizer: Rasterizer | None = None,
    segment_cache: SegmentCache | None = None,
) -> ReelRenderResult:
    """Encode every item as a segment (reused from ``segment_cache`` when
    its argv and inputs are unchanged) and stitch them. No browser draws
    every card as its backdrop alone and records a degradation; the reel
    never fails for a card."""
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    degradations: list[str] = []
    owned: ChromiumRasterizer | None = None
    active = rasterizer
    if active is None and any(isinstance(i, ReelCard) for i in items):
        owned = ChromiumRasterizer()
        try:
            active = owned.__enter__()
        except RasterizerUnavailableError as exc:
            owned, active = None, None
            degradations.append(f"reel cards drawn without text: {exc.detail}")
            logger.warning("%s", degradations[-1])
    look = load_look(look_name)
    fps = sequence.frame_rate_num / sequence.frame_rate_den
    used: set[str] = set()

    def encode(
        cmd: tuple[str, ...],
        out: Path,
        *,
        virtual: dict[str, str] | None = None,
        prepare: Callable[[], object] | None = None,
    ) -> Path:
        if segment_cache is None:
            if prepare is not None:
                prepare()
            _run(cmd, runner=runner)
            return out
        key = segment_cache.key(cmd, output_path=out, work_dir=work_dir, virtual_inputs=virtual)
        used.add(key)
        hit = segment_cache.lookup(key)
        if hit is not None:
            return hit
        if prepare is not None:
            prepare()
        partial = segment_cache.partial_path(key)
        try:
            _run(tuple(str(partial) if token == str(out) else token for token in cmd), runner=runner)
            return segment_cache.commit(partial, key)
        finally:
            partial.unlink(missing_ok=True)

    def backdrop_for(card: ReelCard) -> Image.Image:
        if card.backdrop is None:
            return Image.new("RGB", (sequence.width, sequence.height), "black")
        frame = work_dir / f"{card.name}_frame.png"
        try:
            _run(
                frame_command(
                    card.backdrop, sequence=sequence, output_path=frame, ffmpeg_binary=ffmpeg_binary
                ),
                runner=runner,
            )
        except FFmpegError as exc:
            logger.warning("highlight reel: frame grab for %s failed (%s)", card.name, exc)
        if not frame.is_file():
            # A seek at or past the clip's end writes nothing: the callout
            # holds on black rather than failing the reel.
            degradations.append(f"{card.name}: no frame at {card.backdrop.at:g} s, held on black")
            return Image.new("RGB", (sequence.width, sequence.height), "black")
        return card_backdrop(
            frame, width=sequence.width, height=sequence.height, look=look, blur_radius=0, dim=CALLOUT_DIM
        )

    segments: list[tuple[Path, float]] = []
    try:
        for item in items:
            out = work_dir / f"{item.name}.mp4"
            if isinstance(item, ReelClip):
                cmd = clip_command(
                    item,
                    sequence=sequence,
                    output_path=out,
                    ffmpeg_binary=ffmpeg_binary,
                    source_audio=_has_audio_stream(item.source),
                )
                segments.append((encode(cmd, out), item.out_seconds))
                continue
            canvas = backdrop_for(item)
            png = work_dir / f"{item.name}_backdrop.png"
            motion = None
            if active is not None:
                motion = reel_card_motion(
                    look, item, width=sequence.width, height=sequence.height, fps=fps, rasterizer=active
                )
                if motion is None:
                    degradations.append(f"{item.name}: the highlight card failed to load")
            if motion is not None and motion.animated:
                canvas.save(png)
                clip_path = work_dir / f"{item.name}_motion.mov"

                def write(frames=motion.frames, clip_path=clip_path) -> None:
                    if not clip_path.exists():
                        write_motion_clip(frames, out=clip_path, fps=fps, ffmpeg_binary=ffmpeg_binary)

                cmd = card_command(
                    png,
                    clip_path,
                    seconds=item.seconds,
                    sfx=item.sfx,
                    sequence=sequence,
                    output_path=out,
                    ffmpeg_binary=ffmpeg_binary,
                )
                try:
                    path = encode(cmd, out, virtual={str(clip_path): motion.digest}, prepare=write)
                finally:
                    motion.close()
                segments.append((path, item.seconds))
                continue
            if motion is not None:
                text = first_frame_image(motion)
                if text is not None:
                    canvas = compose_card(text, canvas)
            canvas.save(png)
            cmd = card_command(
                png,
                None,
                seconds=item.seconds,
                sfx=item.sfx,
                sequence=sequence,
                output_path=out,
                ffmpeg_binary=ffmpeg_binary,
            )
            segments.append((encode(cmd, out), item.seconds))
    finally:
        if owned is not None:
            owned.__exit__(None, None, None)

    listing = work_dir / "segments.txt"
    listing.write_text("".join(f"file '{path}'\n" for path, _ in segments))
    mix = sfx_mix_command(
        work_dir / "stitched.mp4", sfx_cues(items), output_path=output_path, ffmpeg_binary=ffmpeg_binary
    )
    stitched = output_path if mix is None else work_dir / "stitched.mp4"
    _run(
        _build_concat_command(
            list_path=listing, output_path=stitched, ffmpeg_binary=ffmpeg_binary, reencode_audio=True
        ),
        runner=runner,
    )
    if mix is not None:
        _run(mix, runner=runner)
    if segment_cache is not None:
        segment_cache.evict(keep=used)
    return ReelRenderResult(output_path, sum(seconds for _, seconds in segments), tuple(degradations))


def frame_command(
    ref: FrameRef, *, sequence: SequenceFormat, output_path: Path, ffmpeg_binary: str = "ffmpeg"
) -> tuple[str, ...]:
    """One frame of ``ref`` cropped and scaled to the canvas, as a PNG."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-ss",
        f"{ref.at:g}",
        "-i",
        str(ref.source),
        "-frames:v",
        "1",
        "-vf",
        _fit(sequence),
        str(output_path),
    )
