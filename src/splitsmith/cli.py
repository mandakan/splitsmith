"""Typer CLI for splitsmith.

Subcommands (per SPEC.md):
- ``single``: run the full pipeline against one video with an explicit stage time.
- ``detect``: same as ``single`` but only prints results, writes nothing.
- ``process``: batch over a stage JSON, matching videos by mtime.
- ``fcpxml``: regenerate a timeline from a (possibly hand-edited) splits CSV.

This module orchestrates -- all detection / IO logic lives in dedicated modules.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import (
    audit,
    beep_detect,
    csv_gen,
    fcpxml_gen,
    report,
    review_server,
    shot_detect,
    shot_refine,
    trim,
    video_match,
)
from .config import (
    CompetitorStages,
    Config,
    ReportFiles,
    Shot,
    StageAnalysis,
    StageData,
)

app = typer.Typer(
    name="splitsmith",
    help="Extract IPSC shot splits from head-mounted camera footage.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@app.command()
def single(
    video: Path = typer.Option(..., "--video", help="Path to the video file."),
    time: float = typer.Option(..., "--time", help="Official stage time (seconds)."),
    output: Path = typer.Option(..., "--output", help="Output directory for analysis files."),
    stage_name: str = typer.Option("stage", "--stage-name", help="Slug used in output filenames."),
    stage_number: int = typer.Option(1, "--stage-number", help="Stage number for the report."),
    config_path: Path | None = typer.Option(None, "--config", help="Optional YAML config."),
    write_trim: bool = typer.Option(True, "--trim/--no-trim"),
    write_csv: bool = typer.Option(True, "--csv/--no-csv"),
    write_fcpxml: bool = typer.Option(True, "--fcpxml/--no-fcpxml"),
) -> None:
    """Process a single video with an explicit stage time."""
    config = Config.load(config_path)
    output.mkdir(parents=True, exist_ok=True)

    stage = StageData(
        stage_number=stage_number,
        stage_name=stage_name,
        time_seconds=time,
        scorecard_updated_at=_dummy_scorecard_time(),
    )
    files = _process_one(
        stage=stage,
        video=video,
        output_dir=output,
        config=config,
        write_trim=write_trim,
        write_csv=write_csv,
        write_fcpxml=write_fcpxml,
    )
    _print_files_summary(files)


@app.command()
def detect(
    video: Path = typer.Option(..., "--video", help="Path to the video file."),
    time: float = typer.Option(..., "--time", help="Official stage time (seconds)."),
    config_path: Path | None = typer.Option(None, "--config", help="Optional YAML config."),
) -> None:
    """Detect beep + shots and print results without writing any files."""
    config = Config.load(config_path)
    audio_path = _video_to_audio_path(video)
    audio, sr = _extract_or_load_audio(video, audio_path)

    console.print(f"[bold]{video.name}[/]: {len(audio) / sr:.2f}s @ {sr} Hz")
    beep = beep_detect.detect_beep(audio, sr, config.beep_detect)
    console.print(
        f"  beep: t=[cyan]{beep.time:.4f}s[/]  "
        f"peak={beep.peak_amplitude:.3f}  duration={beep.duration_ms:.0f}ms"
    )

    shots = shot_detect.detect_shots(audio, sr, beep.time, time, config.shot_detect)
    shots, refine_diffs = _refine_shot_times(audio, sr, shots, beep.time, config)
    if refine_diffs:
        console.print(
            f"  [cyan]refined {len(refine_diffs)} shot time(s)[/]: "
            + ", ".join(f"#{d['shot_number']} {d['drift_ms']:+.1f}ms" for d in refine_diffs)
        )
    _print_shots_table(shots)
    _print_anomalies(report.detect_anomalies(shots, beep.time, time))


@app.command()
def process(
    videos: Path = typer.Option(..., "--videos", help="Directory of video files."),
    stages: Path = typer.Option(..., "--stages", help="Stage JSON (SSI Scoreboard format)."),
    output: Path = typer.Option(..., "--output", help="Output directory for analysis files."),
    config_path: Path | None = typer.Option(None, "--config", help="Optional YAML config."),
    write_trim: bool = typer.Option(True, "--trim/--no-trim"),
    write_csv: bool = typer.Option(True, "--csv/--no-csv"),
    write_fcpxml: bool = typer.Option(True, "--fcpxml/--no-fcpxml"),
) -> None:
    """Batch-process every stage in a stage JSON, matching videos by file timestamp."""
    config = Config.load(config_path)
    output.mkdir(parents=True, exist_ok=True)

    competitor_stages = _load_stage_json(stages)
    video_paths = sorted(_iter_video_files(videos))
    match = video_match.match_videos_to_stages(
        video_paths, competitor_stages.stages, config.video_match
    )

    if match.unmatched_stages or match.ambiguous_stages or match.orphan_videos:
        _print_match_diagnostics(match)
    if not match.matches:
        raise typer.Exit(code=1)

    for m in match.matches:
        stage = next(s for s in competitor_stages.stages if s.stage_number == m.stage_number)
        console.rule(f"[bold]Stage {stage.stage_number}: {stage.stage_name}[/]")
        try:
            _process_one(
                stage=stage,
                video=m.video_path,
                output_dir=output,
                config=config,
                write_trim=write_trim,
                write_csv=write_csv,
                write_fcpxml=write_fcpxml,
            )
        except Exception as exc:  # noqa: BLE001 -- soft-fail per SPEC error-handling rules
            console.print(f"[red]Stage {stage.stage_number} failed: {exc}[/]")


@app.command()
def review(
    fixture: Path = typer.Option(..., "--fixture", help="Fixture JSON to audit."),
    video: Path | None = typer.Option(
        None, "--video", help="Optional source video to align alongside the waveform."
    ),
    video_offset_seconds: float | None = typer.Option(
        None,
        "--video-offset-seconds",
        help=(
            "Seconds added to the waveform time to seek the video. "
            "Defaults to fixture_window_in_source[0] from the fixture JSON."
        ),
    ),
    port: int = typer.Option(5173, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Skip auto-opening browser."),
) -> None:
    """Open the audit-only SPA in a browser to review/correct shot detections."""
    if not fixture.exists():
        raise typer.BadParameter(f"fixture not found: {fixture}")
    audio_path = fixture.with_suffix(".wav")
    if not audio_path.exists():
        raise typer.BadParameter(f"expected audio sibling not found: {audio_path}")
    if video is not None and not video.exists():
        raise typer.BadParameter(f"video not found: {video}")

    if video_offset_seconds is None:
        try:
            data = json.loads(fixture.read_text())
            video_offset_seconds = float(data.get("fixture_window_in_source", [0.0])[0])
        except (json.JSONDecodeError, KeyError, ValueError):
            video_offset_seconds = 0.0

    config = review_server.ReviewConfig(
        fixture_path=fixture.resolve(),
        audio_path=audio_path.resolve(),
        video_path=video.resolve() if video is not None else None,
        video_offset_seconds=video_offset_seconds,
    )
    server = review_server.make_server(host, port, config)
    url = f"http://{host}:{port}/"
    console.print(f"[green]Audit UI[/]: [bold]{url}[/]   (Ctrl+C to stop)")
    console.print(f"  fixture: {fixture}")
    console.print(f"  audio:   {audio_path}")
    if video is not None:
        console.print(f"  video:   {video}  (offset {video_offset_seconds:+.3f}s)")

    if not no_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")
    finally:
        server.server_close()


@app.command("audit-prep")
def audit_prep(
    video: Path = typer.Option(..., "--video", help="Source video file (mp4/mov)."),
    time: float = typer.Option(..., "--time", help="Official stage time in seconds."),
    stage_number: int = typer.Option(..., "--stage-number"),
    stage_name: str = typer.Option(..., "--stage-name"),
    output_dir: Path = typer.Option(
        Path("tests/fixtures"), "--output-dir", help="Where to write the fixture files."
    ),
    stem: str = typer.Option(..., "--stem", help="File stem (e.g. stage-shots-tallmilan-stage5)."),
    fixture_pre_pad_s: float = typer.Option(0.5, "--pre-pad-seconds"),
    fixture_post_pad_s: float = typer.Option(1.5, "--post-pad-seconds"),
    config_path: Path | None = typer.Option(None, "--config"),
    beep_time_override: float | None = typer.Option(
        None,
        "--beep-time",
        help=(
            "Source-time of the beep in seconds. Skips automatic beep detection "
            "(use when wind / steel rings / other 3 kHz transients fool detect_beep)."
        ),
    ),
    paper: int = typer.Option(
        0,
        "--paper",
        min=0,
        help="Paper-target count for the stage (each scored x2 in IPSC by default).",
    ),
    poppers: int = typer.Option(0, "--poppers", min=0, help="Popper count."),
    plates: int = typer.Option(0, "--plates", min=0, help="Plate count."),
    shots_per_paper: int = typer.Option(
        2,
        "--shots-per-paper",
        min=1,
        max=2,
        help="Shots per paper target. 2 for normal stages, 1 for strong/weak-hand-only.",
    ),
) -> None:
    """Build a review-ready fixture (wav + JSON + audit CSV) from a source video.

    Steps:
    1. Extract mono 48 kHz audio from the source video via ffmpeg.
    2. Detect the beep, then run shot_detect over the stage window.
    3. Slice ``[beep - pre_pad, beep + stage_time + post_pad]`` of the audio
       and save as ``<stem>.wav`` in ``output_dir``.
    4. Save ``<stem>.json`` (metadata + empty shots[] + candidate dump) and
       ``<stem>-candidates.csv`` (with audit_keep column).

    Existing files at the same paths are overwritten.
    """
    import csv as _csv
    import json as _json
    import subprocess as _subprocess

    import soundfile as sf

    config = Config.load(config_path)
    if not video.exists():
        raise typer.BadParameter(f"video not found: {video}")
    if not shutil.which("ffmpeg"):
        raise typer.BadParameter("ffmpeg is required to extract audio")
    output_dir.mkdir(parents=True, exist_ok=True)

    full_wav = output_dir / f"{stem}-FULL.wav"
    console.print(f"  extracting full audio -> {full_wav}")
    _subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-ac",
            "1",
            "-ar",
            "48000",
            "-vn",
            str(full_wav),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    audio, sr = beep_detect.load_audio(full_wav)
    full_wav.unlink()  # we keep only the sliced fixture wav

    if beep_time_override is not None:
        from .config import BeepDetection

        beep = BeepDetection(time=beep_time_override, peak_amplitude=0.0, duration_ms=0.0)
        console.print(f"  beep override: t=[cyan]{beep.time:.4f}s[/]  (detection skipped)")
    else:
        beep = beep_detect.detect_beep(audio, sr, config.beep_detect)
        console.print(
            f"  beep at source t=[cyan]{beep.time:.4f}s[/]  "
            f"peak={beep.peak_amplitude:.3f}  duration={beep.duration_ms:.0f}ms"
        )

    fix_lo = max(0.0, beep.time - fixture_pre_pad_s)
    fix_hi = min(len(audio) / sr, beep.time + time + fixture_post_pad_s)
    fixture_audio = audio[int(fix_lo * sr) : int(fix_hi * sr)]
    fixture_wav = output_dir / f"{stem}.wav"
    sf.write(fixture_wav, fixture_audio, sr, subtype="PCM_16")
    console.print(
        f"  fixture wav: {fixture_wav} "
        f"({len(fixture_audio) / sr:.2f}s, {fixture_wav.stat().st_size / 1e6:.2f} MB)"
    )

    detected = shot_detect.detect_shots(audio, sr, beep.time, time, config.shot_detect)
    beep_in_fixture = beep.time - fix_lo
    candidates = []
    for i, s in enumerate(detected, start=1):
        candidates.append(
            {
                "candidate_number": i,
                "time": round(s.time_absolute - fix_lo, 4),
                "ms_after_beep": round(s.time_from_beep * 1000, 0),
                "peak_amplitude": round(s.peak_amplitude, 4),
                "confidence": round(s.confidence, 3),
            }
        )

    fixture_json_data = {
        "source": (
            f"{video.name} stage {stage_number} '{stage_name}' " "(audio extracted at 48 kHz mono)"
        ),
        "stage_number": stage_number,
        "stage_name": stage_name,
        "fixture_window_in_source": [round(fix_lo, 4), round(fix_hi, 4)],
        "beep_time": round(beep_in_fixture, 4),
        "tolerance_ms": 15,
        "stage_time_seconds": time,
        "stage_window_end_in_fixture": round(beep_in_fixture + time, 4),
        "shots": [],
    }
    if paper or poppers or plates:
        fixture_json_data["stage_rounds"] = {
            "paper": paper,
            "poppers": poppers,
            "plates": plates,
            "shots_per_paper": shots_per_paper,
            "expected": paper * shots_per_paper + poppers + plates,
        }
    fixture_json = output_dir / f"{stem}.json"
    fixture_json.write_text(
        _json.dumps(
            {
                **fixture_json_data,
                "_candidates_pending_audit": {
                    "_note": (
                        "Auto-detected by current shot_detect (half-rise leading edge). "
                        "NOT ground truth. Open in `splitsmith review` to audit, "
                        "or mark audit_keep in the companion -candidates.csv and run "
                        "`splitsmith audit-apply`."
                    ),
                    "candidates": candidates,
                },
            },
            indent=2,
        )
        + "\n"
    )
    console.print(f"  fixture json: {fixture_json}  ({len(candidates)} candidates)")

    csv_path = output_dir / f"{stem}-candidates.csv"
    stage_end_ms = time * 1000
    with csv_path.open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(
            [
                "audit_keep",
                "candidate_number",
                "time_fixture_s",
                "time_source_s",
                "ms_after_beep",
                "split_from_prev_ms",
                "peak_amplitude",
                "confidence",
                "in_stage_window",
                "suspect_echo_lt_150ms",
            ]
        )
        prev_t = beep_in_fixture
        for c in candidates:
            t_fix = c["time"]
            t_src = round(t_fix + fix_lo, 4)
            split_ms = round((t_fix - prev_t) * 1000, 1)
            in_win = "Y" if c["ms_after_beep"] <= stage_end_ms + 1000 else "N"
            echo = "Y" if split_ms < 150 else ""
            w.writerow(
                [
                    "",
                    c["candidate_number"],
                    t_fix,
                    t_src,
                    c["ms_after_beep"],
                    split_ms,
                    c["peak_amplitude"],
                    c["confidence"],
                    in_win,
                    echo,
                ]
            )
            prev_t = t_fix
    console.print(f"  candidates csv: {csv_path}")
    console.print(
        f"\n[green]Ready.[/] Open in the UI:\n"
        f"  uv run splitsmith review --fixture {fixture_json} --video {video}"
    )


@app.command("audit-apply")
def audit_apply(
    candidates: Path = typer.Option(
        ..., "--candidates", help="The audited candidates CSV (must contain audit_keep column)."
    ),
    fixture: Path = typer.Option(
        ..., "--fixture", help="The corresponding fixture JSON to update in place."
    ),
) -> None:
    """Merge audit_keep-marked rows from a candidates CSV into a fixture JSON's shots[]."""
    n = audit.apply_audit_to_fixture(candidates, fixture)
    console.print(
        f"[green]Wrote {n} audited shots[/] to {fixture} "
        f"(from {candidates.name}, audit_keep column)."
    )


