"""``splitsmith lab`` -- CLI mirror of the Lab UI surface.

Every command shells through to ``splitsmith.lab`` so the JSON it
prints is byte-identical to what the UI renders. Designed to be driven
by Claude Code: outputs JSON to stdout, writes deterministic run
records under ``build/lab/runs/`` for diff-based comparison.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import typer
import yaml

from . import beep_detect
from . import lab as lab_module
from .cross_align import CrossAlignError, align_secondary_to_primary
from .ensemble.api import detect_shots_ensemble, load_ensemble_runtime
from .fixture_schema import (
    AgcState,
    AudioSource,
    Camera,
    CameraMount,
    CameraPosition,
    probe_camera_metadata,
)
from .lab.snap_window import snap_anchor_shots

app = typer.Typer(help="Algorithm lab: fixtures, eval, tuning.", no_args_is_help=True)


def _emit(payload: Any, *, pretty: bool) -> None:
    indent = 2 if pretty else None
    sys.stdout.write(json.dumps(payload, indent=indent, sort_keys=True, ensure_ascii=True))
    sys.stdout.write("\n")


@app.command("fixtures")
def fixtures(
    fixtures_root: Path | None = typer.Option(
        None, "--fixtures-root", help="Override the fixtures directory (default: tests/fixtures/)."
    ),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """List audited fixtures available for eval."""
    records = lab_module.list_fixtures(fixtures_root)
    _emit([r.model_dump(mode="json") for r in records], pretty=pretty)


@app.command("eval")
def cmd_eval(
    slug: list[str] = typer.Option(
        None, "--slug", "-s", help="Restrict to specific fixture slugs (repeatable)."
    ),
    consensus: int = typer.Option(3, "--consensus", min=1, max=5),
    apriori_boost: float = typer.Option(1.0, "--apriori-boost", min=0.0),
    tolerance_ms: float = typer.Option(75.0, "--tolerance-ms", min=0.001),
    no_expected_rounds: bool = typer.Option(
        False,
        "--no-expected-rounds",
        help=(
            "Don't pass stage_rounds.expected into the ensemble "
            "(disables adaptive voter C + apriori boost)."
        ),
    ),
    save: bool = typer.Option(True, "--save/--no-save", help="Persist run under build/lab/runs/."),
    summary_only: bool = typer.Option(
        False,
        "--summary-only",
        help="Print only aggregate metrics + per-fixture P/R (no candidate-level detail).",
    ),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Run the ensemble against fixtures and report P/R per fixture."""
    cfg = lab_module.EvalConfig(
        consensus=consensus,
        apriori_boost=apriori_boost,
        tolerance_ms=tolerance_ms,
        use_expected_rounds=not no_expected_rounds,
    )
    runtime = load_ensemble_runtime()
    run = lab_module.run_eval(runtime, slugs=slug or None, config=cfg)
    if save:
        try:
            target = lab_module.save_run(run)
            sys.stderr.write(f"saved: {target}\n")
        except OSError as exc:
            sys.stderr.write(f"WARN: save_run failed: {exc}\n")
    if summary_only:
        payload = {
            "config_hash": run.config_hash,
            "summary": run.summary.model_dump(mode="json"),
            "per_fixture": [
                {
                    "slug": f.slug,
                    "n_truth": f.metrics.n_truth,
                    "n_kept": f.metrics.n_kept,
                    "precision": f.metrics.precision,
                    "recall": f.metrics.recall,
                    "f1": f.metrics.f1,
                }
                for f in run.universe.fixtures
            ],
        }
        _emit(payload, pretty=pretty)
    else:
        _emit(run.model_dump(mode="json"), pretty=pretty)


