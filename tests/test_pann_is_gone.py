"""PANN is retired: no runtime, build or lab code reads or writes it, and old
audit docs that carry ``gunshot_prob`` still load (thin detection spec 2026-10-26)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# ``PANN``, ``pann_cnn14``, ``panns_inference``, ``PannRuntime`` and the
# signal's name; ``panning`` (the waveform) is not a match.
_PANN = r"\bPANN\b|\bpanns?_|PannRuntime|gunshot_prob|gunshotProb"
# The one place the name may stay: the lab's optional field for old eval records.
_ALLOWED = {"src/splitsmith/lab/core.py"}


def _hits(*paths: str) -> list[str]:
    out = subprocess.run(
        ["git", "grep", "-n", "-E", _PANN, "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    return [line for line in out if line.split(":", 1)[0] not in _ALLOWED]


def test_no_runtime_build_or_spa_code_references_pann() -> None:
    assert _hits("src/splitsmith", "scripts", ":!src/splitsmith/data") == []


def test_the_shipped_artifacts_name_no_pann() -> None:
    assert _hits("src/splitsmith/data") == []


def test_fetch_models_lists_only_clap() -> None:
    from splitsmith.models.registry import load_spec_from_calibration

    spec = load_spec_from_calibration()
    assert spec is not None
    assert sorted(spec.slugs()) == ["clap_audio_encoder", "clap_text_embeddings"]


def test_old_eval_candidates_still_load() -> None:
    from splitsmith.lab.core import EvalCandidate

    row = {
        "candidate_number": 1,
        "time": 1.0,
        "ms_after_beep": 500,
        "confidence": 0.4,
        "peak_amplitude": 0.3,
        "score_c": 0.5,
        "clap_diff": 0.0,
        "vote_a": 1,
        "vote_b": 0,
        "vote_c": 1,
        "vote_total": 2,
        "apriori_boost": 0.0,
        "ensemble_score": 2.0,
        "kept": True,
        "truth": 1,
    }
    assert EvalCandidate.model_validate({**row, "gunshot_prob": 0.9}).gunshot_prob == 0.9
    assert EvalCandidate.model_validate(row).gunshot_prob is None


def test_hosted_images_pin_the_clap_set_and_the_wheel_has_no_hub_dependency() -> None:
    import tomllib

    for name in ("Dockerfile", "Dockerfile.gpu"):
        assert "SPLITSMITH_MODEL_SET=clap" in (REPO / name).read_text(encoding="utf-8"), name
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert not any(d.replace("-", "_").startswith("huggingface_hub") for d in deps)
    dev = [d for group in pyproject.get("dependency-groups", {}).values() for d in group if isinstance(d, str)]
    assert not any(d.replace("-", "_").startswith("panns_inference") for d in dev)
