"""What the shipped detection models were built from: the lab's Models page.

One read-only picture of the corpus and the models it feeds, assembled
from files the build already writes, never from a new artifact:

* ``ensemble_calibration.json`` -- the shipped voter C heads, one per
  camera class, with the fixtures each was calibrated on, its thresholds
  and its cross-validated metrics;
* ``build/ensemble_heldout/report.json`` -- the same heads scored under
  the three splits ``scripts/build_ensemble_artifacts.py`` runs
  (by candidate, grouped by match + stage, leave one match out); a
  checkout that never ran the build has none;
* ``tests/fixtures/beep_calibration/ranker_report.json`` and its
  ``manifest.yaml`` -- the beep ranker's corpus and its out-of-fold picks;
* git history of the calibration file -- every version that shipped.

:func:`build_model_map` is pure: it takes the parsed documents and the
fixture descriptors and returns the page's payload. :func:`load_model_map`
does the reading.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from ..ensemble.fixtures import FIXTURES_DIR, WRONG_CLIP_FIXTURES, Fixture, all_fixtures

REPO_ROOT: Path = FIXTURES_DIR.parents[1]
CALIBRATION_REPO_PATH = "src/splitsmith/data/ensemble_calibration.json"
HELDOUT_REPORT: Path = REPO_ROOT / "build" / "ensemble_heldout" / "report.json"
RANKER_DIR: Path = FIXTURES_DIR / "beep_calibration"
BUILD_SCRIPT: Path = REPO_ROOT / "scripts" / "build_ensemble_artifacts.py"

CameraClass = Literal["headcam", "handheld"]
HeldOut = Literal["right", "wrong", "unreachable"]
SPLITS: tuple[str, ...] = ("stratified", "grouped", "lomo")


class BeepRankerUse(BaseModel):
    """One fixture's place in the beep ranker's corpus."""

    tags: list[str]
    camera_kind: str | None = None
    # How the shipped ranker did on this fixture while its match was held
    # out: ``right`` (top pick is the beep), ``wrong``, or ``unreachable``
    # (the beep is not among the candidates). ``None``: not scored.
    held_out: HeldOut | None = None


class ModelMapFixture(BaseModel):
    stem: str
    match: str | None
    stage: int | None
    shooter_id: str | None
    camera_class: CameraClass
    camera: str | None
    n_shots: int
    expected_rounds: int | None
    has_wav: bool
    review_status: str
    superseded_by: str | None
    wrong_clip: bool
    # The voter C head this fixture calibrated in the shipped build, if any.
    voter_c: CameraClass | None
    # Fold keys of the two grouped splits: every recording of one stage
    # shares ``stage_group``; leave one match out folds by ``match_group``.
    match_group: str | None
    stage_group: str | None
    beep_ranker: BeepRankerUse | None
    # This model's voter C on this fixture while its match was left out, at
    # the head's threshold. ``None``: the build recorded no labels (builds
    # before the bundle work) or the fixture did not calibrate the model.
    heldout_lomo: FixtureHeldOut | None = None


class FixtureHeldOut(BaseModel):
    tp: int
    fp: int
    fn: int


class ModelSummary(BaseModel):
    """One model the page can show: the shipped one, or a bundle under
    ``build/models`` (``ensemble.model_bundle``)."""

    id: str
    name: str
    version: str
    channel: str
    built_at: str | None
    description: str | None = None
    n_fixtures: dict[str, int] = {}
    selection: str | None = None


class SplitMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


class HeldoutSplit(BaseModel):
    split: str
    at_shipped_threshold: SplitMetrics
    own_threshold: float | None
    at_own_threshold: SplitMetrics | None


class VoterCHead(BaseModel):
    model_set: str
    camera_class: CameraClass
    n_fixtures: int
    n_candidates: int
    n_positives: int
    voter_a_floor: float | None
    voter_b_threshold: float | None
    voter_c_threshold: float | None
    cv: SplitMetrics | None
    onnx: str | None
    onnx_bytes: int | None
    heldout: list[HeldoutSplit]
    heldout_matches: list[str]
    heldout_stages: int | None
    heldout_candidates: int | None
    # Leave one match out, each match scored by the model that never saw it.
    lomo_by_match: dict[str, SplitMetrics] = {}


class ModelArtifact(BaseModel):
    name: str
    filename: str
    sha256: str
    size_bytes: int


class EnsembleModel(BaseModel):
    built_at: str | None
    target_recall: float | None
    tolerance_ms: float | None
    feature_dim: int | None
    default_camera_class: str | None
    heads: list[VoterCHead]
    hyperparameters: dict[str, Any]
    splits: dict[str, str]
    heldout_built_at: str | None
    clap_prompts: list[str]
    clap_shot_prompts: list[str]
    amp_floor_by_camera_model: dict[str, float]
    artifacts: list[ModelArtifact]


class RankerFeature(BaseModel):
    name: str
    lr_coef: float | None
    gbdt_importance: float | None


class RankerScore(BaseModel):
    name: str
    n: int
    top1: int
    top1_pct: float


class BeepRankerModel(BaseModel):
    shipped_version: str | None
    shipped_kind: str
    winner: str | None
    n_fixtures: int
    n_reachable: int
    folds: list[str]
    features: list[RankerFeature]
    scores: list[RankerScore]
    by_tag: dict[str, RankerScore]


class ModelVersion(BaseModel):
    sha: str
    date: str
    subject: str
    built_at: str | None
    # The set the recall and precision figures are read from.
    model_set: str
    n_fixtures: dict[str, int]
    recall: dict[str, float]
    precision: dict[str, float]
    added: list[str]
    removed: list[str]


class ModelMap(BaseModel):
    fixtures: list[ModelMapFixture]
    ensemble: EnsembleModel
    beep_ranker: BeepRankerModel | None
    history: list[ModelVersion]
    model: ModelSummary | None = None
    models: list[ModelSummary] = []


# -- pure assembly ---------------------------------------------------------


def _metrics(block: Mapping[str, Any] | None) -> SplitMetrics | None:
    if not block:
        return None
    try:
        return SplitMetrics(
            precision=float(block["precision"]),
            recall=float(block["recall"]),
            f1=float(block["f1"]),
            tp=int(block["tp"]),
            fp=int(block["fp"]),
            fn=int(block["fn"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _cv_metrics(block: Mapping[str, Any] | None) -> SplitMetrics | None:
    if not block:
        return None
    return _metrics(
        {
            "precision": block.get("voter_c_precision_cv"),
            "recall": block.get("voter_c_recall_cv"),
            "f1": block.get("voter_c_f1_cv"),
            "tp": block.get("voter_c_tp"),
            "fp": block.get("voter_c_fp"),
            "fn": block.get("voter_c_fn"),
        }
    )


LEGACY_MODEL_SET = "legacy"


def model_set_names(cal: Mapping[str, Any]) -> list[str]:
    """The calibration's model sets, the default set first. A flat
    calibration from before model sets (git history) is one set, ``legacy``."""
    sets = cal.get("model_sets")
    if not isinstance(sets, Mapping):
        return [LEGACY_MODEL_SET]
    default = cal.get("default_model_set")
    return sorted(sets, key=lambda n: (n != default, n))


def model_set_block(cal: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """The one place the lab reads a set's per-set fields (thresholds,
    graphs, metrics, feature width). ``legacy`` is a flat calibration's
    top level; an unknown name raises ``KeyError``."""
    sets = cal.get("model_sets")
    if not isinstance(sets, Mapping):
        if name != LEGACY_MODEL_SET:
            raise KeyError(name)
        return cal
    return sets[name]


def _primary_set(cal: Mapping[str, Any]) -> str:
    return model_set_names(cal)[0]


def _heldout_block(heldout: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """A set's held-out report: under ``model_sets`` in a report written with
    model sets, the top level in one written before them."""
    report = heldout or {}
    sets = report.get("model_sets")
    if isinstance(sets, Mapping):
        return sets.get(name) or {}
    return report if name == LEGACY_MODEL_SET else {}


def calibration_fixtures_by_class(cal: Mapping[str, Any]) -> dict[str, list[str]]:
    """Each head's fixture list (every set shares one, so the default set's);
    a calibration from before the per-class heads lists one corpus, reported
    under ``all``."""
    by_class = model_set_block(cal, _primary_set(cal)).get("thresholds_by_camera_class") or {}
    out = {cls: list(block.get("calibration_fixtures") or []) for cls, block in by_class.items()}
    if not out and cal.get("calibration_fixtures"):
        out["all"] = list(cal["calibration_fixtures"])
    return out


def _heads(
    cal: Mapping[str, Any], heldout: Mapping[str, Any] | None, artifact_sizes: Mapping[str, int]
) -> list[VoterCHead]:
    heads: list[VoterCHead] = []
    for set_name in model_set_names(cal):
        heads += _set_heads(set_name, cal, heldout, artifact_sizes)
    return heads


def _set_heads(
    set_name: str,
    cal: Mapping[str, Any],
    heldout: Mapping[str, Any] | None,
    artifact_sizes: Mapping[str, int],
) -> list[VoterCHead]:
    block_set = model_set_block(cal, set_name)
    by_class = block_set.get("thresholds_by_camera_class") or {}
    metrics = block_set.get("voter_c_metrics_by_camera_class") or {}
    onnx = block_set.get("voter_c_onnx_artifacts") or {}
    held = _heldout_block(heldout, set_name).get("by_camera_class") or {}
    heads: list[VoterCHead] = []
    for cls in ("headcam", "handheld"):
        block = by_class.get(cls)
        if block is None:
            continue
        h = held.get(cls) or {}
        splits: list[HeldoutSplit] = []
        for name in SPLITS:
            sb = h.get(name) or {}
            shipped = _metrics(sb.get("at_shipped_threshold"))
            if shipped is None:
                continue
            splits.append(
                HeldoutSplit(
                    split=name,
                    at_shipped_threshold=shipped,
                    own_threshold=sb.get("own_threshold"),
                    at_own_threshold=_metrics(sb.get("at_own_threshold")),
                )
            )
        heads.append(
            VoterCHead(
                model_set=set_name,
                camera_class=cls,
                n_fixtures=len(block.get("calibration_fixtures") or []),
                n_candidates=int(block.get("n_calibration_candidates") or 0),
                n_positives=int(block.get("n_calibration_positives") or 0),
                voter_a_floor=block.get("voter_a_floor"),
                voter_b_threshold=block.get("voter_b_threshold"),
                voter_c_threshold=block.get("voter_c_threshold"),
                cv=_cv_metrics(metrics.get(cls)),
                onnx=onnx.get(cls),
                onnx_bytes=artifact_sizes.get(onnx.get(cls) or ""),
                heldout=splits,
                heldout_matches=list(h.get("matches") or []),
                heldout_stages=h.get("n_stages"),
                heldout_candidates=h.get("n_candidates"),
                lomo_by_match={
                    m: met
                    for m, b in (h.get("lomo_by_match") or {}).items()
                    if (met := _metrics(b)) is not None
                },
            )
        )
    return heads


def fixture_heldout(
    oof_rows: Iterable[Mapping[str, Any]], thresholds: Mapping[str, float]
) -> dict[str, FixtureHeldOut]:
    """Each fixture's leave-one-match-out outcome at its head's threshold.

    ``oof_rows`` are the build's ``voter_c_oof.parquet`` rows; ``thresholds``
    maps each fixture to the voter C threshold of the head it calibrated.
    Rows without a label (builds before labels were recorded) or a score
    (an unscorable fold) are left out, so such a build yields nothing.
    """
    out: dict[str, list[int]] = {}
    for r in oof_rows:
        stem, label, score = r.get("fixture"), r.get("label"), r.get("score_c_lomo")
        if stem not in thresholds or label is None or score is None or score != score:
            continue
        kept = score >= thresholds[stem]
        c = out.setdefault(stem, [0, 0, 0])
        if kept and label == 1:
            c[0] += 1
        elif kept:
            c[1] += 1
        elif label == 1:
            c[2] += 1
    return {s: FixtureHeldOut(tp=c[0], fp=c[1], fn=c[2]) for s, c in out.items()}


def shipped_ranker_kind(version: str | None) -> str:
    """``beep-ranker-lr-<hash>`` -> ``lr``; the LR ships unless told otherwise."""
    m = re.match(r"beep-ranker-([a-z]+)-", version or "")
    return m.group(1) if m else "lr"


def _score(name: str, block: Mapping[str, Any] | None) -> RankerScore | None:
    if not block:
        return None
    try:
        return RankerScore(
            name=name, n=int(block["n"]), top1=int(block["top1"]), top1_pct=float(block["top1_pct"])
        )
    except (KeyError, TypeError, ValueError):
        return None


def _ranker(report: Mapping[str, Any] | None, shipped_version: str | None) -> BeepRankerModel | None:
    if not report:
        return None
    models = report.get("models") or {}
    lr_fit = (models.get("lr") or {}).get("final_fit") or {}
    gb_fit = (models.get("gbdt") or {}).get("final_fit") or {}
    names = list(report.get("features") or lr_fit.get("features") or [])
    coefs = lr_fit.get("coef") or []
    imps = gb_fit.get("importances") or []
    features = [
        RankerFeature(
            name=n,
            lr_coef=coefs[i] if i < len(coefs) else None,
            gbdt_importance=imps[i] if i < len(imps) else None,
        )
        for i, n in enumerate(names)
    ]
    scores = [
        s
        for s in (
            _score("heuristic", (report.get("heuristic") or {}).get("all")),
            _score("lr", (models.get("lr") or {}).get("all")),
            _score("gbdt", (models.get("gbdt") or {}).get("all")),
        )
        if s is not None
    ]
    kind = shipped_ranker_kind(shipped_version)
    tags = (models.get(kind) or {}).get("by_tag") or {}
    by_tag = {t: s for t, b in tags.items() if (s := _score(t, b)) is not None}
    return BeepRankerModel(
        shipped_version=shipped_version,
        shipped_kind=kind,
        winner=report.get("winner"),
        n_fixtures=int(report.get("fixtures") or 0),
        n_reachable=int(report.get("reachable") or 0),
        folds=list(report.get("groups") or []),
        features=features,
        scores=scores,
        by_tag=by_tag,
    )


def ranker_held_out(oof: Mapping[str, Any] | None) -> HeldOut | None:
    """The shipped ranker's out-of-fold verdict on one fixture."""
    if not oof:
        return None
    probs = oof.get("probs") or []
    positive = oof.get("positive") or []
    if not probs or len(probs) != len(positive):
        return None
    if not any(positive):
        return "unreachable"
    best = max(range(len(probs)), key=lambda i: probs[i])
    return "right" if positive[best] else "wrong"


