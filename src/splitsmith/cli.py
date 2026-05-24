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
    overlay_render,
    overlay_theme,
    report,
    shot_detect,
    shot_refine,
    trim,
    video_match,
)
from . import (
    automation as automation_settings,
)
from . import (
    cleanup as cleanup_mod,
)
from .config import (
    CompetitorStages,
    Config,
    ReportFiles,
    Shot,
    StageAnalysis,
    StageData,
)
from .runtime import runtime
from .ui.project import MatchProject

app = typer.Typer(
    name="splitsmith",
    help="Extract IPSC shot splits from head-mounted camera footage.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

from .compare.cli import compare_app  # noqa: E402
from .lab_cli import app as _lab_app  # noqa: E402
from .match_cli import match_app  # noqa: E402
from .model_cli import fetch_models as _fetch_models  # noqa: E402

app.add_typer(_lab_app, name="lab")
app.add_typer(compare_app, name="compare")
app.add_typer(match_app, name="match")
app.command("fetch-models")(_fetch_models)


project_app = typer.Typer(
    name="project",
    help="Match-project housekeeping: export/import for backup or transfer.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(project_app, name="project")


@project_app.command("export")
def project_export(
    project_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Path to the MatchProject directory (the one containing project.json).",
    ),
    output: Path = typer.Option(
        Path(),
        "--output",
        "-o",
        help=(
            "Destination. If a directory, the archive filename is "
            "<project-slug>-backup-YYYYMMDD.tar.gz. If a file, used as-is."
        ),
    ),
    include_trimmed: bool = typer.Option(
        False,
        "--with-trimmed",
        help="Include trimmed/ (per-stage MP4s, regeneratable).",
    ),
    include_exports: bool = typer.Option(
        False,
        "--with-exports",
        help="Include exports/ (FCPXML, CSV, lossless trims).",
    ),
    include_raw: bool = typer.Option(
        False,
        "--with-raw",
        help="Include the raw/ subdirectory (source video).",
    ),
    include_audio: bool = typer.Option(
        False,
        "--with-audio",
        help="Include the audio/ subdirectory (extracted wav).",
    ),
) -> None:
    """Tar the non-regeneratable parts of a project for backup or transfer.

    Default archive contains ``project.json`` plus ``audit/`` and
    ``scoreboard/`` -- the only artefacts that cannot be regenerated from
    the source footage. Use the ``--with-*`` flags to opt regeneratable
    directories into the archive.
    """
    from .backup import export_project

    result = export_project(
        project_dir,
        output,
        include_trimmed=include_trimmed,
        include_exports=include_exports,
        include_raw=include_raw,
        include_audio=include_audio,
    )
    size_mb = result.bytes_written / (1024 * 1024)
    console.print(f"[green]Wrote[/] {result.archive_path} ({size_mb:.1f} MB)")
    console.print(f"  included: {', '.join(result.included)}")
    if result.skipped:
        for s in result.skipped:
            console.print(
                f"  [yellow]skipped[/] {s.name} ({s.reason})"
                + (f": {s.resolved_path}" if s.resolved_path else "")
            )


@project_app.command("import")
def project_import(
    archive: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to a .tar.gz produced by `splitsmith project export`.",
    ),
    dest: Path = typer.Option(
        ...,
        "--dest",
        "-d",
        help="Destination directory. The archive's top-level folder is restored under it.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="If the target directory already exists, replace it.",
    ),
) -> None:
    """Restore a project archive produced by ``splitsmith project export``."""
    from .backup import import_project

    result = import_project(archive, dest, overwrite=overwrite)
    console.print(f"[green]Restored[/] {result.project_name} -> {result.project_root}")


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
    auto_detect: bool | None = typer.Option(
        None,
        "--auto-detect/--no-auto-detect",
        help=(
            "Whether to run shot detection after the beep. Defaults to the "
            "global automation setting (True). Use --no-auto-detect for a "
            "trim-only export (issue #215)."
        ),
    ),
    trim_mode: str | None = typer.Option(
        None,
        "--trim-mode",
        help=(
            "Override trim mode: 'lossless' (-c copy, instant, archival) or "
            "'audit' (re-encode with short GOP for scrub-friendly playback). "
            "Default: from config (lossless)."
        ),
    ),
) -> None:
    """Process a single video with an explicit stage time."""
    config = Config.load(config_path)
    if trim_mode is not None:
        config = config.model_copy(update={"output": _override_trim_mode(config.output, trim_mode)})
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
        auto_detect_shots=_resolve_cli_auto_detect(auto_detect),
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
    auto_detect: bool | None = typer.Option(
        None,
        "--auto-detect/--no-auto-detect",
        help=(
            "Whether to run shot detection after the beep. Defaults to the "
            "global automation setting (True). Use --no-auto-detect to skip "
            "shot detection across the batch (issue #215)."
        ),
    ),
    trim_mode: str | None = typer.Option(
        None,
        "--trim-mode",
        help="Override trim mode: 'lossless' or 'audit'. Default: from config (lossless).",
    ),
) -> None:
    """Batch-process every stage in a stage JSON, matching videos by file timestamp."""
    config = Config.load(config_path)
    if trim_mode is not None:
        config = config.model_copy(update={"output": _override_trim_mode(config.output, trim_mode)})
    output.mkdir(parents=True, exist_ok=True)

    competitor_stages = _load_stage_json(stages)
    video_paths = sorted(_iter_video_files(videos))
    match = video_match.match_videos_to_stages(video_paths, competitor_stages.stages, config.video_match)

    if match.unmatched_stages or match.ambiguous_stages or match.orphan_videos:
        _print_match_diagnostics(match)
    if not match.matches:
        raise typer.Exit(code=1)

    auto_detect_shots = _resolve_cli_auto_detect(auto_detect)
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
                auto_detect_shots=auto_detect_shots,
            )
        except Exception as exc:  # noqa: BLE001 -- soft-fail per SPEC error-handling rules
            console.print(f"[red]Stage {stage.stage_number} failed: {exc}[/]")


