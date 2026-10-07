"""Fit and evaluate a learned beep-candidate ranker (#949, step 2).

Spec: docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md.
Runs ``detect_beep`` over every calibration fixture keeping every candidate,
labels the candidate(s) at the true beep, scores logistic regression and a
GBDT by leave-one-match-out, fits a confidence head on the out-of-fold
logits, applies the ship gate and writes the report.

Run::

    uv run python scripts/train_beep_ranker.py
    uv run python scripts/train_beep_ranker.py --report /tmp/ranker_report.json

scikit-learn is a dev dependency; nothing under src/ imports it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from splitsmith.beep_calibration import BeepFixtureEntry, load_manifest
from splitsmith.beep_detect import BeepNotFoundError, detect_beep, load_audio
from splitsmith.beep_features import feature_vector
from splitsmith.config import BeepDetectConfig, BeepDetection, BeepRankerConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "beep_calibration" / "manifest.yaml"
REPORT_PATH = FIXTURES_DIR / "beep_calibration" / "ranker_report.json"
ALL_CANDIDATES = 10_000

_MATCH = re.compile(r"^stage-shots-(.+?)-stage\d+")


def match_group(stem: str) -> str:
    """The cross-validation group: the match a fixture was shot at."""
    found = _MATCH.match(stem)
    return found.group(1) if found else stem


@dataclass(frozen=True)
class CandidateRow:
    stem: str
    group: str
    features: list[float]
    positive: bool
    heuristic_score: float
    heuristic_confidence: float
    # Candidate time minus the labeled beep: shows whether a wrong pick is the
    # beep with a late onset or a different event.
    time_error_ms: float = 0.0


@dataclass(frozen=True)
class Clip:
    stem: str
    group: str
    rows: list[CandidateRow]

    @property
    def reachable(self) -> bool:
        return any(r.positive for r in self.rows)


def clip_from_detection(entry: BeepFixtureEntry, detection: BeepDetection | None) -> Clip:
    """Label ``detection``'s candidates against ``entry``'s truth and tolerance."""
    group = match_group(entry.stem)
    if detection is None:
        return Clip(stem=entry.stem, group=group, rows=[])
    tol_s = entry.tolerance_ms / 1000.0
    rows = []
    for c in detection.candidates:
        if c.features is None:
            raise ValueError(f"{entry.stem}: a candidate without features; is PR 1 merged?")
        rows.append(
            CandidateRow(
                stem=entry.stem,
                group=group,
                features=feature_vector(c.features),
                positive=abs(c.time - entry.ground_truth_in_clip) <= tol_s,
                heuristic_score=c.score,
                heuristic_confidence=c.confidence,
                time_error_ms=(c.time - entry.ground_truth_in_clip) * 1000.0,
            )
        )
    return Clip(stem=entry.stem, group=group, rows=rows)


def collect(manifest_path: Path = MANIFEST_PATH, fixtures_dir: Path = FIXTURES_DIR) -> list[Clip]:
    """Every manifest fixture's clip track, every candidate kept."""
    # Rows carry today's ranking and confidence ("heuristic_*"): the learned
    # ranker is the detector's default now, and a collect on the default would
    # compare the model against itself.
    config = BeepDetectConfig(top_n_candidates=ALL_CANDIDATES, ranker=BeepRankerConfig(ranker="heuristic"))
    clips = []
    for entry in load_manifest(manifest_path).fixtures:
        audio, sr = load_audio(fixtures_dir / entry.clip_wav)
        try:
            detection: BeepDetection | None = detect_beep(audio, sr, config)
        except BeepNotFoundError:
            detection = None
        clips.append(clip_from_detection(entry, detection))
    return clips


LOGIT_CLAMP = 10.0
TOP_N = 5
TOP1_FLOOR = 78  # 51.2 % + 10 pp of 127, rounded up
TOPN_FLOOR = 105  # today's top-N, 82.7 %
AUTO_TRUST = 0.95