def _event_groups(event_id: str | None, fx: Fixture) -> tuple[str | None, str | None]:
    """``(match, match:stage)`` exactly as the build groups its folds."""
    if event_id and event_id.count(":") == 2:
        match, stage, _shooter = event_id.split(":")
        return match, f"{match}:{stage}"
    if fx.match is None:
        return None, None
    return fx.match, f"{fx.match}:{fx.stage_number}"


def _camera(fx: Fixture) -> str | None:
    parts = [p for p in (fx.camera_make, fx.camera_model) if p]
    return " ".join(parts) or fx.camera_id


def build_model_map(
    *,
    fixtures: Iterable[Fixture],
    event_ids: Mapping[str, str | None],
    calibration: Mapping[str, Any],
    heldout: Mapping[str, Any] | None,
    ranker_report: Mapping[str, Any] | None,
    ranker_manifest: Mapping[str, Any] | None,
    ranker_version: str | None,
    hyperparameters: Mapping[str, Any],
    artifact_sizes: Mapping[str, int],
    history: list[ModelVersion],
    heldout_by_fixture: Mapping[str, FixtureHeldOut] | None = None,
    model: ModelSummary | None = None,
    models: list[ModelSummary] | None = None,
) -> ModelMap:
    by_class = calibration_fixtures_by_class(calibration)
    head_of: dict[str, CameraClass] = {}
    for cls in ("headcam", "handheld"):
        for stem in by_class.get(cls, []):
            head_of[stem] = cls  # type: ignore[assignment]
    manifest_rows = {
        r["stem"]: r
        for r in ((ranker_manifest or {}).get("fixtures") or [])
        if isinstance(r, dict) and r.get("stem")
    }
    kind = shipped_ranker_kind(ranker_version)
    oof = (((ranker_report or {}).get("models") or {}).get(kind) or {}).get("oof") or {}

    rows: list[ModelMapFixture] = []
    for fx in fixtures:
        match_group, stage_group = _event_groups(event_ids.get(fx.stem), fx)
        man = manifest_rows.get(fx.stem)
        rows.append(
            ModelMapFixture(
                stem=fx.stem,
                match=fx.match,
                stage=fx.stage_number,
                shooter_id=fx.shooter_id,
                camera_class=fx.camera_class,  # type: ignore[arg-type]
                camera=_camera(fx),
                n_shots=fx.n_audited_shots,
                expected_rounds=fx.expected_rounds,
                has_wav=fx.has_wav,
                review_status=fx.review_status,
                superseded_by=fx.superseded_by,
                wrong_clip=fx.stem in WRONG_CLIP_FIXTURES,
                voter_c=head_of.get(fx.stem),
                match_group=match_group,
                stage_group=stage_group,
                beep_ranker=(
                    BeepRankerUse(
                        tags=list(man.get("tags") or []),
                        camera_kind=man.get("camera_kind"),
                        held_out=ranker_held_out(oof.get(fx.stem)),
                    )
                    if man is not None
                    else None
                ),
                heldout_lomo=(heldout_by_fixture or {}).get(fx.stem),
            )
        )

    artifacts = [
        ModelArtifact(
            name=name,
            filename=str(a.get("filename") or name),
            sha256=str(a.get("sha256") or ""),
            size_bytes=int(a.get("size_bytes") or 0),
        )
        for name, a in (calibration.get("model_artifacts") or {}).items()
        if isinstance(a, Mapping)
    ]
    ensemble = EnsembleModel(
        built_at=calibration.get("built_at"),
        target_recall=calibration.get("voter_c_target_recall"),
        tolerance_ms=calibration.get("tolerance_ms"),
        feature_dim=model_set_block(calibration, _primary_set(calibration)).get("voter_c_feature_dim"),
        default_camera_class=calibration.get("default_camera_class"),
        heads=_heads(calibration, heldout, artifact_sizes),
        hyperparameters=dict(hyperparameters),
        splits=dict((heldout or {}).get("splits") or {}),
        heldout_built_at=(heldout or {}).get("built_at"),
        clap_prompts=list(calibration.get("clap_prompts") or []),
        clap_shot_prompts=list(calibration.get("clap_prompts_shot") or []),
        amp_floor_by_camera_model={
            k: float(v) for k, v in (calibration.get("amp_floor_by_camera_model") or {}).items()
        },
        artifacts=artifacts,
    )
    return ModelMap(
        fixtures=rows,
        ensemble=ensemble,
        beep_ranker=_ranker(ranker_report, ranker_version),
        history=history,
        model=model,
        models=list(models or []),
    )


