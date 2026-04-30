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
