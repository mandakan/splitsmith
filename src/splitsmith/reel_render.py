"""Rendering a highlight reel (spec 2026-10-18-highlight-reel-design).

Every item is one segment encoded like a stage (``mp4_render._encode_args``,
48 kHz stereo audio) so the stitch stream-copies the video. A clip is cut
from its source, centre-cropped to the canvas aspect, conformed to the
reel's rate, slowed by ``setpts`` and held on its last frame by ``tpad``.
SFX are lavfi sources in the argv, so the segment cache keys them and no
audio file ships.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .composition import SequenceFormat
from .config import VideoMetadata
from .look_motion import motion_overlay_filters
from .mp4_render import _encode_args, _rate_string
from .reel_plan import FrameRef, ReelClip, SfxKind

AUDIO_NORMALIZE = "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo"


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


def sfx_source(kind: SfxKind | None, seconds: float) -> str:
    """A lavfi source for ``kind``, stereo at 48 kHz, ``seconds`` long or
    padded to it; ``None`` is silence."""
    d = f"{seconds:g}"
    stereo = "pan=stereo|c0=c0|c1=c0"
    if kind == "beep":
        return f"sine=frequency=3000:sample_rate=48000:duration=0.3,volume=0.4,{stereo},apad=whole_dur={d}"
    if kind == "impact":
        return (
            "aevalsrc=exprs='0.8*sin(2*PI*(45+90*exp(-10*t))*t)*exp(-4*t)'"
            f":sample_rate=48000:channel_layout=stereo:duration={d}"
        )
    if kind == "riser":
        return (
            f"aevalsrc=exprs='0.3*sin(2*PI*(150*t+400*t*t/{d}))*(t/{d})'"
            f":sample_rate=48000:channel_layout=stereo:duration={d}"
        )
    if kind == "whoosh":
        half = f"{seconds / 2:g}"
        return (
            f"anoisesrc=color=pink:amplitude=0.3:sample_rate=48000:duration={d},highpass=f=400,"
            f"afade=t=in:d={half},afade=t=out:st={half}:d={half},{stereo}"
        )
    return "anullsrc=channel_layout=stereo:sample_rate=48000"


def clip_command(
    clip: ReelClip,
    *,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    source_audio: bool = True,
) -> tuple[str, ...]:
    """One clip. The source's own audio only at real speed with no SFX, cut
    from an unseeked second read by ``atrim`` (``_build_stage_command``
    explains why input ``-ss`` is not trusted for audio)."""
    out_seconds = clip.out_seconds
    rate = _rate_string(sequence)
    freeze = f",tpad=stop_mode=clone:stop_duration={clip.freeze_seconds:g}" if clip.freeze_seconds > 0 else ""
    video = (
        f"[0:v]{_fit(sequence)},setpts=(PTS-STARTPTS)/{clip.speed:g},fps={rate}{freeze},format=yuv420p[final]"
    )
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    args += ["-ss", f"{clip.start:g}", "-t", f"{clip.seconds:g}", "-i", str(clip.source)]
    if source_audio and clip.speed == 1.0 and clip.sfx is None:
        args += ["-i", str(clip.source)]
        audio = (
            f"[1:a]atrim=start={clip.start:g}:duration={clip.seconds:g},asetpts=PTS-STARTPTS,"
            f"{AUDIO_NORMALIZE},apad=whole_dur={out_seconds:g}[aout]"
        )
    else:
        args += ["-f", "lavfi", "-i", sfx_source(clip.sfx, out_seconds)]
        audio = f"[1:a]{AUDIO_NORMALIZE},atrim=duration={out_seconds:g}[aout]"
    args += ["-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += ["-t", f"{out_seconds:g}", *_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


def card_command(
    backdrop_png: Path,
    clip: Path | None,
    *,
    seconds: float,
    sfx: SfxKind | None,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """A card held ``seconds`` over its backdrop: a canvas-sized PNG (the
    composed still, or the backdrop under ``clip``, the template's alpha
    MOV, last frame held), with its SFX."""
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
    args += ["-f", "lavfi", "-i", sfx_source(sfx, seconds)]
    audio = f"[{audio_index}:a]{AUDIO_NORMALIZE}[aout]"
    args += ["-t", f"{seconds:g}", "-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += [*_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


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