@app.command()
def review(
    fixture: Path = typer.Option(..., "--fixture", help="Fixture JSON to audit."),
    video: Path | None = typer.Option(
        None, "--video", help="Optional source video to align alongside the waveform."
    ),
    port: int = typer.Option(5174, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Skip auto-opening browser."),
) -> None:
    """Open the production UI's standalone fixture-review page.

    Boots the same server ``splitsmith ui`` uses, with a throwaway project
    root, then opens ``/review?fixture=...&video=...``. The route reads
    the fixture JSON via the API, edits markers in-memory, saves back to
    the same path with a ``.bak`` for the previous version. The
    standalone splitsmith.review_server has been retired (#19).
    """
    import tempfile
    from urllib.parse import urlencode

    from .ui.server import serve

    if not fixture.exists():
        raise typer.BadParameter(f"fixture not found: {fixture}")
    audio_path = fixture.with_suffix(".wav")
    if not audio_path.exists():
        raise typer.BadParameter(f"expected audio sibling not found: {audio_path}")
    if video is not None and not video.exists():
        raise typer.BadParameter(f"video not found: {video}")

    fixture_resolved = fixture.resolve()
    video_resolved = video.resolve() if video is not None else None

    # The production UI server requires a project root, but the fixture
    # endpoints don't touch project state. Use a throwaway tmpdir so the
    # server boots cleanly and nothing from this run pollutes a real
    # match folder. Cleanup is left to the OS.
    tmp_root = Path(tempfile.mkdtemp(prefix="splitsmith-review-"))

    qs = {"fixture": str(fixture_resolved)}
    if video_resolved is not None:
        qs["video"] = str(video_resolved)
    url = f"http://{host}:{port}/review?{urlencode(qs)}"

    console.print(f"[green]splitsmith review[/]: [bold]{url}[/]   (Ctrl+C to stop)")
    console.print(f"  fixture: {fixture_resolved}")
    console.print(f"  audio:   {audio_path.resolve()}")
    if video_resolved is not None:
        console.print(f"  video:   {video_resolved}")

    if not no_browser:
        import webbrowser

        webbrowser.open(url)

    try:
        serve(project_root=tmp_root, project_name="review", host=host, port=port)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")


@app.command()
def ui(
    project: Path | None = typer.Option(
        None,
        "--project",
        help=(
            "Match-project root directory. Created (with subdirs) if missing. "
            "Omit to boot the picker; pass --last to reopen the most recent."
        ),
    ),
    project_name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Display name for the match. Defaults to the project directory's "
            "basename. Ignored if the project already has a name on disk."
        ),
    ),
    last: bool = typer.Option(
        False,
        "--last",
        help="Open the most-recently-opened project. Errors if the recent list is empty.",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(5174, "--port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Skip auto-opening browser."),
    lab: bool = typer.Option(
        False,
        "--lab",
        help=(
            "Expose the Algorithm Lab page (fixture eval + labeling). "
            "Hidden by default since it's a developer tool that loads "
            "heavy CLAP/PANN models on first use."
        ),
    ),
    skip_system_check: bool = typer.Option(
        False,
        "--skip-system-check",
        help=(
            "Bypass the first-launch ffmpeg / ffprobe presence check. "
            "Use only for debugging install issues -- detection will "
            "fail with a cryptic error if either binary is missing."
        ),
    ),
) -> None:
    """Start the production UI server (issue #11/#12).

    The UI is a localhost SPA driven by a FastAPI backend that orchestrates
    the existing engine modules unchanged. State persists to disk under
    ``--project`` so closing the browser and re-running resumes where you
    left off. With no ``--project`` (and no ``--last``), the server boots
    "unbound" -- the SPA renders a picker drawn from
    ``~/.splitsmith/projects.json`` and binds in-memory once the user picks.
    """
    from . import user_config
    from .ui.server import serve

    if last:
        if project is not None:
            raise typer.BadParameter("--last and --project are mutually exclusive")
        recents = user_config.get_recent_projects()
        if not recents:
            raise typer.BadParameter("no recent projects to reopen; run with --project")
        project = Path(recents[0].path)

    resolved: Path | None = None
    name: str | None = None
    if project is not None:
        resolved = project.expanduser().resolve()
        name = project_name or resolved.name or "match"

    url = f"http://{host}:{port}/"
    console.print(f"[green]splitsmith UI[/]: [bold]{url}[/]   (Ctrl+C to stop)")
    if resolved is not None:
        console.print(f"  project: {resolved}")
    else:
        console.print("  project: [dim]none -- showing picker[/]")
    if lab:
        console.print("  [cyan]Algorithm Lab[/] enabled")

    if not no_browser:
        import webbrowser

        webbrowser.open(url)

    try:
        serve(
            project_root=resolved,
            project_name=name,
            host=host,
            port=port,
            lab_enabled=lab,
            skip_system_check=skip_system_check,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")


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
    ffmpeg_bin = runtime().ffmpeg_binary
    if not shutil.which(ffmpeg_bin):
        raise typer.BadParameter(f"ffmpeg binary not found: {ffmpeg_bin}")
    output_dir.mkdir(parents=True, exist_ok=True)

    full_wav = output_dir / f"{stem}-FULL.wav"
    console.print(f"  extracting full audio -> {full_wav}")
    _subprocess.run(
        [
            ffmpeg_bin,
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
        "source": (f"{video.name} stage {stage_number} '{stage_name}' " "(audio extracted at 48 kHz mono)"),
        # Structured absolute path to the source video. Lets the
        # Lab UI's Re-label button hop straight to /review with the
        # video bound, instead of needing the CLI ``--video`` flag.
        "source_video": str(Path(video).resolve()),
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
    fixture: Path = typer.Option(..., "--fixture", help="The corresponding fixture JSON to update in place."),
) -> None:
    """Merge audit_keep-marked rows from a candidates CSV into a fixture JSON's shots[]."""
    n = audit.apply_audit_to_fixture(candidates, fixture)
    console.print(
        f"[green]Wrote {n} audited shots[/] to {fixture} " f"(from {candidates.name}, audit_keep column)."
    )


@app.command()
def clean(
    project: Path = typer.Argument(..., help="Match-project root directory."),
    caches: bool = typer.Option(
        False, "--caches", help="Thumbnails, ffprobes, scoreboard cache, waveform peaks."
    ),
    exports_light: bool = typer.Option(
        False, "--exports-light", help="CSV / FCPXML / report.txt under exports/."
    ),
    exports_overlays: bool = typer.Option(
        False, "--exports-overlays", help="Pre-rendered overlay MOVs under exports/ (large)."
    ),
    exports_trims: bool = typer.Option(
        False, "--exports-trims", help="Lossless trimmed MP4s under exports/ (large)."
    ),
    audit_trims: bool = typer.Option(
        False, "--audit-trims", help="Audit-mode short-GOP trims under trimmed/ (large)."
    ),
    audio: bool = typer.Option(
        False, "--audio", help="Extracted WAVs under audio/ (medium; re-extracted by detection)."
    ),
    include_audit: bool = typer.Option(
        False,
        "--include-audit",
        help=(
            "Also delete per-stage audit JSONs and .bak backups. DESTRUCTIVE: "
            "loses your shot-audit work for the project."
        ),
    ),
    all_: bool = typer.Option(
        False,
        "--all",
        help="Everything except --include-audit. Combine with --include-audit to wipe audit too.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Actually delete. Omit for a dry-run preview."),
) -> None:
    """Reclaim disk space from a match project.

    Default is dry-run: prints the plan and exits without deleting. Pass
    ``--yes`` to apply. The original source video files (and the
    symlinks under ``raw/``) are never touched. ``project.json`` is
    never touched.
    """
    if not project.exists() or not (project / "project.json").exists():
        raise typer.BadParameter(f"not a match project: {project}")
    proj = MatchProject.load(project)

    selected: set[cleanup_mod.CleanupCategory] = set()
    flag_pairs: list[tuple[bool, cleanup_mod.CleanupCategory]] = [
        (caches, cleanup_mod.CleanupCategory.CACHES),
        (exports_light, cleanup_mod.CleanupCategory.EXPORTS_LIGHT),
        (exports_overlays, cleanup_mod.CleanupCategory.EXPORTS_OVERLAYS),
        (exports_trims, cleanup_mod.CleanupCategory.EXPORTS_TRIMS),
        (audit_trims, cleanup_mod.CleanupCategory.AUDIT_TRIMS),
        (audio, cleanup_mod.CleanupCategory.AUDIO),
    ]
    for flag, cat in flag_pairs:
        if flag:
            selected.add(cat)
    if all_:
        selected |= cleanup_mod.SAFE_CATEGORIES
    if include_audit:
        selected.add(cleanup_mod.CleanupCategory.AUDIT_DATA)

    if not selected:
        raise typer.BadParameter(
            "select at least one category (e.g. --caches, --exports-overlays, --all). "
            "Run with --help for the full list."
        )

    plan = cleanup_mod.plan_cleanup(proj, project, selected)
    _print_cleanup_plan(plan, selected)

    if not yes:
        console.print("\n[dim]Dry run.[/] Re-run with [bold]--yes[/] to delete.")
        return

    result = cleanup_mod.apply_cleanup(plan, root=project)
    freed_mb = result.bytes_freed / (1024 * 1024)
    console.print(f"\n[green]Deleted {len(result.deleted)} file(s)[/], freed [bold]{freed_mb:.1f} MB[/].")
    if result.failed:
        console.print(f"[yellow]{len(result.failed)} failed:[/]")
        for path, err in result.failed[:10]:
            console.print(f"  [yellow]- {path}: {err}[/]")
        if len(result.failed) > 10:
            console.print(f"  [dim]... and {len(result.failed) - 10} more[/]")


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
    meta = fcpxml_gen.probe_video(video, ffprobe_binary=runtime().ffprobe_binary)
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


@app.command()
def overlay(
    audit_path: Path = typer.Option(
        ..., "--audit", help="Audited stage JSON (e.g. <project>/audit/stage<N>.json)."
    ),
    video: Path = typer.Option(..., "--video", help="Trimmed video the overlay must mirror frame-for-frame."),
    output: Path = typer.Option(..., "--output", help="Output overlay MOV (alpha)."),
    beep_offset: float = typer.Option(
        5.0, "--beep-offset", help="Seconds from start of trimmed video to the beep."
    ),
    codec: str = typer.Option(
        "auto",
        "--codec",
        help=(
            "Encoder: 'auto' (HEVC w/ alpha on macOS, ProRes 4444 elsewhere), "
            "'hevc-alpha' (smallest, macOS only), or 'prores-4444' (largest, "
            "cross-platform / archival)."
        ),
    ),
    max_height: int | None = typer.Option(
        None,
        "--max-height",
        help=(
            "Cap output height (aspect preserved). FCPXML emits a separate "
            "format so FCP scales it back up."
        ),
    ),
    max_fps: float | None = typer.Option(
        None, "--max-fps", help="Cap output frame rate. Source rate kept when below cap."
    ),
    font_name: str | None = typer.Option(
        None, "--font", help=f"Preset font: {', '.join(overlay_render.available_font_names())}."
    ),
    theme: str = typer.Option(
        "splitsmith",
        "--theme",
        help=(
            f"Color palette preset: {', '.join(overlay_theme.THEME_NAMES)}. "
            f"'splitsmith' uses the same tokens as the web UI; 'clean' "
            f"is the neutral white-on-amber alternative."
        ),
    ),
) -> None:
    """Render an alpha overlay MOV for an audited stage.

    The overlay drops onto V2 in FCP as a connected clip; the renderer
    mirrors the trimmed clip's resolution / fps / duration unless capped.
    """
    if codec not in overlay_render.OVERLAY_CODECS:
        raise typer.BadParameter(f"--codec must be one of {overlay_render.OVERLAY_CODECS}, got {codec!r}")
    if theme not in overlay_theme.THEME_NAMES:
        raise typer.BadParameter(f"--theme must be one of {overlay_theme.THEME_NAMES}, got {theme!r}")
    overlay_render.render_overlay(
        audit_path=audit_path,
        trimmed_video_path=video,
        output_path=output,
        beep_offset_seconds=beep_offset,
        codec=codec,  # type: ignore[arg-type]
        max_height=max_height,
        max_fps=max_fps,
        font_name=font_name,
        theme=theme,  # type: ignore[arg-type]
        ffmpeg_binary=runtime().ffmpeg_binary,
    )
    console.print(f"[green]Wrote[/] {output}")


@app.command()
def mcp(
    allowed_root: Path | None = typer.Option(
        None,
        "--allowed-root",
        help=(
            "Optional sandbox root. Every path argument the agent passes "
            "(project_root, video files, directories) must resolve under "
            "this directory; otherwise the tool errors with SandboxError. "
            "When omitted the server runs without a sandbox and the agent "
            "has the same filesystem access as the launching user."
        ),
    ),
) -> None:
    """Run the splitsmith Model Context Protocol server over stdio (issue #211).

    Exposes splitsmith's pipeline as agent-callable tools. This layer
    (#211 layer 1) ships the read-only surface (probe video, discover
    videos, get project, list stages, get HITL queue); subsequent
    layers add write tools, detection orchestration, and the export
    pipeline.

    Configure your MCP-aware client (Claude Desktop, Claude Code, etc.)
    to launch this command and pipe stdio. ``--allowed-root`` is the
    single knob to constrain filesystem access.
    """
    import os

    from .mcp import create_server
    from .mcp.sandbox import ALLOWED_ROOT_ENV

    if allowed_root is not None:
        resolved = allowed_root.expanduser().resolve()
        if not resolved.is_dir():
            raise typer.BadParameter(f"--allowed-root {resolved} is not a directory")
        os.environ[ALLOWED_ROOT_ENV] = str(resolved)
    server = create_server()
    server.run()


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _resolve_cli_auto_detect(flag: bool | None) -> bool:
    """Resolve the ``--auto-detect/--no-auto-detect`` flag against the
    layered automation settings (#215).

    ``flag is None`` means the user didn't pass either form; fall
    through to the project (CLI doesn't know which project, so just
    the global settings) + global default. ``True`` / ``False``
    overrides everything else.
    """
    cli_override = (
        automation_settings.AutomationOverride(shot_detect_on_beep_verified=flag)
        if flag is not None
        else None
    )
    resolved = automation_settings.resolve_automation(cli_override=cli_override)
    return resolved.settings.shot_detect_on_beep_verified


def _process_one(
    *,
    stage: StageData,
    video: Path,
    output_dir: Path,
    config: Config,
    write_trim: bool,
    write_csv: bool,
    write_fcpxml: bool,
    auto_detect_shots: bool = True,
) -> ReportFiles:
    """End-to-end pipeline for a single (stage, video) pair. Returns the file footer.

    ``auto_detect_shots=False`` (the CLI's ``--no-auto-detect``) skips
    the shot-detection step. Trim, CSV (empty), and FCPXML (no
    markers) still produce -- mirrors the permissive export gate
    introduced in #214.
    """
    base = f"stage{stage.stage_number}_{_slugify(stage.stage_name)}"
    audio_path = _video_to_audio_path(video)
    audio, sr = _extract_or_load_audio(video, audio_path)

    beep = beep_detect.detect_beep(audio, sr, config.beep_detect)
    console.print(
        f"  beep: t=[cyan]{beep.time:.4f}s[/]  "
        f"peak={beep.peak_amplitude:.3f}  duration={beep.duration_ms:.0f}ms"
    )

    if auto_detect_shots:
        shots = shot_detect.detect_shots(audio, sr, beep.time, stage.time_seconds, config.shot_detect)
        shots, refine_diffs = _refine_shot_times(audio, sr, shots, beep.time, config)
        if refine_diffs:
            console.print(
                f"  [cyan]refined {len(refine_diffs)} shot time(s)[/]: "
                + ", ".join(f"#{i + 1} {d['drift_ms']:+.1f}ms" for i, d in enumerate(refine_diffs))
            )
        _print_shots_table(shots)
    else:
        shots = []
        console.print("  [yellow]shot detection skipped[/] (--no-auto-detect)")

    files = ReportFiles()
    if write_trim:
        files.video = output_dir / f"{base}_trimmed.mp4"
        trim.trim_video(
            video,
            files.video,
            beep_time=beep.time,
            stage_time=stage.time_seconds,
            buffer_seconds=config.output.trim_buffer_seconds,
            mode=config.output.trim_mode,
            gop_frames=config.output.trim_gop_frames,
            crf=config.output.trim_audit_crf,
            preset=config.output.trim_audit_preset,
            overwrite=True,
            ffmpeg_binary=runtime().ffmpeg_binary,
        )
        console.print(f"  [green]trimmed video[/]: {files.video}")

    if write_csv and shots:
        files.csv = output_dir / f"{base}_splits.csv"
        csv_gen.write_splits_csv(shots, files.csv)
        console.print(f"  [green]splits CSV[/]:    {files.csv}")
    elif write_csv:
        # Mirror the permissive export gate (#214): no CSV when no
        # shots; the trim + FCPXML still ship.
        console.print("  [yellow]splits CSV[/]:    skipped (no shots)")

    if write_fcpxml and files.video and files.video.exists():
        files.fcpxml = output_dir / f"{base}.fcpxml"
        meta = fcpxml_gen.probe_video(files.video, ffprobe_binary=runtime().ffprobe_binary)
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
    if video.suffix.lower() == ".wav":
        # Input is already a WAV; skip the ffmpeg pass entirely. The
        # downstream detection code handles arbitrary sample rates.
        # This avoids two failure modes that bit the slim-smoke job:
        # (1) Linux ffmpeg refusing input==output with exit 254;
        # (2) macOS ffmpeg with ``-y`` silently overwriting the source.
        return beep_detect.load_audio(video)
    if audio_path == video:
        # Defensive: caller-supplied audio_path that collides with the
        # video. Load in place rather than ffmpeg-ing into the input.
        return beep_detect.load_audio(video)
    if not audio_path.exists() or audio_path.stat().st_mtime < video.stat().st_mtime:
        ffmpeg_bin = runtime().ffmpeg_binary
        if not shutil.which(ffmpeg_bin):
            raise typer.BadParameter(f"ffmpeg binary not found: {ffmpeg_bin} (required to extract audio)")
        import subprocess

        subprocess.run(
            [
                ffmpeg_bin,
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


def _override_trim_mode(output_config, value: str):
    """Validate ``--trim-mode`` against the OutputConfig.trim_mode literal and
    return a copy with the override applied."""
    if value not in ("lossless", "audit"):
        raise typer.BadParameter("--trim-mode must be 'lossless' or 'audit'")
    return output_config.model_copy(update={"trim_mode": value})


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


_CATEGORY_LABELS: dict[cleanup_mod.CleanupCategory, str] = {
    cleanup_mod.CleanupCategory.CACHES: "Caches (thumbs, probes, scoreboard, peaks)",
    cleanup_mod.CleanupCategory.EXPORTS_LIGHT: "Light exports (CSV / FCPXML / report)",
    cleanup_mod.CleanupCategory.EXPORTS_OVERLAYS: "Overlay MOVs",
    cleanup_mod.CleanupCategory.EXPORTS_TRIMS: "Lossless trims",
    cleanup_mod.CleanupCategory.AUDIT_TRIMS: "Audit-mode trims",
    cleanup_mod.CleanupCategory.AUDIO: "Extracted audio",
    cleanup_mod.CleanupCategory.AUDIT_DATA: "Audit JSON + backups (DESTRUCTIVE)",
}


def _print_cleanup_plan(
    plan: cleanup_mod.CleanupPlan,
    selected: set[cleanup_mod.CleanupCategory],
) -> None:
    table = Table(title=f"{plan.total_file_count} files / {plan.total_bytes / (1024*1024):.1f} MB")
    table.add_column("Category")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for cat in cleanup_mod.CleanupCategory:
        if cat not in selected:
            continue
        totals = plan.totals_by_category.get(cat)
        if totals is None:
            continue
        size_mb = totals.bytes / (1024 * 1024)
        table.add_row(_CATEGORY_LABELS[cat], str(totals.file_count), f"{size_mb:.1f} MB")
    console.print(table)


def _print_files_summary(files: ReportFiles) -> None:
    if any([files.video, files.csv, files.fcpxml]):
        console.print("[bold]Wrote:[/]")
        for label, p in (("video", files.video), ("csv", files.csv), ("fcpxml", files.fcpxml)):
            if p:
                console.print(f"  {label:>6}: {p}")


if __name__ == "__main__":
    app()