def version_from(
    sha: str, date: str, subject: str, cal: Mapping[str, Any], previous: set[str] | None
) -> ModelVersion:
    """One shipped calibration, with the fixtures it added and dropped
    against the version before it (``previous``; ``None`` for the first)."""
    stems = {s for v in calibration_fixtures_by_class(cal).values() for s in v}
    set_name = _primary_set(cal)
    metrics = model_set_block(cal, set_name).get("voter_c_metrics_by_camera_class") or {}
    return ModelVersion(
        sha=sha,
        date=date,
        subject=subject,
        built_at=cal.get("built_at"),
        model_set=set_name,
        n_fixtures={k: len(v) for k, v in calibration_fixtures_by_class(cal).items()},
        recall={
            k: float(m["voter_c_recall_cv"])
            for k, m in metrics.items()
            if m.get("voter_c_recall_cv") is not None
        },
        precision={
            k: float(m["voter_c_precision_cv"])
            for k, m in metrics.items()
            if m.get("voter_c_precision_cv") is not None
        },
        added=sorted(stems - previous) if previous is not None else sorted(stems),
        removed=sorted(previous - stems) if previous is not None else [],
    )


_GBDT_RE = re.compile(
    r"def _new_voter_c_gbdt\(\)[^:]*:\s*return\s+GradientBoostingClassifier\((?P<args>[^)]*)\)"
)


