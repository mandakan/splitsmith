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

import re
from dataclasses import dataclass
from pathlib import Path

from splitsmith.beep_calibration import BeepFixtureEntry, load_manifest
from splitsmith.beep_detect import BeepNotFoundError, detect_beep, load_audio
from splitsmith.beep_features import feature_vector
from splitsmith.config import BeepDetectConfig, BeepDetection

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
            )
        )
    return Clip(stem=entry.stem, group=group, rows=rows)


def collect(manifest_path: Path = MANIFEST_PATH, fixtures_dir: Path = FIXTURES_DIR) -> list[Clip]:
    """Every manifest fixture's clip track, every candidate kept."""
    config = BeepDetectConfig(top_n_candidates=ALL_CANDIDATES)
    clips = []
    for entry in load_manifest(manifest_path).fixtures:
        audio, sr = load_audio(fixtures_dir / entry.clip_wav)
        try:
            detection: BeepDetection | None = detect_beep(audio, sr, config)
        except BeepNotFoundError:
            detection = None
        clips.append(clip_from_detection(entry, detection))
    return clips