@app.command("rescore")
def rescore(
    universe_path: Path = typer.Option(
        ...,
        "--universe",
        help="Path to a saved run JSON (e.g. build/lab/runs/latest.json).",
    ),
    consensus: int = typer.Option(3, "--consensus", min=1, max=5),
    apriori_boost: float = typer.Option(1.0, "--apriori-boost", min=0.0),
    no_expected_rounds: bool = typer.Option(False, "--no-expected-rounds"),
    voter_a_floor: float | None = typer.Option(None, "--voter-a-floor"),
    voter_b_threshold: float | None = typer.Option(None, "--voter-b-threshold"),
    voter_c_threshold: float | None = typer.Option(None, "--voter-c-threshold"),
    voter_d_threshold: float | None = typer.Option(None, "--voter-d-threshold"),
    save: bool = typer.Option(True, "--save/--no-save"),
    summary_only: bool = typer.Option(False, "--summary-only"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Rescore a cached eval universe under a new tuning config (no model calls)."""
    prior = lab_module.load_run(universe_path)
    cfg = lab_module.EvalConfig(
        consensus=consensus,
        apriori_boost=apriori_boost,
        tolerance_ms=prior.universe.tolerance_ms,
        use_expected_rounds=not no_expected_rounds,
        voter_a_floor_override=voter_a_floor,
        voter_b_threshold_override=voter_b_threshold,
        voter_c_threshold_override=voter_c_threshold,
        voter_d_threshold_override=voter_d_threshold,
    )
    run = lab_module.rescore_universe(prior.universe, cfg)
    if save:
        try:
            target = lab_module.save_run(run)
            sys.stderr.write(f"saved: {target}\n")
        except OSError as exc:
            sys.stderr.write(f"WARN: save_run failed: {exc}\n")
    if summary_only:
        payload = {
            "config_hash": run.config_hash,
            "summary": run.summary.model_dump(mode="json"),
            "per_fixture": [
                {
                    "slug": f.slug,
                    "precision": f.metrics.precision,
                    "recall": f.metrics.recall,
                    "f1": f.metrics.f1,
                }
                for f in run.universe.fixtures
            ],
        }
        _emit(payload, pretty=pretty)
    else:
        _emit(run.model_dump(mode="json"), pretty=pretty)


@app.command("promote")
def promote(
    audit_json: Path = typer.Option(
        ..., "--audit-json", help="Path to <project>/audit/stage<N>.json."
    ),
    audit_wav: Path = typer.Option(..., "--audit-wav", help="Path to the stage's audit-clip WAV."),
    slug: str = typer.Option(
        ..., "--slug", help="Target fixture stem (e.g. stage-shots-foo-2026-stage4)."
    ),
    fixtures_root: Path | None = typer.Option(None, "--fixtures-root"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Copy an in-project audit JSON + WAV into tests/fixtures/ as a new fixture."""
    rec = lab_module.promote_stage_to_fixture(
        lab_module.PromoteRequest(
            audit_json_path=audit_json.expanduser().resolve(),
            audit_wav_path=audit_wav.expanduser().resolve(),
            fixture_slug=slug,
            fixtures_root=fixtures_root.expanduser().resolve() if fixtures_root else None,
            overwrite=overwrite,
        )
    )
    _emit(rec.model_dump(mode="json"), pretty=pretty)


@app.command("save-config")
def save_config(
    name: str = typer.Option(
        ..., "--name", help="Slug for the YAML file (configs/ensemble.<slug>.yaml)."
    ),
    universe_path: Path = typer.Option(
        Path("build/lab/runs/latest.json"),
        "--universe",
        help="Run JSON whose config + summary will be captured (defaults to latest run).",
    ),
    output_dir: Path = typer.Option(Path("configs"), "--output-dir"),
    note: str | None = typer.Option(
        None, "--note", help="Free-text note saved alongside provenance."
    ),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Capture a run's config + headline metrics as committable YAML."""
    if not universe_path.exists():
        raise typer.BadParameter(f"run JSON not found: {universe_path}")
    run = lab_module.load_run(universe_path)
    try:
        target = lab_module.save_config_yaml(
            run=run,
            name=name,
            output_dir=output_dir,
            note=note,
            overwrite=overwrite,
        )
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc)) from exc
    sys.stdout.write(str(target) + "\n")


@app.command("label")
def label(
    audit_json: Path = typer.Option(..., "--audit-json", help="Path to a fixture's audit JSON."),
    candidate: int = typer.Option(..., "--candidate", help="candidate_number to label."),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="One of REASON_VALUES; clear with --clear-reason.",
    ),
    subclass: str | None = typer.Option(
        None,
        "--subclass",
        help="One of SUBCLASS_VALUES (paper/steel/unknown); clear with --clear-subclass.",
    ),
    clear_reason: bool = typer.Option(False, "--clear-reason"),
    clear_subclass: bool = typer.Option(False, "--clear-subclass"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Patch one candidate's reason / subclass in a fixture audit JSON (issue #86)."""
    payload = lab_module.CandidateLabel(
        candidate_number=candidate,
        reason=None if clear_reason else reason,
        subclass=None if clear_subclass else subclass,
    )
    try:
        counts = lab_module.apply_labels(audit_json.expanduser().resolve(), [payload])
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit({"path": str(audit_json), "counts": counts}, pretty=pretty)


@app.command("load-config")
def load_config(
    path: Path = typer.Argument(..., exists=True, readable=True),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Print a saved YAML config (config + provenance) as JSON."""
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    _emit(payload, pretty=pretty)


def _extract_video_audio(video: Path, dest: Path, *, sample_rate: int) -> None:
    """Drop a mono float32 WAV at ``dest`` from ``video`` via ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise typer.BadParameter("ffmpeg binary not found on PATH")
    cmd = [
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
        str(sample_rate),
        "-vn",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise typer.BadParameter(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc


@app.command("measure-snap")
def measure_snap(
    anchor: Path = typer.Option(
        ...,
        "--anchor",
        help="Audited fixture JSON whose shot times are the ground truth.",
    ),
    secondary: Path | None = typer.Option(
        None,
        "--secondary",
        help="Secondary camera video (any ffmpeg container). Exclusive with --secondary-wav.",
    ),
    secondary_wav: Path | None = typer.Option(
        None,
        "--secondary-wav",
        help="Pre-extracted secondary WAV. Skips ffmpeg extraction. Exclusive with --secondary.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        help="Output CSV path. Sibling .meta.json is written alongside.",
    ),
    window_ms: float = typer.Option(
        200.0,
        "--window-ms",
        min=10.0,
        help="Snap half-window in milliseconds. Wide by design (issue #122).",
    ),
    min_spacing_ms: float = typer.Option(
        80.0,
        "--min-spacing-ms",
        min=0.0,
        help="Adjacent snaps closer than this are flagged 'min-spacing'.",
    ),
    sample_rate: int = typer.Option(
        48000,
        "--sample-rate",
        min=8000,
        help="Sample rate for the secondary's extracted audio.",
    ),
    anchor_wav: Path | None = typer.Option(
        None,
        "--anchor-wav",
        help="Override the anchor's sibling WAV (defaults to <anchor>.wav).",
    ),
) -> None:
    """Measure cross-camera snap displacement (issue #122).

    Loads an audited anchor, aligns a secondary camera's audio to the
    anchor's beep, runs ensemble detection on the secondary, and snaps
    each anchor shot to the nearest Voter-A-positive candidate within
    ``--window-ms``. Outputs per-shot displacement rows to CSV plus a
    sibling ``.meta.json`` with offset, alignment confidence, and snap
    aggregates.
    """
    if secondary is None and secondary_wav is None:
        raise typer.BadParameter("one of --secondary or --secondary-wav is required")
    if secondary is not None and secondary_wav is not None:
        raise typer.BadParameter("--secondary and --secondary-wav are mutually exclusive")

    anchor_path = anchor.expanduser().resolve()
    out_path = out.expanduser().resolve()

    if not anchor_path.exists():
        raise typer.BadParameter(f"anchor not found: {anchor_path}")

    audit = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor_beep = float(audit["beep_time"])
    anchor_shots = [float(s["time"]) for s in audit["shots"]]
    stage_time = float(audit["stage_time_seconds"])
    expected_rounds = audit.get("stage_rounds", {}).get("expected")

    wav_path = anchor_wav.expanduser().resolve() if anchor_wav else anchor_path.with_suffix(".wav")
    if not wav_path.exists():
        raise typer.BadParameter(f"anchor WAV not found: {wav_path}")

    primary_audio, primary_sr = beep_detect.load_audio(wav_path)

    if secondary_wav is not None:
        sec_wav_path = secondary_wav.expanduser().resolve()
        if not sec_wav_path.exists():
            raise typer.BadParameter(f"secondary WAV not found: {sec_wav_path}")
        secondary_audio, secondary_sr = beep_detect.load_audio(sec_wav_path)
    else:
        secondary_path = secondary.expanduser().resolve()  # type: ignore[union-attr]
        if not secondary_path.exists():
            raise typer.BadParameter(f"secondary not found: {secondary_path}")
        with tempfile.TemporaryDirectory(prefix="splitsmith-measure-snap-") as tmpdir:
            sec_wav_path = Path(tmpdir) / "secondary.wav"
            _extract_video_audio(secondary_path, sec_wav_path, sample_rate=sample_rate)
            secondary_audio, secondary_sr = beep_detect.load_audio(sec_wav_path)

    try:
        align = align_secondary_to_primary(
            primary_audio=primary_audio,
            primary_sr=primary_sr,
            primary_beep_time=anchor_beep,
            secondary_audio=secondary_audio,
            secondary_sr=secondary_sr,
        )
    except CrossAlignError as exc:
        raise typer.BadParameter(f"cross-align failed: {exc}") from exc

    if align.confidence < 1.5:
        sys.stderr.write(
            f"WARNING: cross-align confidence {align.confidence:.2f} is below 1.5 -- "
            f"alignment may be wrong (offset {align.lag_seconds:.3f}s). "
            "Displacement values will include the alignment error. "
            "Consider --anchor-wav / --secondary-wav override or manual offset.\n"
        )

    runtime = load_ensemble_runtime()
    ensemble = detect_shots_ensemble(
        secondary_audio,
        secondary_sr,
        beep_time=align.secondary_beep_time,
        stage_time=stage_time,
        runtime=runtime,
        expected_rounds=expected_rounds,
    )
    voter_a_candidates = [(c.time, c.confidence) for c in ensemble.candidates if c.vote_a >= 1]

    snaps = snap_anchor_shots(
        anchor_beep_time=anchor_beep,
        anchor_shots=anchor_shots,
        secondary_beep_time=align.secondary_beep_time,
        voter_a_candidates=voter_a_candidates,
        window_ms=window_ms,
        min_spacing_ms=min_spacing_ms,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "shot_number",
        "anchor_time",
        "predicted_time",
        "snapped_time",
        "displacement_ms",
        "snap_confidence",
        "time_since_beep_s",
        "sanity_flag",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in snaps:
            writer.writerow(r.model_dump())

    snapped = sum(1 for r in snaps if r.snapped_time is not None)
    flagged = sum(1 for r in snaps if r.sanity_flag != "")
    no_candidate = sum(1 for r in snaps if r.sanity_flag == "no-candidate")
    meta = {
        "anchor": str(anchor_path),
        "secondary": str(
            secondary_wav.expanduser().resolve()  # type: ignore[union-attr]
            if secondary_wav
            else secondary.expanduser().resolve()  # type: ignore[union-attr]
        ),
        "anchor_beep_time": anchor_beep,
        "secondary_beep_time": align.secondary_beep_time,
        "offset_seconds": align.secondary_beep_time - anchor_beep,
        "cross_align_confidence": align.confidence,
        "cross_align_peak_correlation": align.peak_correlation,
        "stage_time_seconds": stage_time,
        "expected_rounds": expected_rounds,
        "window_ms": window_ms,
        "min_spacing_ms": min_spacing_ms,
        "shot_count": len(anchor_shots),
        "voter_a_candidate_count": len(voter_a_candidates),
        "ensemble_candidate_count": len(ensemble.candidates),
        "snapped": snapped,
        "no_candidate": no_candidate,
        "sanity_flagged": flagged,
    }
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    sys.stdout.write(
        f"wrote {out_path} ({snapped}/{len(anchor_shots)} snapped, "
        f"{flagged} flagged, offset {meta['offset_seconds']:.3f}s, "
        f"align conf {align.confidence:.2f})\n"
    )


@app.command("promote-from-anchor")
def promote_from_anchor_cmd(
    anchor: Path = typer.Option(
        ..., "--anchor", help="Audited headcam fixture JSON (ground truth source)."
    ),
    secondary: Path | None = typer.Option(
        None,
        "--secondary",
        help="Secondary camera video file. Mutually exclusive with --secondary-wav.",
    ),
    secondary_wav: Path | None = typer.Option(
        None,
        "--secondary-wav",
        help="Pre-extracted secondary audio WAV. Mutually exclusive with --secondary.",
    ),
    slug: str = typer.Option(
        ..., "--slug", help="Target fixture slug (e.g. tallmilan-2026-stage5-phone)."
    ),
    camera_id: str = typer.Option(
        ...,
        "--camera-id",
        help=(
            "Device identifier for the secondary camera (e.g. 'apple-iphone17pro'). "
            "Run probe-camera on the video to get a suggestion."
        ),
    ),
    mount: CameraMount = typer.Option(
        ..., "--mount", help="Physical mount position of the secondary camera."
    ),
    position: CameraPosition = typer.Option(
        ..., "--position", help="Where the camera operator was relative to the shooter."
    ),
    audio_source: AudioSource = typer.Option(
        AudioSource.internal, "--audio-source", help="Audio capture device."
    ),
    agc_state: AgcState = typer.Option(
        AgcState.unknown, "--agc-state", help="AGC state of the secondary camera."
    ),
    fixtures_root: Path | None = typer.Option(
        None, "--fixtures-root", help="Override the fixtures directory."
    ),
    snap_window_ms: float = typer.Option(
        60.0, "--snap-window-ms", min=5.0, help="Snap half-window in ms (default from #122)."
    ),
    min_spacing_ms: float = typer.Option(
        80.0, "--min-spacing-ms", min=0.0, help="Min gap between adjacent snapped shots."
    ),
    sample_rate: int = typer.Option(48000, "--sample-rate", min=8000),
    anchor_wav: Path | None = typer.Option(
        None, "--anchor-wav", help="Override anchor sibling WAV."
    ),
    report_only: bool = typer.Option(
        False,
        "--report-only",
        help="Print snap diagnostics without writing any fixture files.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite"),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Promote a secondary camera recording to a fixture using a headcam anchor.

    Runs cross-alignment, ensemble shot detection on the secondary, and
    snaps each anchor shot to the nearest Voter-A-positive candidate.
    Outputs a pre-filled fixture JSON + sibling WAV + promotion report.
    The user reviews via ``splitsmith review`` before the fixture is
    considered audited.
    """
    if secondary is None and secondary_wav is None:
        raise typer.BadParameter("one of --secondary or --secondary-wav is required")
    if secondary is not None and secondary_wav is not None:
        raise typer.BadParameter("--secondary and --secondary-wav are mutually exclusive")

    anchor_path = anchor.expanduser().resolve()
    if not anchor_path.exists():
        raise typer.BadParameter(f"anchor not found: {anchor_path}")

    wav_path = anchor_wav.expanduser().resolve() if anchor_wav else anchor_path.with_suffix(".wav")
    if not wav_path.exists():
        raise typer.BadParameter(f"anchor WAV not found: {wav_path}")

    root = (fixtures_root or (anchor_path.parent)).resolve()
    target_json = root / f"{slug}.json"
    target_wav = root / f"{slug}.wav"
    if not report_only and target_json.exists() and not overwrite:
        raise typer.BadParameter(
            f"fixture already exists: {target_json}  (use --overwrite to replace)"
        )

    anchor_data = json.loads(anchor_path.read_text(encoding="utf-8"))
    primary_audio, primary_sr = beep_detect.load_audio(wav_path)

    # Probe secondary for make/model; build Camera.
    make: str | None = None
    model: str | None = None
    sec_sample_rate = sample_rate
    if secondary is not None:
        secondary_path = secondary.expanduser().resolve()
        if not secondary_path.exists():
            raise typer.BadParameter(f"secondary not found: {secondary_path}")
        probe = probe_camera_metadata(secondary_path)
        make = probe.make
        model = probe.model
        if probe.sample_rate:
            sec_sample_rate = probe.sample_rate
        secondary_source_desc = str(secondary_path)
    else:
        secondary_source_desc = str(secondary_wav.expanduser().resolve())  # type: ignore[union-attr]

    camera = Camera(
        id=camera_id,
        make=make,
        model=model,
        mount=mount,
        position=position,
        audio_source=audio_source,
        agc_state=agc_state,
        sample_rate=sec_sample_rate,
    )

    # Load secondary audio.
    if secondary_wav is not None:
        sec_wav_path = secondary_wav.expanduser().resolve()
        if not sec_wav_path.exists():
            raise typer.BadParameter(f"secondary WAV not found: {sec_wav_path}")
        secondary_audio, secondary_sr = beep_detect.load_audio(sec_wav_path)
        wav_source_path = sec_wav_path
    else:
        sec_wav_tmp = None
        try:
            _tmpdir = tempfile.mkdtemp(prefix="splitsmith-promote-")
            sec_wav_tmp = Path(_tmpdir) / "secondary.wav"
            _extract_video_audio(secondary_path, sec_wav_tmp, sample_rate=sec_sample_rate)
            secondary_audio, secondary_sr = beep_detect.load_audio(sec_wav_tmp)
            wav_source_path = sec_wav_tmp
        except Exception:
            if sec_wav_tmp and sec_wav_tmp.parent.exists():
                shutil.rmtree(sec_wav_tmp.parent, ignore_errors=True)
            raise

    try:
        runtime = load_ensemble_runtime()
        result = lab_module.promote_from_anchor(
            lab_module.PromoteFromAnchorRequest(
                anchor_data=anchor_data,
                primary_audio=primary_audio,
                primary_sr=primary_sr,
                secondary_audio=secondary_audio,
                secondary_sr=secondary_sr,
                secondary_source_desc=secondary_source_desc,
                camera=camera,
                slug=slug,
                snap_window_ms=snap_window_ms,
                min_spacing_ms=min_spacing_ms,
            ),
            runtime=runtime,
        )
    finally:
        if secondary is not None and sec_wav_tmp is not None:
            shutil.rmtree(sec_wav_tmp.parent, ignore_errors=True)

    for w in result.warnings:
        sys.stderr.write(f"WARNING: {w}\n")

    if report_only:
        _emit(result.promotion_report, pretty=pretty)
        return

    # Write fixture JSON.
    root.mkdir(parents=True, exist_ok=True)
    tmp_json = target_json.with_suffix(".json.tmp")
    tmp_json.write_text(
        json.dumps(result.fixture_data, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    tmp_json.replace(target_json)

    # Copy secondary WAV alongside fixture.
    shutil.copy2(wav_source_path, target_wav)

    # Write candidates CSV (same format as primary CLI).
    csv_path = root / f"{slug}-candidates.csv"
    beep_t = result.secondary_beep_time
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
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
                "vote_a",
                "vote_b",
                "vote_c",
                "vote_d",
                "kept",
            ]
        )
        prev_t = beep_t
        stage_end_ms = anchor_data.get("stage_time_seconds", 60) * 1000
        for c in result.ensemble_result.candidates:
            split_ms = round((c.time - prev_t) * 1000, 1)
            in_win = "Y" if c.ms_after_beep <= stage_end_ms + 1000 else "N"
            echo = "Y" if split_ms < 150 else ""
            writer.writerow(
                [
                    "",
                    c.candidate_number,
                    c.time,
                    c.time,
                    c.ms_after_beep,
                    split_ms,
                    c.peak_amplitude,
                    c.confidence,
                    in_win,
                    echo,
                    c.vote_a,
                    c.vote_b,
                    c.vote_c,
                    c.vote_d,
                    "Y" if c.kept else "",
                ]
            )
            prev_t = c.time

    # Write promotion report.
    report_path = root / f"{slug}-promotion-report.json"
    report_path.write_text(
        json.dumps(result.promotion_report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    counts = result.promotion_report["counts"]
    sys.stdout.write(
        f"wrote {target_json}\n"
        f"  snapped: {counts['snapped']}/{counts['anchor_shots']}  "
        f"missed: {counts['missed']}  "
        f"offset: {result.align.lag_seconds:.3f}s  "
        f"align conf: {result.align.confidence:.2f}"
        + (
            f"  drift: {result.drift_ms_per_minute:.1f}ms/min"
            if result.drift_ms_per_minute is not None
            else ""
        )
        + "\n"
        f"  candidates csv: {csv_path}\n"
        f"  promotion report: {report_path}\n"
    )


@app.command("probe-camera")
def probe_camera(
    video: Path = typer.Argument(..., help="Video file to probe."),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Probe a video file for camera make/model and audio metadata.

    Useful for finding the suggested --camera-id before running
    promote-from-anchor.
    """
    video_path = video.expanduser().resolve()
    if not video_path.exists():
        raise typer.BadParameter(f"not found: {video_path}")
    from .fixture_schema import probe_camera_metadata as _probe

    result = _probe(video_path)
    _emit(result.model_dump(mode="json"), pretty=pretty)