def _fit_hook(model: Any, train: list[Clip]) -> None:
    """Test seam: called with each fold's training clips before ``fit``."""


def _head_fit_hook(head: Any, train: list[Clip]) -> None:
    """Test seam: called with each head fold's training clips before ``fit``."""


def _make_head() -> Any:
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression()


def clip_logits(probs: Sequence[float]) -> list[float]:
    out = []
    for p in probs:
        p = min(max(p, 1e-12), 1.0 - 1e-12)
        out.append(max(-LOGIT_CLAMP, min(LOGIT_CLAMP, math.log(p / (1.0 - p)))))
    return out


def margins(logits: Sequence[float]) -> list[float]:
    out = []
    for i, z in enumerate(logits):
        others = [o for j, o in enumerate(logits) if j != i]
        out.append(z - (max(others) if others else -LOGIT_CLAMP))
    return out


def _matrix(clips: list[Clip]) -> tuple[np.ndarray, np.ndarray]:
    rows = [r for c in clips for r in c.rows]
    x = np.array([r.features for r in rows], dtype=np.float64)
    y = np.array([r.positive for r in rows], dtype=np.int8)
    return x, y


def _balanced_weights(y: np.ndarray) -> np.ndarray:
    pos = max(1, int(y.sum()))
    neg = max(1, int(y.size - y.sum()))
    return np.where(y == 1, y.size / (2.0 * pos), y.size / (2.0 * neg))


def oof_probs(clips: list[Clip], make_model: Callable[[], Any]) -> dict[str, list[float]]:
    """Leave-one-match-out P(positive) for every row of every clip with rows.
    Trains only on reachable clips (spec 2.1); predicts every clip."""
    out: dict[str, list[float]] = {}
    for group in sorted({c.group for c in clips}):
        held = [c for c in clips if c.group == group and c.rows]
        train = [c for c in clips if c.group != group and c.reachable]
        if not held:
            continue
        x, y = _matrix(train)
        model = make_model()
        _fit_hook(model, train)
        model.fit(x, y, sample_weight=_balanced_weights(y))
        for clip in held:
            xc = np.array([r.features for r in clip.rows], dtype=np.float64)
            out[clip.stem] = [float(p) for p in model.predict_proba(xc)[:, 1]]
    return out


def _head_table(cs: list[Clip], probs: dict[str, list[float]]) -> tuple[np.ndarray, np.ndarray]:
    """The confidence head's rows: [logit, margin] per candidate; target 1 when
    the candidate is its clip's top-1 by ``probs`` and it is positive."""
    xs, y = [], []
    for c in cs:
        z = clip_logits(probs[c.stem])
        mg = margins(z)
        best = int(np.argmax(probs[c.stem]))
        for i, row in enumerate(c.rows):
            xs.append([z[i], mg[i]])
            y.append(int(i == best and row.positive))
    return np.array(xs, dtype=np.float64), np.array(y, dtype=np.int8)


def oof_head_confidence(clips: list[Clip], probs: dict[str, list[float]]) -> dict[str, list[float]]:
    """Leave-one-match-out confidence head over [logit, margin] (spec 4).
    Fitted over all clips with rows, unreachable ones included."""
    with_rows = [c for c in clips if c.rows and c.stem in probs]
    out: dict[str, list[float]] = {}
    for group in sorted({c.group for c in with_rows}):
        train = [c for c in with_rows if c.group != group]
        x, y = _head_table(train, probs)
        head = None
        if len(set(y.tolist())) == 2:
            head = _make_head()
            _head_fit_hook(head, train)
            head.fit(x, y)
        for c in (c for c in with_rows if c.group == group):
            xc, _ = _head_table([c], probs)
            out[c.stem] = (
                [float(p) for p in head.predict_proba(xc)[:, 1]] if head is not None else [0.0] * len(c.rows)
            )
    return out


