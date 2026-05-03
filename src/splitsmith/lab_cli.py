"""``splitsmith lab`` -- CLI mirror of the Lab UI surface.

Every command shells through to ``splitsmith.lab`` so the JSON it
prints is byte-identical to what the UI renders. Designed to be driven
by Claude Code: outputs JSON to stdout, writes deterministic run
records under ``build/lab/runs/`` for diff-based comparison.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
import yaml

from . import lab as lab_module
from .ensemble.api import load_ensemble_runtime

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


@app.command("load-config")
def load_config(
    path: Path = typer.Argument(..., exists=True, readable=True),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty"),
) -> None:
    """Print a saved YAML config (config + provenance) as JSON."""
    with path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    _emit(payload, pretty=pretty)
