"""The lab's Models page payload (``lab.model_map``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.ensemble.fixtures import Fixture
from splitsmith.lab import model_map
from splitsmith.lab.model_map import (
    BUILD_SCRIPT,
    build_model_map,
    fixture_heldout,
    gbdt_hyperparameters,
    ranker_held_out,
    shipped_ranker_kind,
    version_from,
)


def _fx(stem: str, *, mount: str = "head", match: str = "alpha-2026", stage: int = 1, **kw) -> Fixture:
    return Fixture(
        stem=stem,
        mount=mount,
        camera_id=None,
        camera_make="Insta360",
        camera_model="GO 3S",
        shooter_id="sabc",
        match=match,
        stage_number=stage,
        expected_rounds=12,
        n_audited_shots=12,
        has_wav=True,
        **kw,
    )


# A flat calibration as git history holds it: built before model sets.
LEGACY_CAL = {
    "built_at": "2026-10-09T20:14:31+00:00",
    "voter_c_target_recall": 0.95,
    "tolerance_ms": 75.0,
    "thresholds_by_camera_class": {
        "headcam": {
            "voter_a_floor": 0.013,
            "voter_b_threshold": 0.036,
            "voter_c_threshold": 0.2,
            "n_calibration_candidates": 100,
            "n_calibration_positives": 40,
            "calibration_fixtures": ["stage-shots-alpha-2026-stage1-sabc"],
        },
        "handheld": {
            "voter_c_threshold": 0.9,
            "calibration_fixtures": ["stage-shots-alpha-2026-stage1-sabc-iphone"],
        },
    },
    "voter_c_metrics_by_camera_class": {
        "headcam": {
            "voter_c_precision_cv": 0.9,
            "voter_c_recall_cv": 0.95,
            "voter_c_f1_cv": 0.92,
            "voter_c_tp": 38,
            "voter_c_fp": 4,
            "voter_c_fn": 2,
        }
    },
    "voter_c_onnx_artifacts": {"headcam": "voter_c_gbdt_headcam.onnx"},
    "model_artifacts": {"pann_cnn14": {"filename": "pann.onnx", "sha256": "ab", "size_bytes": 3}},
}

LEGACY_HELDOUT = {
    "built_at": "2026-10-10T08:15:48+00:00",
    "splits": {"lomo": "LeaveOneGroupOut, groups = match"},
    "by_camera_class": {
        "headcam": {
            "n_stages": 2,
            "n_candidates": 100,
            "matches": ["alpha-2026"],
            "lomo": {
                "at_shipped_threshold": {
                    "precision": 0.8,
                    "recall": 0.9,
                    "f1": 0.85,
                    "tp": 9,
                    "fp": 2,
                    "fn": 1,
                },
                "own_threshold": 0.1,
                "at_own_threshold": None,
            },
        }
    },
}


def _set_block(*, with_clap: bool) -> dict:
    by_class = json.loads(json.dumps(LEGACY_CAL["thresholds_by_camera_class"]))
    if not with_clap:
        for block in by_class.values():
            block.pop("voter_b_threshold", None)
    name = "clap" if with_clap else "base"
    return {
        "columns": [],
        "voter_c_feature_dim": 55 if with_clap else 31,
        "voter_c_onnx_artifacts": {"headcam": f"voter_c_gbdt_{name}_headcam.onnx"},
        "thresholds_by_camera_class": by_class,
        "voter_c_metrics_by_camera_class": LEGACY_CAL["voter_c_metrics_by_camera_class"],
        "requires_artifacts": ["clap_audio_encoder", "clap_text_embeddings"] if with_clap else [],
    }


CAL = {
    **{
        k: v
        for k, v in LEGACY_CAL.items()
        if k
        not in ("thresholds_by_camera_class", "voter_c_metrics_by_camera_class", "voter_c_onnx_artifacts")
    },
    "default_model_set": "base",
    "model_sets": {"base": _set_block(with_clap=False), "clap": _set_block(with_clap=True)},
}

HELDOUT = {
    "built_at": LEGACY_HELDOUT["built_at"],
    "splits": LEGACY_HELDOUT["splits"],
    "model_sets": {
        "base": {"by_camera_class": LEGACY_HELDOUT["by_camera_class"]},
        "clap": {"by_camera_class": {}},
    },
}


def _build(fixtures, **over):
    kwargs = {
        "fixtures": fixtures,
        "event_ids": {},
        "calibration": CAL,
        "heldout": HELDOUT,
        "ranker_report": None,
        "ranker_manifest": None,
        "ranker_version": "beep-ranker-lr-4a15dc5d",
        "hyperparameters": {"max_depth": 3},
        "artifact_sizes": {"voter_c_gbdt_base_headcam.onnx": 1234},
        "history": [],
    }
    kwargs.update(over)
    return build_model_map(**kwargs)


def test_each_fixture_names_the_head_it_calibrated_and_why_others_are_out() -> None:
    m = _build(
        [
            _fx("stage-shots-alpha-2026-stage1-sabc"),
            _fx("stage-shots-alpha-2026-stage1-sabc-iphone", mount="hand"),
            _fx("stage-shots-alpha-2026-stage2-sabc", stage=2, superseded_by="stage-shots-new"),
            _fx("stage-shots-blacksmith-2026-stage6-s97dcec94-apple-iphone17pro", mount="hand"),
        ]
    )
    rows = {f.stem: f for f in m.fixtures}
    assert rows["stage-shots-alpha-2026-stage1-sabc"].voter_c == "headcam"
    assert rows["stage-shots-alpha-2026-stage1-sabc-iphone"].voter_c == "handheld"
    assert rows["stage-shots-alpha-2026-stage2-sabc"].voter_c is None
    assert rows["stage-shots-alpha-2026-stage2-sabc"].superseded_by == "stage-shots-new"
    assert rows["stage-shots-blacksmith-2026-stage6-s97dcec94-apple-iphone17pro"].wrong_clip is True
    assert rows["stage-shots-alpha-2026-stage1-sabc"].camera == "Insta360 GO 3S"


def test_fold_keys_follow_the_event_id_like_the_build() -> None:
    """The build groups by ``event_id`` (``match:stage:shooter``), which can
    name a match the stem does not; the stem is only the fallback."""
    m = _build(
        [_fx("stage-shots-alpha-2026-stage1-sabc"), _fx("stage-shots-alpha-2026-stage3-sabc", stage=3)],
        event_ids={"stage-shots-alpha-2026-stage1-sabc": "alpha-open-2026:1:sabc"},
    )
    rows = {f.stem: f for f in m.fixtures}
    assert rows["stage-shots-alpha-2026-stage1-sabc"].match_group == "alpha-open-2026"
    assert rows["stage-shots-alpha-2026-stage1-sabc"].stage_group == "alpha-open-2026:1"
    assert rows["stage-shots-alpha-2026-stage3-sabc"].stage_group == "alpha-2026:3"


def test_heads_carry_thresholds_cv_and_held_out_metrics() -> None:
    m = _build([])
    head = {h.camera_class: h for h in m.ensemble.heads if h.model_set == "base"}
    assert head["headcam"].voter_c_threshold == 0.2
    assert head["headcam"].cv is not None and head["headcam"].cv.tp == 38
    assert head["headcam"].onnx_bytes == 1234
    assert [s.split for s in head["headcam"].heldout] == ["lomo"]
    assert head["headcam"].heldout[0].at_shipped_threshold.recall == 0.9
    assert head["headcam"].heldout_matches == ["alpha-2026"]
    # No held-out block and no metrics for the class: still listed, empty.
    assert head["handheld"].heldout == [] and head["handheld"].cv is None
    assert m.ensemble.artifacts[0].filename == "pann.onnx"


def test_every_head_names_its_set_and_the_default_set_comes_first() -> None:
    m = _build([])
    assert [(h.model_set, h.camera_class) for h in m.ensemble.heads] == [
        ("base", "headcam"),
        ("base", "handheld"),
        ("clap", "headcam"),
        ("clap", "handheld"),
    ]
    by = {(h.model_set, h.camera_class): h for h in m.ensemble.heads}
    assert by[("base", "headcam")].voter_b_threshold is None
    assert by[("clap", "headcam")].voter_b_threshold == 0.036
    assert by[("clap", "headcam")].onnx == "voter_c_gbdt_clap_headcam.onnx"
    assert by[("clap", "headcam")].heldout == []
    assert m.ensemble.feature_dim == 31


def test_a_flat_calibration_from_history_reads_as_one_legacy_set() -> None:
    assert model_map.model_set_names(LEGACY_CAL) == ["legacy"]
    assert model_map.model_set_block(LEGACY_CAL, "legacy") is LEGACY_CAL
    assert model_map.model_set_names(CAL) == ["base", "clap"]
    with pytest.raises(KeyError):
        model_map.model_set_block(CAL, "legacy")
    m = _build(
        [],
        calibration=LEGACY_CAL,
        heldout=LEGACY_HELDOUT,
        artifact_sizes={"voter_c_gbdt_headcam.onnx": 1234},
    )
    head = {h.camera_class: h for h in m.ensemble.heads}
    assert {h.model_set for h in m.ensemble.heads} == {"legacy"}
    assert head["headcam"].voter_c_threshold == 0.2
    assert head["headcam"].onnx_bytes == 1234
    assert head["headcam"].heldout[0].at_shipped_threshold.recall == 0.9
    v = version_from("cccc", "2026-07-01", "old", LEGACY_CAL, None)
    assert v.model_set == "legacy" and v.recall == {"headcam": 0.95}


def test_a_build_without_the_heldout_report_still_maps() -> None:
    m = _build([], heldout=None)
    assert all(h.heldout == [] for h in m.ensemble.heads)
    assert m.ensemble.heldout_built_at is None


def test_ranker_verdicts_come_from_the_shipped_models_out_of_fold_picks() -> None:
    assert ranker_held_out({"probs": [0.2, 0.9], "positive": [False, True]}) == "right"
    assert ranker_held_out({"probs": [0.9, 0.2], "positive": [False, True]}) == "wrong"
    assert ranker_held_out({"probs": [0.9, 0.2], "positive": [False, False]}) == "unreachable"
    assert ranker_held_out(None) is None
    assert shipped_ranker_kind("beep-ranker-gbdt-4ec5d074") == "gbdt"
    report = {
        "fixtures": 2,
        "reachable": 1,
        "groups": ["alpha-2026"],
        "features": ["a", "b"],
        "models": {
            "lr": {
                "all": {"n": 2, "top1": 1, "top1_pct": 50.0},
                "final_fit": {"coef": [0.5, -1.0]},
                "oof": {
                    "stage-shots-alpha-2026-stage1-sabc": {"probs": [0.9, 0.1], "positive": [True, False]}
                },
            },
            # The GBDT's own pick is wrong here; it is not the shipped model.
            "gbdt": {
                "oof": {
                    "stage-shots-alpha-2026-stage1-sabc": {"probs": [0.1, 0.9], "positive": [True, False]}
                }
            },
        },
    }
    manifest = {"fixtures": [{"stem": "stage-shots-alpha-2026-stage1-sabc", "tags": ["headcam"]}]}
    m = _build(
        [_fx("stage-shots-alpha-2026-stage1-sabc"), _fx("stage-shots-alpha-2026-stage2-sabc", stage=2)],
        ranker_report=report,
        ranker_manifest=manifest,
    )
    rows = {f.stem: f for f in m.fixtures}
    use = rows["stage-shots-alpha-2026-stage1-sabc"].beep_ranker
    assert use is not None and use.held_out == "right" and use.tags == ["headcam"]
    assert rows["stage-shots-alpha-2026-stage2-sabc"].beep_ranker is None
    assert m.beep_ranker is not None and m.beep_ranker.features[1].lr_coef == -1.0
    assert m.beep_ranker.folds == ["alpha-2026"]


def test_version_lists_what_it_added_and_dropped() -> None:
    first = version_from("aaaa", "2026-05-01", "first", {"calibration_fixtures": ["x", "y"]}, None)
    assert first.n_fixtures == {"all": 2} and first.added == ["x", "y"]
    second = version_from("bbbb", "2026-06-01", "second", CAL, {"x", "stage-shots-alpha-2026-stage1-sabc"})
    assert second.added == ["stage-shots-alpha-2026-stage1-sabc-iphone"]
    assert second.removed == ["x"]
    assert second.recall == {"headcam": 0.95}
    assert second.model_set == "base"


def test_fixture_heldout_counts_each_fixture_at_its_heads_threshold() -> None:
    rows = [
        {"fixture": "h", "label": 1, "score_c_lomo": 0.5},  # headcam threshold 0.2: found
        {"fixture": "h", "label": 0, "score_c_lomo": 0.3},  # extra
        {"fixture": "p", "label": 1, "score_c_lomo": 0.5},  # handheld threshold 0.9: missed
        {"fixture": "p", "label": 1, "score_c_lomo": float("nan")},  # unscorable fold: left out
        {"fixture": "old", "score_c_lomo": 0.9},  # a build without labels
        {"fixture": "gone", "label": 1, "score_c_lomo": 0.9},  # not calibrating this model
    ]
    out = fixture_heldout(rows, {"h": 0.2, "p": 0.9, "old": 0.5})
    assert {k: (v.tp, v.fp, v.fn) for k, v in out.items()} == {"h": (1, 1, 0), "p": (0, 0, 1)}


def test_models_are_the_shipped_one_and_every_bundle(tmp_path, monkeypatch) -> None:
    """The route resolves a model id only through this list, so an id is a
    folder name under build/models or ``shipped``, never a path."""
    from splitsmith.ensemble import model_bundle as mb

    folder = tmp_path / "reaudit"
    folder.mkdir()
    (folder / mb.CALIBRATION_NAME).write_text(json.dumps(CAL), encoding="utf-8")
    mb.write_manifest(
        folder,
        mb.ModelManifest(
            name="Re-audited",
            version="v1",
            built_at="2026-10-15T18:00:00+00:00",
            selection=mb.CorpusSelection(matches=["alpha-2026"], reviewed_only=True),
        ),
    )
    monkeypatch.setattr(mb, "MODELS_DIR", tmp_path)
    shipped = tmp_path / "shipped_cal.json"
    shipped.write_text(json.dumps(CAL), encoding="utf-8")

    sources = model_map.model_sources(shipped)
    assert list(sources) == ["shipped", "reaudit"]
    assert sources["reaudit"][1] == folder / "heldout"

    m = model_map.load_model_map(shipped, "reaudit")
    assert m.model is not None and m.model.name == "Re-audited"
    assert m.model.selection == "matches: alpha-2026, reviewed only"
    assert [s.id for s in m.models] == ["shipped", "reaudit"]
    with pytest.raises(KeyError):
        model_map.load_model_map(shipped, "../etc")


def test_gbdt_hyperparameters_are_read_from_the_build_script() -> None:
    params = gbdt_hyperparameters(Path(BUILD_SCRIPT).read_text(encoding="utf-8"))
    assert set(params) >= {"n_estimators", "max_depth", "learning_rate"}
    assert gbdt_hyperparameters("nothing here") == {}
