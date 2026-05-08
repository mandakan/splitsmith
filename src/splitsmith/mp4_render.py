"""MP4 renderer for the Composition IR via ffmpeg (issue #174).

Walks ``composition.Composition`` and produces a stitched MP4 by:

1. Building one ffmpeg invocation per stage that re-encodes the primary
   with PiP secondaries and the alpha overlay composited in. Each stage
   writes to a temp ``.mp4`` in the work directory.
2. Concatenating the per-stage temps via the ``concat`` demuxer with
   ``-c copy`` so the final stitch doesn't re-encode.

Coverage in this PR matches the FCPXML / FCP7 renderers for the
in-scope features:

- Primary clip per stage on the base layer.
- Secondary cams composited with optional PiP transform (scale +
  position). The IR's pixel-space ``Transform`` (+Y up, sequence-centre
  origin) converts to ffmpeg's ``overlay`` filter convention (+Y down,
  top-left origin).
- Alpha overlay composited on the topmost layer.
- Per-stage trim via ``-ss`` / ``-t`` on the input.

Out of scope for this PR (sibling issues): transitions (#195), title
cards (#196), intro / outro segments (#173), audio mix tweaks,
codec-tuned presets such as the YouTube preset (#204). Audio is taken
from the primary; secondaries / overlay contribute video only.

The IR is the contract: ``render_mp4`` is the second non-XML renderer
that consumes it (FCPXML, FCP7 XML, MP4 -- three targets, one IR).

Determinism / testability: command construction is split into pure
functions (``_build_stage_command`` / ``_build_concat_command``) so
unit tests can verify the ffmpeg invocation without shelling out. The
runner is injectable, mirroring the pattern in
``splitsmith.trim``.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .composition import Composition, ConnectedClip, Stage, Transform

Runner = Callable[..., subprocess.CompletedProcess]


class FFmpegError(RuntimeError):
    """ffmpeg exited non-zero or could not be invoked."""


def render_mp4(
    composition: Composition,
    *,
    output_path: Path,
    work_dir: Path | None = None,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    youtube_preset: bool = False,
) -> None:
    """Render ``composition`` as a stitched ``.mp4`` at ``output_path``.

    ``work_dir`` is where per-stage temps live; defaults to a fresh
    ``TemporaryDirectory`` cleaned up on return. Pass an explicit path
    when debugging; the per-stage MP4s are easier to inspect than the
    final concat output.

    ``youtube_preset`` swaps the default per-stage encode for YouTube's
    recommended H.264 profile / GOP / colour tags (issue #204 layer 2).
    The concat step keeps stream-copy in either case.
    """
    plans = [_plan_stage(stage, composition.sequence) for stage in composition.stages]
    if not plans:
        raise ValueError("render_mp4 requires at least one stage")

    # The concat-demuxer pipeline (stream-copy) requires every per-stage
    # temp share the same frame rate as the sequence. The FCPXML / FCP7
    # path lifted this restriction in #233 because their NLE readers
    # conform per-asset rates; the MP4 path can't conform without
    # re-encoding through an ``fps=`` filter, which is a larger
    # change than this issue's scope. Surface a clear error naming the
    # offenders so the user knows whether to switch renderer or
    # convert sources externally.
    seq = composition.sequence
    mismatched: list[str] = []
    for stage in composition.stages:
        meta = stage.primary.metadata
        if meta.frame_rate_num != seq.frame_rate_num or meta.frame_rate_den != seq.frame_rate_den:
            mismatched.append(f"{stage.name} ({meta.frame_rate_num}/{meta.frame_rate_den})")
    if mismatched:
        raise ValueError(
            f"mp4 renderer requires all stages at the timeline frame rate "
            f"({seq.frame_rate_num}/{seq.frame_rate_den}); these stages differ: "
            + ", ".join(mismatched)
            + ". Switch to the FCPXML or FCP7 renderer (which conform "
            "per-asset rates) or convert the sources to a shared rate first."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="splitsmith-mp4-") as tmp:
            _render_with_work_dir(
                composition,
                plans,
                output_path=output_path,
                work_dir=Path(tmp),
                ffmpeg_binary=ffmpeg_binary,
                runner=runner,
                youtube_preset=youtube_preset,
            )
    else:
        work_dir.mkdir(parents=True, exist_ok=True)
        _render_with_work_dir(
            composition,
            plans,
            output_path=output_path,
            work_dir=work_dir,
            ffmpeg_binary=ffmpeg_binary,
            runner=runner,
            youtube_preset=youtube_preset,
        )


def _render_with_work_dir(
    composition: Composition,
    plans: list[_StagePlan],
    *,
    output_path: Path,
    work_dir: Path,
    ffmpeg_binary: str,
    runner: Runner,
    youtube_preset: bool = False,
) -> None:
    stage_files: list[Path] = []
    for idx, plan in enumerate(plans):
        stage_out = work_dir / f"stage_{idx:03d}.mp4"
        cmd = _build_stage_command(
            plan,
            sequence=composition.sequence,
            output_path=stage_out,
            ffmpeg_binary=ffmpeg_binary,
            youtube_preset=youtube_preset,
        )
        _run(cmd, runner=runner)
        stage_files.append(stage_out)

    list_path = work_dir / "concat.txt"
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in stage_files),
        encoding="utf-8",
    )
    cmd = _build_concat_command(
        list_path=list_path,
        output_path=output_path,
        ffmpeg_binary=ffmpeg_binary,
    )
    _run(cmd, runner=runner)


def _run(cmd: tuple[str, ...], *, runner: Runner) -> None:
    try:
        runner(list(cmd), check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg binary not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(
            f"ffmpeg failed (exit {exc.returncode}): "
            f"{(exc.stderr or exc.stdout or '').strip()[-2000:] or '(no output)'}"
        ) from exc


# --- planning -------------------------------------------------------------


@dataclass(frozen=True)
class _StagePlan:
    """Per-stage timing + alignment derived from the IR.

    ``head_trim_seconds`` is how far we ``-ss`` into the primary;
    ``effective_seconds`` is how long the stage runs on the spine.
    ``cam_alignments`` carries each cam's seek-into-source plus the
    spine time the cam should appear, mirroring the head-slip math the
    FCPXML / FCP7 renderers do in frames.
    """

    stage: Stage
    head_trim_seconds: float
    effective_seconds: float
    cam_alignments: tuple[_CamAlignment, ...]


@dataclass(frozen=True)
class _CamAlignment:
    cam: ConnectedClip
    cam_seek_seconds: float  # ``-ss`` value for the cam input
    cam_spine_start: float  # spine time when the cam first appears
    cam_visible_seconds: float  # how long the cam shows on the spine


def _plan_stage(stage: Stage, sequence_format) -> _StagePlan:  # type: ignore[no-untyped-def]
    duration = stage.primary.metadata.duration_seconds
    head_avail = max(0.0, stage.beep_offset_seconds)
    if stage.markers:
        last_local = max(m.time_seconds for m in stage.markers)
    else:
        last_local = stage.beep_offset_seconds
    tail_avail = max(0.0, duration - last_local)
    head_trim_seconds = max(0.0, head_avail - stage.head_pad_seconds)
    tail_trim_seconds = max(0.0, tail_avail - stage.tail_pad_seconds)
    effective_seconds = duration - head_trim_seconds - tail_trim_seconds
    if effective_seconds <= 0:
        raise ValueError(
            f"stage {stage.name!r} would have non-positive effective duration "
            f"after trim ({effective_seconds:.3f}s); reduce head/tail pad"
        )

    cam_alignments: list[_CamAlignment] = []
    visible_head = head_trim_seconds  # source time of the visible head in the primary
    for sec in stage.secondaries:
        assert sec.beep_offset_seconds is not None  # cam role
        # Same head-slip math as the FCPXML emitter, expressed in seconds.
        delta = (stage.beep_offset_seconds - visible_head) - sec.beep_offset_seconds
        if delta >= 0:
            seek = 0.0
            spine_start = delta
        else:
            seek = -delta
            spine_start = 0.0
        cam_total = sec.asset.metadata.duration_seconds
        # The cam shows from spine_start until either (a) its own media
        # runs out or (b) the stage ends.
        cam_visible = min(cam_total - seek, effective_seconds - spine_start)
        cam_alignments.append(
            _CamAlignment(
                cam=sec,
                cam_seek_seconds=seek,
                cam_spine_start=spine_start,
                cam_visible_seconds=max(0.0, cam_visible),
            )
        )

    return _StagePlan(
        stage=stage,
        head_trim_seconds=head_trim_seconds,
        effective_seconds=effective_seconds,
        cam_alignments=tuple(cam_alignments),
    )


# --- command construction -------------------------------------------------


def _build_stage_command(
    plan: _StagePlan,
    *,
    sequence,  # type: ignore[no-untyped-def]
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    youtube_preset: bool = False,
) -> tuple[str, ...]:
    """Build the ffmpeg invocation that renders one stage to ``output_path``.

    The invocation re-encodes -- there's no general way to bake
    overlays + PiP without re-encoding. For the non-composited subset
    of cases (no cams, no overlay) we still re-encode for simplicity;
    a stream-copy fast-path is a follow-up if encode time becomes a
    problem.

    ``youtube_preset`` swaps the encode params (codec, GOP, colour
    tags, audio bitrate) to YouTube's recommended profile.
    """
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    stage = plan.stage

    # Primary input: trim with ``-ss``/``-t`` before ``-i`` for fast
    # seeking. The buffer in the trimmed clip absorbs any seek
    # imprecision; same trade-off as ``trim.py`` makes.
    args += [
        "-ss",
        f"{plan.head_trim_seconds:g}",
        "-t",
        f"{plan.effective_seconds:g}",
        "-i",
        str(stage.primary.path),
    ]

    # Secondary cam inputs.
    for align in plan.cam_alignments:
        args += [
            "-ss",
            f"{align.cam_seek_seconds:g}",
            "-t",
            f"{align.cam_visible_seconds:g}",
            "-i",
            str(align.cam.asset.path),
        ]

    # Overlay input (if any). Same head-trim window as the primary --
    # the overlay was rendered to mirror the primary frame-for-frame.
    overlay_index: int | None = None
    if stage.overlay is not None:
        overlay_index = 1 + len(plan.cam_alignments)
        args += [
            "-ss",
            f"{plan.head_trim_seconds:g}",
            "-t",
            f"{plan.effective_seconds:g}",
            "-i",
            str(stage.overlay.asset.path),
        ]

    filter_graph = _build_stage_filter_graph(
        plan,
        sequence=sequence,
        overlay_input_index=overlay_index,
    )

    args += [
        "-filter_complex",
        filter_graph,
        "-map",
        "[final]",
        "-map",
        "0:a?",  # primary audio when present, no error if absent
    ]
    args += list(_encode_args(sequence, youtube_preset=youtube_preset))
    args += [str(output_path)]
    return tuple(args)


def _encode_args(
    sequence,  # type: ignore[no-untyped-def]
    *,
    youtube_preset: bool,
) -> tuple[str, ...]:
    """Return the ``-c:v`` ... ``-movflags +faststart`` slice of the
    ffmpeg invocation. The default profile keeps today's lossy-but-fast
    encode (CRF 20, AAC 192k); ``youtube_preset`` swaps in YouTube's
    recommended params (issue #204 layer 2).

    Resolution / fps inform GOP only -- 2 seconds at the sequence frame
    rate, rounded to the nearest frame. Quality is CRF 18 universally;
    YouTube re-encodes regardless, so a single-pass quality target is
    enough and avoids the extra runner invocation a true two-pass
    encode would require.
    """
    if not youtube_preset:
        return (
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        )
    fps = sequence.frame_rate_num / max(1, sequence.frame_rate_den)
    gop = max(1, int(round(fps * 2)))
    return (
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",  # closed GOP -- no scene-cut keyframes between the fixed boundaries
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-c:a",
        "aac",
        "-b:a",
        "384k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    )


def _build_stage_filter_graph(
    plan: _StagePlan,
    *,
    sequence,  # type: ignore[no-untyped-def]
    overlay_input_index: int | None,
) -> str:
    """Compose primary + cams + overlay into a single ``-filter_complex``.

    Primary becomes ``[base]``; each cam is scaled (when it has a
    transform) and overlaid at its computed corner with an ``enable``
    expression so cams that appear late on the spine don't show until
    their start. The overlay -- when present -- composites last so it
    sits on top of all cams.
    """
    parts: list[str] = []
    parts.append("[0:v]setpts=PTS-STARTPTS,format=yuv420p[base]")

    base_label = "base"
    for cam_idx, align in enumerate(plan.cam_alignments):
        input_label = f"{1 + cam_idx}:v"
        cam_label = f"cam{cam_idx}"
        scaled_label = f"{cam_label}_scaled"
        transform = align.cam.transform

        # Each cam gets setpts-zeroed and scaled when needed.
        scale_chain = "setpts=PTS-STARTPTS"
        if transform is not None and transform.scale != 1.0:
            target_w = int(round(sequence.width * transform.scale))
            target_h = int(round(sequence.height * transform.scale))
            scale_chain += f",scale={target_w}:{target_h}"
        parts.append(f"[{input_label}]{scale_chain}[{scaled_label}]")

        # ffmpeg's overlay filter places the secondary at top-left
        # (X, Y) on the base. Convert from IR's centre / +Y-up to
        # top-left / +Y-down.
        x, y = _overlay_position(transform, sequence)
        out_label = f"layer{cam_idx}"
        # ``enable`` makes the cam show only during its visible window
        # on the spine; outside that range the base shows through.
        end_time = align.cam_spine_start + align.cam_visible_seconds
        enable = f"between(t,{align.cam_spine_start:g},{end_time:g})"
        parts.append(
            f"[{base_label}][{scaled_label}]"
            f"overlay=x={x:g}:y={y:g}:enable='{enable}'[{out_label}]"
        )
        base_label = out_label

    if overlay_input_index is not None:
        parts.append(f"[{overlay_input_index}:v]setpts=PTS-STARTPTS[overlay_v]")
        parts.append(f"[{base_label}][overlay_v]overlay=0:0[withov]")
        base_label = "withov"

    parts.append(f"[{base_label}]null[final]")
    return ";".join(parts)


def _overlay_position(
    transform: Transform | None,
    sequence,  # type: ignore[no-untyped-def]
) -> tuple[float, float]:
    """Return the top-left (X, Y) ffmpeg ``overlay`` expects.

    No transform -> (0, 0): cam covers the base full-frame, matching
    today's stacked layout. With a transform: the cam centre lives at
    ``(W/2 + tx, H/2 - ty)`` (sequence-centre + IR offset, Y axis
    flipped); the top-left is that minus half the scaled clip's width
    / height.
    """
    if transform is None:
        return 0.0, 0.0
    scale = transform.scale
    seq_w = sequence.width
    seq_h = sequence.height
    centre_x = seq_w / 2.0 + transform.position[0]
    centre_y = seq_h / 2.0 - transform.position[1]  # flip
    clip_w = seq_w * scale
    clip_h = seq_h * scale
    return centre_x - clip_w / 2.0, centre_y - clip_h / 2.0


def _build_concat_command(
    *,
    list_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """Build the ``concat``-demuxer invocation that stitches per-stage
    temps without re-encoding."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    )


__all__ = ["FFmpegError", "render_mp4"]