def gbdt_hyperparameters(script_source: str) -> dict[str, Any]:
    """The voter C GBDT's arguments, read from the build script's one
    constructor (the calibration file does not record them)."""
    m = _GBDT_RE.search(script_source)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for part in m.group("args").split(","):
        if "=" not in part:
            continue
        key, value = (s.strip() for s in part.split("=", 1))
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


# -- reading ---------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git(repo: Path, *args: str) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


_HISTORY_CACHE: dict[str, list[ModelVersion]] = {}


def calibration_history(repo: Path = REPO_ROOT, path: str = CALIBRATION_REPO_PATH) -> list[ModelVersion]:
    """Every committed version of the calibration file, oldest first.
    Empty outside a git checkout (an installed app)."""
    head = _git(repo, "rev-parse", "HEAD")
    if head is None:
        return []
    key = f"{repo}:{head.strip()}:{path}"
    if key in _HISTORY_CACHE:
        return _HISTORY_CACHE[key]
    log = _git(repo, "log", "--reverse", "--format=%H%x09%ad%x09%s", "--date=short", "--", path) or ""
    versions: list[ModelVersion] = []
    previous: set[str] | None = None
    for line in log.splitlines():
        try:
            sha, date, subject = line.split("\t", 2)
        except ValueError:
            continue
        text = _git(repo, "show", f"{sha}:{path}")
        if text is None:
            continue
        try:
            cal = json.loads(text)
        except json.JSONDecodeError:
            continue
        versions.append(version_from(sha[:8], date, subject, cal, previous))
        previous = {s for vals in calibration_fixtures_by_class(cal).values() for s in vals}
    _HISTORY_CACHE[key] = versions
    return versions