def fit_final_head(clips: list[Clip], probs: dict[str, list[float]]) -> dict[str, Any]:
    """The head that ships: one fit over every clip's out-of-fold logits,
    unreachable clips included (spec 4)."""
    with_rows = [c for c in clips if c.rows and c.stem in probs]
    x, y = _head_table(with_rows, probs)
    head = _make_head()
    _head_fit_hook(head, with_rows)
    head.fit(x, y)
    return {
        "coef": [round(float(v), 8) for v in head.coef_[0]],
        "intercept": round(float(head.intercept_[0]), 8),
    }


def final_head_bins(
    clips: list[Clip], probs: dict[str, list[float]], head: dict[str, Any], thresholds: Sequence[float]
) -> list[dict[str, Any]]:
    """The shipped head applied to each clip's out-of-fold winner: how many
    clear each threshold and how many of those are wrong."""
    a, b = head["coef"]
    winners = []
    for c in clips:
        if not c.rows or c.stem not in probs:
            continue
        z = clip_logits(probs[c.stem])
        mg = margins(z)
        best = int(np.argmax(probs[c.stem]))
        conf = 1.0 / (1.0 + math.exp(-(a * z[best] + b * mg[best] + head["intercept"])))
        winners.append((conf, c.rows[best].positive))
    return [
        {
            "threshold": t,
            "n": sum(1 for conf, _ in winners if conf >= t),
            "wrong": sum(1 for conf, ok in winners if conf >= t and not ok),
        }
        for t in thresholds
    ]


@dataclass
class ClipOutcome:
    stem: str
    tags: list[str]
    top1: bool
    topn: bool
    confidence: float


def outcomes(
    clips: list[Clip],
    probs: dict[str, list[float]],
    head: dict[str, list[float]],
    tags: dict[str, list[str]],
    top_n: int = TOP_N,
) -> list[ClipOutcome]:
    out = []
    for c in clips:
        if not c.rows or c.stem not in probs:
            out.append(
                ClipOutcome(stem=c.stem, tags=tags.get(c.stem, []), top1=False, topn=False, confidence=0.0)
            )
            continue
        order = sorted(range(len(c.rows)), key=lambda i: probs[c.stem][i], reverse=True)
        out.append(
            ClipOutcome(
                stem=c.stem,
                tags=tags.get(c.stem, []),
                top1=c.rows[order[0]].positive,
                topn=any(c.rows[i].positive for i in order[:top_n]),
                confidence=head[c.stem][order[0]] if c.stem in head else 0.0,
            )
        )
    return out


def heuristic_outcomes(
    clips: list[Clip], tags: dict[str, list[str]], top_n: int = TOP_N
) -> list[ClipOutcome]:
    """Today's detector, scored by the same rules (rows are already in its order)."""
    out = []
    for c in clips:
        if not c.rows:
            out.append(
                ClipOutcome(stem=c.stem, tags=tags.get(c.stem, []), top1=False, topn=False, confidence=0.0)
            )
            continue
        out.append(
            ClipOutcome(
                stem=c.stem,
                tags=tags.get(c.stem, []),
                top1=c.rows[0].positive,
                topn=any(r.positive for r in c.rows[:top_n]),
                confidence=c.rows[0].heuristic_confidence,
            )
        )
    return out


def candidate_table(clip: Clip, probs: Sequence[float], limit: int = TOP_N) -> list[dict[str, Any]]:
    """``clip``'s candidates in the model's order with both rankings and each
    one's offset from the labeled beep (spec 2.5: a loss is shown with both
    rankings' candidate tables). Rows are in the heuristic's order already."""
    order = sorted(range(len(clip.rows)), key=lambda i: probs[i], reverse=True)
    return [
        {
            "model_rank": rank,
            "heuristic_rank": i + 1,
            "model_prob": round(float(probs[i]), 4),
            "heuristic_score": round(clip.rows[i].heuristic_score, 4),
            "time_error_ms": round(clip.rows[i].time_error_ms, 2),
            "positive": clip.rows[i].positive,
        }
        for rank, i in enumerate(order[:limit], start=1)
    ]