@app.command()
def fcpxml(
    csv_path: Path = typer.Option(..., "--csv", help="Splits CSV (possibly hand-edited)."),
    video: Path = typer.Option(..., "--video", help="Trimmed video the markers anchor to."),
    output: Path = typer.Option(..., "--output", help="Output FCPXML file."),
    beep_offset: float = typer.Option(
        5.0, "--beep-offset", help="Seconds from start of trimmed video to the beep."
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Optional YAML config."),
    project_name: str | None = typer.Option(None, "--project-name"),
) -> None:
    """Regenerate FCPXML from a (possibly hand-edited) splits CSV."""
    config = Config.load(config_path)
    rows = csv_gen.read_splits_csv(csv_path)
    shots = [
        Shot(
            shot_number=r.shot_number,
            time_absolute=beep_offset + r.time_from_start,
            time_from_beep=r.time_from_start,
            split=r.split,
            peak_amplitude=r.peak_amplitude,
            confidence=r.confidence,
            notes=r.notes,
        )
        for r in rows
    ]
    meta = fcpxml_gen.probe_video(video)
    fcpxml_gen.generate_fcpxml(
        video_path=video,
        video=meta,
        shots=shots,
        beep_offset_seconds=beep_offset,
        output_path=output,
        project_name=project_name or video.stem,
        config=config.output,
    )
    console.print(f"[green]Wrote[/] {output}")


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _process_one(
    *,
    stage: StageData,
    video: Path,
    output_dir: Path,
    config: Config,
    write_trim: bool,
    write_csv: bool,
    write_fcpxml: bool,
) -> ReportFiles:
    """End-to-end pipeline for a single (stage, video) pair. Returns the file footer."""
    base = f"stage{stage.stage_number}_{_slugify(stage.stage_name)}"
    audio_path = _video_to_audio_path(video)
    audio, sr = _extract_or_load_audio(video, audio_path)

    beep = beep_detect.detect_beep(audio, sr, config.beep_detect)
    console.print(
        f"  beep: t=[cyan]{beep.time:.4f}s[/]  "
        f"peak={beep.peak_amplitude:.3f}  duration={beep.duration_ms:.0f}ms"
    )

    shots = shot_detect.detect_shots(audio, sr, beep.time, stage.time_seconds, config.shot_detect)
    shots, refine_diffs = _refine_shot_times(audio, sr, shots, beep.time, config)
    if refine_diffs:
        console.print(
            f"  [cyan]refined {len(refine_diffs)} shot time(s)[/]: "
            + ", ".join(f"#{i + 1} {d['drift_ms']:+.1f}ms" for i, d in enumerate(refine_diffs))
        )
    _print_shots_table(shots)

    files = ReportFiles()
    if write_trim:
        files.video = output_dir / f"{base}_trimmed.mp4"
        trim.trim_video(
            video,
            files.video,
            beep_time=beep.time,
            stage_time=stage.time_seconds,
            buffer_seconds=config.output.trim_buffer_seconds,
            overwrite=True,
        )
        console.print(f"  [green]trimmed video[/]: {files.video}")

    if write_csv:
        files.csv = output_dir / f"{base}_splits.csv"
        csv_gen.write_splits_csv(shots, files.csv)
        console.print(f"  [green]splits CSV[/]:    {files.csv}")

    if write_fcpxml and files.video and files.video.exists():
        files.fcpxml = output_dir / f"{base}.fcpxml"
        meta = fcpxml_gen.probe_video(files.video)
        fcpxml_gen.generate_fcpxml(
            video_path=files.video,
            video=meta,
            shots=shots,
            beep_offset_seconds=config.output.trim_buffer_seconds,
            output_path=files.fcpxml,
            project_name=base,
            config=config.output,
        )
        console.print(f"  [green]FCPXML[/]:        {files.fcpxml}")

    anomalies = report.detect_anomalies(shots, beep.time, stage.time_seconds)
    analysis = StageAnalysis(
        stage=stage,
        video_path=video,
        beep_time=beep.time,
        shots=shots,
        anomalies=anomalies,
    )
    report_path = output_dir / f"{base}_report.txt"
    report.write_report(analysis, files, report_path)
    console.print(f"  [green]report[/]:        {report_path}")
    _print_anomalies(anomalies)
    return files


def _refine_shot_times(
    audio,
    sr: int,
    shots: list[Shot],
    beep_time: float,
    config: Config,
) -> tuple[list[Shot], list[dict]]:
    """Run second-pass timing refinement on each shot.

    Returns (refined_shots, diffs) where ``diffs`` lists only the shots whose
    timestamp was actually moved (accepted by ``shot_refine`` and non-zero
    drift). Splits and time_from_beep are recomputed from the refined times.
    """
    refined: list[Shot] = []
    diffs: list[dict] = []
    prev_t = beep_time
    for s in shots:
        r = shot_refine.refine_shot_time(audio, sr, s.time_absolute, config.shot_refine)
        new_t = r.time if r.accepted else s.time_absolute
        if r.accepted and abs(r.drift_ms) > 0.01:
            diffs.append({"shot_number": s.shot_number, "drift_ms": r.drift_ms})
        refined.append(
            Shot(
                shot_number=s.shot_number,
                time_absolute=new_t,
                time_from_beep=new_t - beep_time,
                split=new_t - prev_t,
                peak_amplitude=s.peak_amplitude,
                confidence=s.confidence,
                notes=s.notes,
            )
        )
        prev_t = new_t
    return refined, diffs


def _video_to_audio_path(video: Path) -> Path:
    """Return a sibling path where the extracted mono wav can be cached."""
    return video.with_suffix(".wav")


def _extract_or_load_audio(video: Path, audio_path: Path):
    """Extract mono 48 kHz wav via ffmpeg if not already cached, then load it.

    The cached wav is intentionally placed next to the source video; the caller
    decides what to do with it. For a one-off ``detect`` we never delete it,
    which trades disk for repeat-run speed.
    """
    if not audio_path.exists() or audio_path.stat().st_mtime < video.stat().st_mtime:
        if not shutil.which("ffmpeg"):
            raise typer.BadParameter("ffmpeg is required to extract audio from video files")
        import subprocess

        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video),
                "-ac",
                "1",
                "-ar",
                "48000",
                "-vn",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return beep_detect.load_audio(audio_path)