SHIPPED_ID = "shipped"


def _summary(model_id: str, calibration: Mapping[str, Any], manifest: Any | None) -> ModelSummary:
    built = calibration.get("built_at")
    counts = {k: len(v) for k, v in calibration_fixtures_by_class(calibration).items()}
    if manifest is None:
        return ModelSummary(
            id=model_id,
            name="Shipped",
            version=f"v{built[:10].replace('-', '.')}" if built else "unknown",
            channel="shipped",
            built_at=built,
            description="The model in src/splitsmith/data that the app runs.",
            n_fixtures=counts,
        )
    sel = manifest.selection
    selection = (
        f"matches: {', '.join(sel.matches)}" if sel.matches else f"{len(sel.fixtures or [])} named fixtures"
    ) + (", reviewed only" if sel.reviewed_only else "")
    return ModelSummary(
        id=model_id,
        name=manifest.name,
        version=manifest.version,
        channel=manifest.channel,
        built_at=manifest.built_at,
        description=manifest.description,
        n_fixtures=counts,
        selection=selection,
    )


def model_sources(shipped_calibration: Path) -> dict[str, tuple[Path, Path, Any | None]]:
    """Every model the page can show: id -> (calibration file, held-out
    folder, bundle manifest or ``None`` for the shipped model). Bundle ids are
    their folder names under ``build/models``; nothing else resolves."""
    from ..ensemble import model_bundle as mb

    out: dict[str, tuple[Path, Path, Any | None]] = {
        SHIPPED_ID: (shipped_calibration, HELDOUT_REPORT.parent, None)
    }
    for folder, manifest in mb.list_bundles():
        if folder.name != SHIPPED_ID:
            out[folder.name] = (folder / mb.CALIBRATION_NAME, folder / "heldout", manifest)
    return out