@dataclass
class Gate:
    top1_hits: int
    topn_hits: int
    wrong_at_95: int
    passed: bool


def gate(results: list[ClipOutcome]) -> Gate:
    top1 = sum(o.top1 for o in results)
    topn = sum(o.topn for o in results)
    wrong = sum(1 for o in results if o.confidence >= AUTO_TRUST and not o.top1)
    return Gate(top1, topn, wrong, top1 >= TOP1_FLOOR and topn >= TOPN_FLOOR and wrong == 0)


BINS = (
    (0.0, 0.5, "<0.5"),
    (0.5, 0.7, "0.5-0.7"),
    (0.7, AUTO_TRUST, "0.7-0.95"),
    (AUTO_TRUST, 1.01, ">=0.95"),
)


def summary(results: list[ClipOutcome]) -> dict[str, Any]:
    def block(rs: list[ClipOutcome]) -> dict[str, Any]:
        n = len(rs)
        return {
            "n": n,
            "top1": sum(o.top1 for o in rs),
            "topn": sum(o.topn for o in rs),
            "top1_pct": round(100.0 * sum(o.top1 for o in rs) / n, 1) if n else 0.0,
        }

    tags = sorted({t for o in results for t in o.tags})
    bins = []
    for lo, hi, name in BINS:
        rs = [o for o in results if lo <= o.confidence < hi]
        bins.append({"bin": name, "n": len(rs), "right": sum(o.top1 for o in rs)})
    return {
        "all": block(results),
        "by_tag": {t: block([o for o in results if t in o.tags]) for t in tags},
        "bins": bins,
    }


def _models() -> dict[str, Callable[[], Any]]:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    def lr() -> Any:
        pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))

        class _Weighted:
            def fit(self, x, y, sample_weight=None):
                pipe.fit(x, y, logisticregression__sample_weight=sample_weight)
                return self

            def predict_proba(self, x):
                return pipe.predict_proba(x)

            @property
            def pipeline(self):
                return pipe

        return _Weighted()

    return {"lr": lr, "gbdt": lambda: GradientBoostingClassifier(random_state=0)}


def _final_fit(name: str, make: Callable[[], Any], clips: list[Clip]) -> dict[str, Any]:
    """Spec 2.7: fit on every reachable fixture and record what would ship.
    The report's figures stay the out-of-fold ones."""
    from splitsmith.beep_features import FEATURE_NAMES

    x, y = _matrix([c for c in clips if c.reachable])
    model = make()
    model.fit(x, y, sample_weight=_balanced_weights(y))
    if name == "lr":
        pipe = model.pipeline
        scaler, lr = pipe.named_steps["standardscaler"], pipe.named_steps["logisticregression"]
        params = {
            "features": list(FEATURE_NAMES),
            "mean": [round(float(v), 8) for v in scaler.mean_],
            "scale": [round(float(v), 8) for v in scaler.scale_],
            "coef": [round(float(v), 8) for v in lr.coef_[0]],
            "intercept": round(float(lr.intercept_[0]), 8),
        }
    else:
        params = {
            "features": list(FEATURE_NAMES),
            "importances": [round(float(v), 6) for v in model.feature_importances_],
        }
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    return {"model_version": f"beep-ranker-{name}-{digest}", **params}