def _load_stage_json(path: Path) -> CompetitorStages:
    """Load the SSI Scoreboard JSON and return the first competitor's stages."""
    raw = json.loads(path.read_text())
    competitors = raw.get("competitors") or []
    if not competitors:
        raise typer.BadParameter(f"no competitors in {path}")
    return CompetitorStages.model_validate(competitors[0])


def _iter_video_files(directory: Path):
    if not directory.is_dir():
        raise typer.BadParameter(f"not a directory: {directory}")
    return [p for p in directory.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v"}]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "stage"


def _dummy_scorecard_time():
    """A placeholder ``scorecard_updated_at`` for ``single``-mode pipelines that
    never touch ``video_match``."""
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def _print_shots_table(shots: list[Shot]) -> None:
    table = Table(title=f"{len(shots)} shots", show_header=True)
    table.add_column("#", justify="right")
    table.add_column("t_abs (s)", justify="right")
    table.add_column("from beep (s)", justify="right")
    table.add_column("split (s)", justify="right")
    table.add_column("peak", justify="right")
    table.add_column("conf", justify="right")
    for s in shots:
        table.add_row(
            str(s.shot_number),
            f"{s.time_absolute:.3f}",
            f"{s.time_from_beep:.3f}",
            f"{s.split:.3f}",
            f"{s.peak_amplitude:.3f}",
            f"{s.confidence:.2f}",
        )
    console.print(table)


def _print_anomalies(anomalies: list[str]) -> None:
    if not anomalies:
        console.print("[green]No anomalies.[/]")
        return
    console.print("[yellow]Anomalies:[/]")
    for a in anomalies:
        console.print(f"  [yellow]- {a}[/]")


def _print_match_diagnostics(match) -> None:
    if match.unmatched_stages:
        console.print(f"[yellow]Unmatched stages:[/] {match.unmatched_stages}")
    if match.ambiguous_stages:
        console.print("[yellow]Ambiguous stages:[/]")
        for stage_num, candidates in match.ambiguous_stages.items():
            names = ", ".join(p.name for p in candidates)
            console.print(f"  stage {stage_num}: {names}")
    if match.orphan_videos:
        console.print(f"[yellow]Orphan videos:[/] {[p.name for p in match.orphan_videos]}")


def _print_files_summary(files: ReportFiles) -> None:
    if any([files.video, files.csv, files.fcpxml]):
        console.print("[bold]Wrote:[/]")
        for label, p in (("video", files.video), ("csv", files.csv), ("fcpxml", files.fcpxml)):
            if p:
                console.print(f"  {label:>6}: {p}")


if __name__ == "__main__":
    app()