def _oof_rows(heldout_dir: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError:  # dev-only dependency; the page still renders without it
        return []
    try:
        return pq.read_table(heldout_dir / "voter_c_oof.parquet").to_pylist()
    except (OSError, ValueError):
        return []


def load_model_map(shipped_calibration: Path, model_id: str = SHIPPED_ID) -> ModelMap:
    """Read every source for one model and assemble the page's payload.
    Raises ``KeyError`` for an id :func:`model_sources` does not list."""
    from ..config import BeepRankerConfig

    sources = model_sources(shipped_calibration)
    calibration_path, heldout_dir, manifest = sources[model_id]
    summaries = [_summary(i, _read_json(p) or {}, m) for i, (p, _h, m) in sources.items()]
    calibration = _read_json(calibration_path) or {}
    thresholds = {
        stem: float(block["voter_c_threshold"])
        for block in (
            model_set_block(calibration, _primary_set(calibration)).get("thresholds_by_camera_class") or {}
        ).values()
        if block.get("voter_c_threshold") is not None
        for stem in block.get("calibration_fixtures") or []
    }
    fixtures = all_fixtures()
    event_ids = {
        fx.stem: (_read_json(FIXTURES_DIR / f"{fx.stem}.json") or {}).get("event_id") for fx in fixtures
    }
    try:
        manifest = yaml.safe_load((RANKER_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        manifest = None
    try:
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        script = ""
    sizes: dict[str, int] = {}
    graphs = {
        g
        for set_name in model_set_names(calibration)
        for g in (model_set_block(calibration, set_name).get("voter_c_onnx_artifacts") or {}).values()
    }
    for name in sorted(graphs):
        try:
            sizes[name] = (calibration_path.parent / name).stat().st_size
        except OSError:
            pass
    return build_model_map(
        fixtures=fixtures,
        event_ids=event_ids,
        calibration=calibration,
        heldout=_read_json(heldout_dir / "report.json"),
        heldout_by_fixture=fixture_heldout(_oof_rows(heldout_dir), thresholds),
        model=next(s for s in summaries if s.id == model_id),
        models=summaries,
        ranker_report=_read_json(RANKER_DIR / "ranker_report.json"),
        ranker_manifest=manifest if isinstance(manifest, dict) else None,
        ranker_version=BeepRankerConfig().model_version,
        hyperparameters=gbdt_hyperparameters(script),
        artifact_sizes=sizes,
        history=calibration_history(),
    )
