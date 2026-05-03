"""Lab: fixture management + ensemble evaluation + tuning.

Pure-functional core, used identically by the FastAPI ``/api/lab/*``
endpoints and the ``splitsmith lab`` CLI subcommands. The module owns
no global state -- every function takes the fixtures root + an optional
pre-loaded ``EnsembleRuntime`` so callers can amortise model loads.

The eval flow is deliberately split into two steps:

* ``run_eval`` walks every fixture, runs detector + CLAP + PANN + GBDT,
  builds the per-candidate ``EvalUniverse``, and scores it under the
  current ``EnsembleConfig``. Slow (model-bound) on first call.
* ``rescore_universe`` takes a cached ``EvalUniverse`` + a new config
  and recomputes only votes / consensus / metrics. Fast (<100 ms for
  the whole calibration set), which is what makes UI sliders feel live.

Run records are persisted under ``build/lab/runs/`` as deterministic
JSON so they're greppable, diffable, and Claude-Code-friendly.
"""

from .core import (
    REASON_VALUES,
    SUBCLASS_VALUES,
    CandidateLabel,
    EvalCandidate,
    EvalConfig,
    EvalFixture,
    EvalFixtureMetrics,
    EvalRun,
    EvalUniverse,
    FixtureRecord,
    PromoteRequest,
    RunSummary,
    apply_labels,
    list_fixtures,
    load_run,
    promote_stage_to_fixture,
    rescore_universe,
    run_eval,
    save_config_yaml,
    save_run,
)

__all__ = [
    "REASON_VALUES",
    "SUBCLASS_VALUES",
    "CandidateLabel",
    "EvalCandidate",
    "EvalConfig",
    "EvalFixture",
    "EvalFixtureMetrics",
    "EvalRun",
    "EvalUniverse",
    "FixtureRecord",
    "PromoteRequest",
    "RunSummary",
    "apply_labels",
    "list_fixtures",
    "load_run",
    "promote_stage_to_fixture",
    "rescore_universe",
    "run_eval",
    "save_config_yaml",
    "save_run",
]