def main() -> None:
    from splitsmith.beep_features import FEATURE_NAMES

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    clips = collect()
    tags = {e.stem: list(e.tags) for e in load_manifest(MANIFEST_PATH).fixtures}
    today = heuristic_outcomes(clips, tags)
    report: dict[str, Any] = {
        "spec": "docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md",
        "features": list(FEATURE_NAMES),
        "fixtures": len(clips),
        "reachable": sum(c.reachable for c in clips),
        "groups": sorted({c.group for c in clips}),
        "heuristic": summary(today),
        "models": {},
    }
    for name, make in _models().items():
        probs = oof_probs(clips, make)
        head = oof_head_confidence(clips, probs)
        res = outcomes(clips, probs, head, tags)
        g = gate(res)
        lost = [o.stem for o, t in zip(res, today, strict=True) if t.top1 and not o.top1]
        fixed = [o.stem for o, t in zip(res, today, strict=True) if o.top1 and not t.top1]
        by_stem = {c.stem: c for c in clips}
        confident_wrong = [o.stem for o in res if o.confidence >= AUTO_TRUST and not o.top1]
        tables = {
            stem: candidate_table(by_stem[stem], probs[stem])
            for stem in dict.fromkeys(lost + confident_wrong)
            if stem in probs
        }
        report["models"][name] = {
            **summary(res),
            "gate": {
                "top1_hits": g.top1_hits,
                "topn_hits": g.topn_hits,
                "wrong_at_95": g.wrong_at_95,
                "passed": g.passed,
            },
            "reachable_top1": sum(o.top1 for o, c in zip(res, clips, strict=True) if c.reachable),
            "lost": lost,
            "fixed": fixed,
            "confident_wrong": confident_wrong,
            "candidate_tables": tables,
            "final_fit": _final_fit(name, make, clips),
            "oof": {
                c.stem: {
                    "probs": probs.get(c.stem, []),
                    "head": head.get(c.stem, []),
                    "positive": [r.positive for r in c.rows],
                }
                for c in clips
            },
        }
    # The shipped LR head (spec "Decision after the report"): one fit over all
    # out-of-fold logits, and how it scores the out-of-fold winners.
    lr_probs = {stem: d["probs"] for stem, d in report["models"]["lr"]["oof"].items() if d["probs"]}
    head = fit_final_head(clips, lr_probs)
    report["models"]["lr"]["final_fit"]["head"] = head
    report["models"]["lr"]["final_head_bins"] = final_head_bins(clips, lr_probs, head, (0.95, 0.97, 0.99))
    lr_top1 = report["models"]["lr"]["gate"]["top1_hits"]
    gbdt_top1 = report["models"]["gbdt"]["gate"]["top1_hits"]
    winner = "lr" if (gbdt_top1 - lr_top1) / len(clips) <= 0.02 else "gbdt"
    report["winner"] = winner
    report["ship"] = report["models"][winner]["gate"]["passed"]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    _print(report)
    print(f"\nWrote {args.report}")


def _print(report: dict[str, Any]) -> None:
    def line(name: str, s: dict[str, Any]) -> str:
        a = s["all"]
        return f"  {name:10} top1 {a['top1']:3}/{a['n']} ({a['top1_pct']:5.1f}%)  topN {a['topn']:3}"

    print(f"{report['fixtures']} fixtures, {report['reachable']} reachable, {len(report['groups'])} matches")
    print(line("heuristic", report["heuristic"]))
    for name, m in report["models"].items():
        print(
            line(name, m)
            + f"  gate={'PASS' if m['gate']['passed'] else 'FAIL'}  wrong@0.95={m['gate']['wrong_at_95']}"
        )
        print(
            f"    reachable top1 {m['reachable_top1']}/{report['reachable']}"
            f"  version {m['final_fit']['model_version']}"
        )
        print(f"    lost {len(m['lost'])}: {', '.join(m['lost'][:6])}{' ...' if len(m['lost']) > 6 else ''}")
        for stem in m["confident_wrong"]:
            pick = m["candidate_tables"][stem][0]
            near = min(m["candidate_tables"][stem], key=lambda t: abs(t["time_error_ms"]))
            print(
                f"    wrong@0.95 {stem}: pick {pick['time_error_ms']:+.2f} ms,"
                f" nearest candidate {near['time_error_ms']:+.2f} ms"
            )
        for b in m["bins"]:
            print(f"    {b['bin']:9} n={b['n']:3} right={b['right']:3}")
    for b in report["models"]["lr"].get("final_head_bins", []):
        print(f"  lr final head >= {b['threshold']}: n={b['n']} wrong={b['wrong']}")
    print(f"winner: {report['winner']}  ship: {report['ship']}")


if __name__ == "__main__":
    main()
