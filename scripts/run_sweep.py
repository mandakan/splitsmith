"""Replay the ensemble voters over ``signals.parquet`` for a parameter grid.

Reads the per-candidate signal table written by ``build_sweep_signals.py``
and evaluates *every* combination of the parameters in the supplied YAML
grid. Because the underlying raw signals (clap_diff, gunshot_prob,
score_c, ...) don't change as thresholds or consensus shift, this script
is pure numpy and finishes in seconds for grids with thousands of points.

Output: ``build/sweeps/runs.parquet`` -- one row per
``(run_id, param_combo, fixture)`` plus an ``__all__`` aggregate row per
combo and per camera class. ``run_id`` is unique per invocation so
multiple sweeps coexist in the same file (filter on it before plotting).

Tunable parameters the grid can vary (default unchanged when omitted):

* ``consensus`` (int 1..4)
* ``c_required`` (bool)
* ``e_required`` (bool)
* ``enable_voter_e`` (bool)
* ``e_audio_strong_min_votes`` (int or null)
* ``apriori_boost`` (float)
* ``voter_a_floor`` (float)             -- absolute override
* ``voter_b_threshold`` (float)         -- absolute override
* ``voter_c_threshold`` (float)         -- absolute override
* ``voter_e_threshold`` (float)         -- absolute override
* ``voter_c_mode`` ("global" / "adaptive")
* ``voter_c_slack_min`` (int)
* ``voter_c_slack_frac`` (float)
* ``voter_c_confidence_override`` (float or null)
* ``use_expected_rounds`` (bool)        -- when false, ignore stage prior
* ``camera_class_filter`` ("all" / "headcam" / "handheld")
* ``tolerance_ms_override`` (informational; label is fixed by signals build)

The shipped thresholds from ``ensemble_calibration.json`` are the
default; absolute overrides win when set, sweep keys ``*_offset``
also accepted (added to the calibrated baseline for a class).

Run:
    uv run python scripts/run_sweep.py --grid scripts/sweep_grids/consensus_x_floor.yaml
    uv run python scripts/run_sweep.py --grid - <<<'{ "consensus": [2,3,4] }'
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from splitsmith.ensemble import voters
from splitsmith.ensemble.calibration import EnsembleCalibration, load_calibration

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "build" / "sweeps"
DEFAULT_SIGNALS = OUT_DIR / "signals.parquet"
DEFAULT_RUNS = OUT_DIR / "runs.parquet"

# Parameter keys we accept in the grid. Single values, lists, or
# ``{baseline_offset: [..]}`` shorthand all become a list of values.
PARAM_KEYS: tuple[str, ...] = (
    "consensus",
    "c_required",
    "e_required",
    "enable_voter_e",
    "e_audio_strong_min_votes",
    "apriori_boost",
    "voter_a_floor",
    "voter_b_threshold",
    "voter_c_threshold",
    "voter_e_threshold",
    "voter_a_floor_offset",
    "voter_b_threshold_offset",
    "voter_c_threshold_offset",
    "voter_e_threshold_offset",
    "voter_c_mode",
    "voter_c_slack_min",
    "voter_c_slack_frac",
    "voter_c_confidence_override",
    "use_expected_rounds",
    "camera_class_filter",
)

# Conservative defaults that mirror the production ensemble config.
DEFAULTS: dict[str, Any] = {
    "consensus": 2,
    "c_required": True,
    "e_required": False,
    "enable_voter_e": False,
    "e_audio_strong_min_votes": 3,
    "apriori_boost": 1.0,
    "voter_a_floor": None,             # use calibration
    "voter_b_threshold": None,
    "voter_c_threshold": None,
    "voter_e_threshold": None,
    "voter_a_floor_offset": 0.0,
    "voter_b_threshold_offset": 0.0,
    "voter_c_threshold_offset": 0.0,
    "voter_e_threshold_offset": 0.0,
    "voter_c_mode": "adaptive",
    "voter_c_slack_min": 3,
    "voter_c_slack_frac": 0.10,
    "voter_c_confidence_override": 0.60,
    "use_expected_rounds": True,
    "camera_class_filter": "all",
}


def _git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:
        return "nogit"


def _load_grid(path: str) -> dict[str, list[Any]]:
    """Load a YAML/JSON grid. Single values are wrapped in a list."""
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    raw = yaml.safe_load(text) or {}
    grid: dict[str, list[Any]] = {}
    for k, v in raw.items():
        if k not in PARAM_KEYS:
            raise SystemExit(
                f"Unknown grid key: {k!r}. Valid keys: {sorted(PARAM_KEYS)}"
            )
        grid[k] = list(v) if isinstance(v, (list, tuple)) else [v]
    return grid


def _resolve_thresholds(
    combo: dict[str, Any],
    camera_class: str,
    cal: EnsembleCalibration,
) -> dict[str, float | None]:
    """Apply absolute/offset overrides on top of the calibrated baseline."""
    base = cal.thresholds_for(camera_class)
    out: dict[str, float | None] = {}
    for v in ("a_floor", "b_threshold", "c_threshold", "e_threshold"):
        baseline = getattr(base, f"voter_{v}")
        abs_v = combo.get(f"voter_{v}")
        offset = combo.get(f"voter_{v}_offset", 0.0)
        if abs_v is not None:
            out[v] = float(abs_v)
        elif baseline is None:
            out[v] = None
        else:
            out[v] = float(baseline) + float(offset)
    return out


def _evaluate_combo(
    combo: dict[str, Any],
    signals: dict[str, np.ndarray],
    fixture_index: dict[str, np.ndarray],
    cal: EnsembleCalibration,
) -> list[dict[str, Any]]:
    """Score one parameter combo. Returns one row per fixture + aggregate rows."""
    fix_filter = combo["camera_class_filter"]
    rows_out: list[dict[str, Any]] = []

    all_kept = np.zeros(signals["label"].size, dtype=bool)
    all_mask = np.zeros(signals["label"].size, dtype=bool)
    per_class_kept: dict[str, np.ndarray] = {}
    per_class_mask: dict[str, np.ndarray] = {}

    for fixture, idx in fixture_index.items():
        cam_class = signals["camera_class"][idx[0]]
        if fix_filter != "all" and cam_class != fix_filter:
            continue
        thresholds = _resolve_thresholds(combo, cam_class, cal)

        conf = signals["confidence"][idx]
        clap_diff = signals["clap_diff"][idx]
        score_c = signals["score_c"][idx]
        voter_e_sig = signals["voter_e_signal"][idx]
        label = signals["label"][idx]
        expected = signals["expected_rounds"][idx[0]]
        audit_count = int(signals["audit_count"][idx[0]])

        va = voters.vote_a(conf, thresholds["a_floor"] or 0.0)
        vb = voters.vote_b(clap_diff, thresholds["b_threshold"] or -np.inf)
        if combo["voter_c_mode"] == "adaptive" and combo["use_expected_rounds"] and (
            expected is not None and expected > 0
        ):
            vc = voters.vote_c_adaptive(
                score_c,
                int(expected),
                slack_min=int(combo["voter_c_slack_min"]),
                slack_frac=float(combo["voter_c_slack_frac"]),
                confidence_override=combo["voter_c_confidence_override"],
            )
        elif combo["voter_c_mode"] == "adaptive":
            # No expected_rounds available -> fall back to audit count
            # so the adaptive mode still behaves; flag with a note.
            vc = voters.vote_c_adaptive(
                score_c,
                audit_count,
                slack_min=int(combo["voter_c_slack_min"]),
                slack_frac=float(combo["voter_c_slack_frac"]),
                confidence_override=combo["voter_c_confidence_override"],
            )
        else:
            vc = voters.vote_c_global(score_c, thresholds["c_threshold"] or 0.0)

        ve_active = (
            combo["enable_voter_e"]
            and thresholds["e_threshold"] is not None
            and not np.all(np.isnan(voter_e_sig))
        )
        if ve_active:
            ve_filled = np.where(np.isnan(voter_e_sig), -np.inf, voter_e_sig)
            ve = voters.vote_e(ve_filled, float(thresholds["e_threshold"]))
        else:
            ve = np.zeros_like(va)

        vote_total = va + vb + vc
        eff_expected = (
            int(expected) if (combo["use_expected_rounds"] and expected is not None) else None
        )
        boost = voters.apriori_boost(conf, eff_expected, float(combo["apriori_boost"]))
        keep_mask = voters.consensus_keep(
            vote_total,
            boost,
            int(combo["consensus"]),
            vote_c=vc,
            c_required=bool(combo["c_required"]),
        )
        if ve_active and combo["e_required"]:
            veto = ~ve.astype(bool)
            strong = combo["e_audio_strong_min_votes"]
            if strong is not None:
                veto = veto & ~(vote_total >= int(strong))
            keep_mask = keep_mask & ~veto

        n_pos = int(label.sum())
        n_kept = int(keep_mask.sum())
        tp = int(((keep_mask) & (label == 1)).sum())
        fp = n_kept - tp
        fn = n_pos - tp
        precision = tp / n_kept if n_kept else 0.0
        recall = tp / n_pos if n_pos else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0.0

        solo = {}
        for name, vec in (("a", va), ("b", vb), ("c", vc), ("e", ve)):
            solo[f"solo_{name}"] = int(
                ((label == 1) & (vec == 1) & (vote_total + ve == 1)).sum()
            )
        disagree1 = int((vote_total + ve == 1).sum())
        disagree1_pos = int(((vote_total + ve == 1) & (label == 1)).sum())

        rows_out.append({
            "fixture": fixture,
            "camera_class": cam_class,
            "n_candidates": int(idx.size),
            "n_positives": n_pos,
            "n_kept": n_kept,
            "true_pos": tp,
            "false_pos": fp,
            "false_neg": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "disagreement_1_count": disagree1,
            "disagreement_1_pos": disagree1_pos,
            **solo,
            "voter_a_floor_eff": thresholds["a_floor"],
            "voter_b_threshold_eff": thresholds["b_threshold"],
            "voter_c_threshold_eff": thresholds["c_threshold"],
            "voter_e_threshold_eff": thresholds["e_threshold"],
        })

        all_kept[idx] = keep_mask
        all_mask[idx] = True
        per_class_kept.setdefault(cam_class, np.zeros_like(all_kept))[idx] = keep_mask
        per_class_mask.setdefault(cam_class, np.zeros_like(all_mask))[idx] = True

    def _agg(name: str, kept: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
        label = signals["label"]
        m = mask
        n_pos = int(label[m].sum())
        n_kept = int(kept[m].sum())
        tp = int(((kept) & (label == 1) & m).sum())
        fp = n_kept - tp
        fn = n_pos - tp
        precision = tp / n_kept if n_kept else 0.0
        recall = tp / n_pos if n_pos else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0.0
        return {
            "fixture": name,
            "camera_class": "__agg__" if name == "__all__" else name.removeprefix("__class_"),
            "n_candidates": int(m.sum()),
            "n_positives": n_pos,
            "n_kept": n_kept,
            "true_pos": tp,
            "false_pos": fp,
            "false_neg": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "disagreement_1_count": np.nan,
            "disagreement_1_pos": np.nan,
            "solo_a": np.nan,
            "solo_b": np.nan,
            "solo_c": np.nan,
            "solo_e": np.nan,
            "voter_a_floor_eff": np.nan,
            "voter_b_threshold_eff": np.nan,
            "voter_c_threshold_eff": np.nan,
            "voter_e_threshold_eff": np.nan,
        }

    if all_mask.any():
        rows_out.append(_agg("__all__", all_kept, all_mask))
    for cls, kept_cls in per_class_kept.items():
        mask_cls = per_class_mask[cls]
        rows_out.append(_agg(f"__class_{cls}", kept_cls, mask_cls))
    return rows_out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    p.add_argument("--out", type=Path, default=DEFAULT_RUNS)
    p.add_argument(
        "--grid",
        type=str,
        default=None,
        help="Path to a YAML/JSON grid file; '-' reads stdin. Empty grid evaluates the defaults once.",
    )
    p.add_argument(
        "--run-name",
        type=str,
        default=None,
        help="Slug appended to the auto run_id. Default: grid file stem (or 'defaults').",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append rows to an existing runs.parquet rather than overwriting it.",
    )
    args = p.parse_args()

    if not args.signals.exists():
        raise SystemExit(
            f"{args.signals} does not exist -- run scripts/build_sweep_signals.py first."
        )
    table = pq.read_table(args.signals)
    signals_build_id = (
        table.column("signals_build_id")[0].as_py() if table.num_rows else "unknown"
    )

    # Materialise as numpy column-major dict for fast slicing.
    raw_cols = {name: np.array(table.column(name).to_pylist()) for name in table.schema.names}
    # ``expected_rounds`` is nullable -> object dtype; collapse to None per row.
    signals: dict[str, np.ndarray] = {
        "camera_class": raw_cols["camera_class"],
        "fixture": raw_cols["fixture"],
        "label": raw_cols["label"].astype(np.int64),
        "confidence": raw_cols["confidence"].astype(np.float64),
        "clap_diff": raw_cols["clap_diff"].astype(np.float64),
        "score_c": raw_cols["score_c"].astype(np.float64),
        "gunshot_prob": raw_cols["gunshot_prob"].astype(np.float64),
        "voter_e_signal": raw_cols["voter_e_signal"].astype(np.float64),
        "expected_rounds": raw_cols["expected_rounds"],
        "audit_count": raw_cols["audit_count"],
    }

    # Build per-fixture index arrays for fast slicing.
    fixture_index: dict[str, np.ndarray] = {}
    for i, fix in enumerate(signals["fixture"]):
        fixture_index.setdefault(str(fix), []).append(i)  # type: ignore[arg-type]
    fixture_index = {k: np.array(v, dtype=np.int64) for k, v in fixture_index.items()}

    grid_path = args.grid or ""
    grid = _load_grid(grid_path) if grid_path else {}
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]

    if value_lists:
        combos = [
            {**DEFAULTS, **dict(zip(keys, vals))}
            for vals in itertools.product(*value_lists)
        ]
    else:
        combos = [dict(DEFAULTS)]

    cal = load_calibration()

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    sha = _git_short_sha()
    slug = args.run_name
    if slug is None:
        slug = Path(grid_path).stem if grid_path and grid_path != "-" else "defaults"
    run_id = f"{now}_{sha}_{slug}"

    all_rows: list[dict[str, Any]] = []
    for combo_idx, combo in enumerate(combos):
        rows = _evaluate_combo(combo, signals, fixture_index, cal)
        for r in rows:
            r["run_id"] = run_id
            r["combo_idx"] = combo_idx
            r["signals_build_id"] = signals_build_id
            for k in PARAM_KEYS:
                r[f"param_{k}"] = combo[k]
            r["swept_keys"] = ",".join(keys)
        all_rows.extend(rows)

    pa_table = pa.Table.from_pylist(all_rows)
    if args.append and args.out.exists():
        existing = pq.read_table(args.out)
        combined = pa.concat_tables([existing, pa_table], promote_options="default")
        pq.write_table(combined, args.out)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa_table, args.out)

    overall = [r for r in all_rows if r["fixture"] == "__all__"]
    best = max(overall, key=lambda r: r["f1"]) if overall else None
    print(f"run_id: {run_id}")
    print(f"combos: {len(combos)}, rows: {len(all_rows)}")
    print(f"wrote {args.out} ({'append' if args.append else 'overwrite'})")
    if best is not None:
        print(
            f"best overall F1: {best['f1']:.3f}  (P={best['precision']:.3f} "
            f"R={best['recall']:.3f} kept={best['n_kept']} of {best['n_positives']} pos) "
            f"combo_idx={best['combo_idx']}"
        )
    print(f"swept keys: {keys or '(none -- single defaults run)'}")
    print(json.dumps({"run_id": run_id, "n_combos": len(combos)}))


if __name__ == "__main__":
    main()
