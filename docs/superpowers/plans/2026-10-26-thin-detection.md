# Thin Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shot detection runs with no model download by default (`base`), the 120 MB CLAP model becomes an opt-in second model set (`clap`), PANN is retired, and the shipped calibration is rebuilt on the whole corpus with one stage decoder per camera class per set.

**Architecture:** One build produces two model sets from one candidate universe by selecting voter C columns by name; the calibration carries both under `model_sets`, each with per-class GBDT graphs, thresholds, metrics and decoders. The runtime resolves a set (env, then preference, then `base`), constructs the CLAP session only for `clap`, falls back to `base` when the artifacts are absent, and stamps the set it used on the result and the audit doc. A local preference plus one Settings control drive the opt-in and the existing download job.

**Tech Stack:** Python 3.11, numpy, scikit-learn (build only), onnxruntime, skl2onnx (build only), FastAPI, Pydantic; React + TypeScript (SPA, vitest), pnpm.

**Spec:** `docs/superpowers/specs/2026-10-26-thin-detection-design.md`

**Base:** written against `main` at `6ab8d76e`; line anchors are for that commit.

## Global Constraints

- No new dependencies. `huggingface_hub` leaves `[project.dependencies]`; `onnxruntime` stays.
- Never commit a fixture WAV or anything under `tests/fixtures/.cache/`.
- `uv run` rewrites `uv.lock`: commit `uv.lock` only in the task that removes `huggingface_hub` (Task 11), and `git add` named files everywhere else.
- Every text read, write and subprocess decode names `encoding="utf-8"` (`read_text`, `write_text`, `open` in text mode, `subprocess.run(..., text=True)`): Ruff PLW1514 and `tests/test_text_io_encoding.py` fail the suite otherwise.
- Black, line length 110; Ruff; type hints; `pathlib.Path`; Pydantic for everything crossing a module boundary.
- ASCII only in rule files, What's new entries and docs; no dash punctuation in user copy.
- Hosted path: a request never runs ffmpeg or an `ensure_*` producer; nothing on the `create_app` path imports `splitsmith.db` at module level (`scripts/ci/assert_slim_import_surface.py`).
- After any route or response-model change: `uv run python scripts/export_openapi.py`, then `pnpm gen:api` in `src/splitsmith/ui_static`, commit both.
- The test suite runs in parallel; a new test must not share mutable state outside `tmp_path`. Full-suite runs on gaspode use `-n 4` (see memory: parallel agents OOM the box).
- Two PRs: Tasks 1 to 11 are PR 1 (branch `thin-detection`), Tasks 12 to 14 are PR 2 (branch `detection-model-opt-in`, after PR 1 merges).
- Between Task 2 and Task 10 the shipped `ensemble_calibration.json` is the old flat shape and `load_calibration()` refuses it, so every test that reads the shipped calibration or graphs (the voter C tests in `tests/test_onnx_parity.py`, the shipped-calibration test in `tests/test_calibration_model_sets.py`, the manifest test in `tests/test_pann_is_gone.py`, the CI-script test in `tests/test_models_layer.py`, and any the suite reveals) carries `@pytest.mark.xfail(strict=True, reason="until the rebuild in Task 10")`. Task 10 removes every such marker; `git grep "until the rebuild"` must be empty when PR 1 opens. Tests that need a calibration use `tests/_ensemble_stubs.py` (Task 5).

## Acceptance gate (what Task 10's table must show)

The spec's gate is leave-one-match-out on the same rows. Both the old and the new build produce candidate-level LOMO figures per class and per match (`build/ensemble_heldout/report.json`: `at_own_threshold` and `lomo_by_match`), so that comparison is mechanical. Event-level per class exists only for the new build (the old build fits one decoder over all classes and reports one overall out-of-fold F1), so event level is reported per class for the new build and compared overall with the old; the spike's per-class event figures (headcam `clap` 0.936 on 104 fixtures, `base` 0.927; handheld old columns 0.971, `base` 0.974 on 68) are the sanity reference.

- `clap`, per class: candidate-level LOMO F1 at own threshold within 0.005 of the old-columns baseline (Task 0) on the same rows; per match, a drop over 0.02 blocks the PR, a smaller one is listed.
- `base`, per class: decoder event F1 at least 0.92 headcam, 0.97 handheld; per match, no more than 0.03 under `clap`.

## Review Focus

1. A `clap` preference on a machine with no network and no cached artifacts: detection completes with `base` and records it (Task 7, `test_clap_requested_without_artifacts_runs_base_and_says_so`).
2. A calibration JSON from before this change (flat schema, no `model_sets`) loaded by the new runtime: one clear error naming the rebuild, not a `KeyError` (Task 2, `test_a_flat_calibration_names_the_rebuild`).
3. An audit doc written before this change (candidate rows carry `gunshot_prob`, no `_model_set`) opened in the lab and the Audit page: loads, set shows as unknown (Task 4, `test_old_candidate_rows_still_load`; Task 13 vitest).
4. The Settings control flipped to `clap` while a download job is already active: no second job (Task 12, `test_put_clap_does_not_submit_a_second_download`).
5. `camera_class=None` (unknown mount) under `base`: the default class's thresholds and decoder are used, same as today (Task 2, `test_unknown_camera_class_falls_to_the_default_class`).

---

### Task 0: Baseline bundle from the unchanged build

**Files:**
- Create (local only, not committed): `build/models/baseline-2026-10-26/`

**Interfaces:**
- Produces: `build/models/baseline-2026-10-26/heldout/report.json` with `by_camera_class[cls].lomo.at_own_threshold` and `lomo_by_match`, and the calibration's `decoder.fit_report.out_of_fold.f1`. Task 10 reads these.

- [ ] **Step 1: Confirm the caches cover the corpus**

Run (on gaspode, from `main`, before any code change):
```bash
uv run python scripts/fixture_audio.py fetch
uv run python scripts/build_ensemble_artifacts.py --out build/models/baseline-2026-10-26 --extract 2>&1 | tee ~/.claude-tmp/thin-detect/baseline-build.log
```
Expected: the build completes and writes `build/models/baseline-2026-10-26/heldout/report.json`. `--extract` regenerates stale CLAP and PANN caches (PANN extraction needs the dev venv's torch and `panns_inference`; this is the last time PANN is ever extracted). Budget: up to two hours CPU on gaspode; run it with nothing else heavy on the box.

- [ ] **Step 2: Record the baseline numbers**

Run:
```bash
uv run python - <<'EOF'
import json
r = json.load(open("build/models/baseline-2026-10-26/heldout/report.json"))
for cls, b in r["by_camera_class"].items():
    print(cls, "lomo own", b["lomo"]["at_own_threshold"], "by match", {m: v["f1"] for m, v in b["lomo_by_match"].items()})
c = json.load(open("build/models/baseline-2026-10-26/ensemble_calibration.json"))
print("decoder oof f1", c["decoder"]["fit_report"]["out_of_fold"])
EOF
```
Expected: one line per class with F1 and the per-match map; paste the output into `~/.claude-tmp/thin-detect/baseline.txt`. Task 10 quotes it in the PR.

---

### Task 1: Voter C columns by name

**Files:**
- Modify: `src/splitsmith/ensemble/features.py:365-400` (relative block), `:750-805` (`voter_c_feature_matrix`)
- Test: `tests/test_features_stage_relative.py`, create `tests/test_model_set_columns.py`

**Interfaces:**
- Produces, in `features.py`:
  - `MODEL_SETS: tuple[str, ...] = ("base", "clap")`, `DEFAULT_MODEL_SET: str = "base"`
  - `VOTER_C_COLUMN_NAMES: tuple[str, ...]`: every column of the full matrix, in order `hand | clap_sim_00..clap_sim_NN | clap_diff | cam_headcam, cam_handheld | rel_*`. `VOTER_C_FEATURE_DIM == len(VOTER_C_COLUMN_NAMES)`.
  - `model_set_columns(name: str) -> tuple[str, ...]`: `clap` is every column; `base` is every column whose name does not contain `clap`. Unknown name raises `ValueError` listing `MODEL_SETS`.
  - `select_columns(x: np.ndarray, columns: Sequence[str]) -> np.ndarray`: `(N, len(columns))` by name lookup in `VOTER_C_COLUMN_NAMES`; unknown name raises `KeyError`.
  - `voter_c_feature_matrix(hand_features, clap_sims, clap_diff, camera_classes=None) -> np.ndarray`: the full matrix, `gunshot_prob` parameter removed.
  - `stage_relative_features(hand, clap_sims, clap_diff) -> np.ndarray`; `REL_FEATURE_NAMES` loses `rel_gunshot_prob`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_model_set_columns.py`:
```python
import numpy as np
import pytest

from splitsmith.ensemble import features as feat


def test_column_names_cover_the_full_matrix() -> None:
    assert len(feat.VOTER_C_COLUMN_NAMES) == feat.VOTER_C_FEATURE_DIM
    assert len(set(feat.VOTER_C_COLUMN_NAMES)) == feat.VOTER_C_FEATURE_DIM
    assert not any("gunshot" in n for n in feat.VOTER_C_COLUMN_NAMES)


def test_base_has_no_clap_columns_and_clap_has_them_all() -> None:
    base = feat.model_set_columns("base")
    clap = feat.model_set_columns("clap")
    assert not any("clap" in n for n in base)
    assert clap == feat.VOTER_C_COLUMN_NAMES
    assert set(base) < set(clap)
    assert "confidence" in base and "cam_headcam" in base and "rel_log_peak_amp" in base or "rel_peak_amp" in base


def test_unknown_set_is_refused() -> None:
    with pytest.raises(ValueError, match="base"):
        feat.model_set_columns("pann")


def test_select_columns_picks_by_name_in_the_given_order() -> None:
    x = np.arange(2 * feat.VOTER_C_FEATURE_DIM, dtype=float).reshape(2, -1)
    names = (feat.VOTER_C_COLUMN_NAMES[3], feat.VOTER_C_COLUMN_NAMES[0])
    out = feat.select_columns(x, names)
    assert out.shape == (2, 2)
    assert out[0, 0] == 3.0 and out[0, 1] == 0.0
    with pytest.raises(KeyError):
        feat.select_columns(x, ("no_such_column",))


def test_full_matrix_has_no_gunshot_parameter() -> None:
    import inspect

    assert "gunshot_prob" not in inspect.signature(feat.voter_c_feature_matrix).parameters
    assert "gunshot_prob" not in inspect.signature(feat.stage_relative_features).parameters
```
Fix the `rel_*` name in the second test to the actual relative peak-amplitude column name once you have read `_REL_LOG_SOURCES` (`rel_peak_amp` if the source is `peak_amp`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_model_set_columns.py -n0 -v`
Expected: FAIL with `AttributeError: ... has no attribute 'MODEL_SETS'` (and the signature test fails on the `gunshot_prob` parameter).

- [ ] **Step 3: Implement the names and the selectors in `features.py`**

Build `VOTER_C_COLUMN_NAMES` from the existing pieces (`_HAND_FEATURE_NAMES`, `CLAP_PROMPTS` length, `CAMERA_CLASS_FEATURE_NAMES`, `REL_FEATURE_NAMES`) so it can never drift from the matrix; derive `VOTER_C_FEATURE_DIM` from it. Remove the `gunshot_prob` column and `rel_gunshot_prob`; update the docstring's column order.

- [ ] **Step 4: Fix the callers that break**

`grep -rn "voter_c_feature_matrix\|stage_relative_features" src scripts tests`: drop the `gunshot_prob` argument at each site (scripts keep working in Task 5 when PANN goes; here only the signature changes). `tests/test_features_stage_relative.py` asserts the relative block's width and names: update the expected names.

- [ ] **Step 5: Run the touched tests**

Run: `uv run pytest tests/test_model_set_columns.py tests/test_features_stage_relative.py tests/test_ensemble.py -n0 -q`
Expected: the first two files pass; `tests/test_ensemble.py` may fail on the stub runtime's `pann` field, which Task 5 fixes. Note any other failure here; it must be a signature change, nothing else.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ensemble/features.py tests/test_model_set_columns.py tests/test_features_stage_relative.py
git commit -m "feat(ensemble): name voter C columns and select a model set's columns by name"
```

---

### Task 2: Calibration schema with model sets

**Files:**
- Modify: `src/splitsmith/ensemble/calibration.py:97-300` (models), `:300-380` (`thresholds_for`), `:457-490` (loaders)
- Modify: `src/splitsmith/lab/model_map.py:243-270, 490-530, 695-720` (readers), `tests/test_lab_model_map.py:40-75` (calibration fixture)
- Test: create `tests/test_calibration_model_sets.py`

**Interfaces:**
- Produces, in `calibration.py`:
  - `class ModelSet(BaseModel)`: `columns: list[str]`, `voter_c_feature_dim: int`, `voter_c_onnx_artifacts: dict[str, str]`, `thresholds_by_camera_class: dict[str, ClassThresholds]`, `voter_c_metrics_by_camera_class: dict[str, dict[str, float]] = {}`, `decoders_by_camera_class: dict[str, StageDecoder] = {}`, `requires_artifacts: list[str] = []` (registry slugs; `["clap_audio_encoder", "clap_text_embeddings"]` for `clap`, empty for `base`). Method `thresholds_for(self, camera_class: str | None, default_camera_class: str) -> ClassThresholds` (same fallback rules as today's `EnsembleCalibration.thresholds_for`) and `decoder_for(self, camera_class: str | None, default_camera_class: str) -> StageDecoder | None`.
  - `ClassThresholds.voter_b_threshold: float | None = None`.
  - `EnsembleCalibration`: new `model_sets: dict[str, ModelSet]` (required) and `default_model_set: str = "base"`; removed `voter_a_floor`, `voter_b_threshold`, `voter_c_threshold`, `voter_e_threshold`, `voter_c_feature_dim`, `voter_c_onnx_artifacts`, `thresholds_by_camera_class`, `voter_c_metrics_by_camera_class`, `decoder`. Kept: everything else (`voter_c_target_recall`, `tolerance_ms`, the CLAP prompt lists, `calibration_fixtures`, `n_calibration_*`, `voter_e_*`, `built_at`, `default_camera_class`, `amp_floor_by_camera_model`, `camera_model_metadata`, `model_artifacts`, mining provenance). Method `model_set(self, name: str) -> ModelSet` raising `RuntimeError` naming the available sets. `thresholds_for(self, camera_class, *, model_set: str) -> ClassThresholds` delegates.
  - `load_calibration()` raises `RuntimeError` whose message contains `scripts/build_ensemble_artifacts.py` when the JSON has no `model_sets` key (before Pydantic validation).
- Produces, in `lab/model_map.py`: `model_set_block(cal: Mapping[str, Any], name: str) -> Mapping[str, Any]` is the one place the lab reads a set's fields, and `model_set_names(cal: Mapping[str, Any]) -> list[str]` lists them; a flat calibration from git history (no `model_sets`) is exposed as one set named `legacy` whose block is the flat top level, so the Models page's version history still renders older builds; only the runtime refuses the flat shape. `calibration_fixtures_by_class` reads `thresholds_by_camera_class` through the accessor; every lab figure names its set (add `model_set: str` to `VoterCHead` and `ModelVersion`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_calibration_model_sets.py`:
```python
import json

import pytest

from splitsmith.ensemble import features as feat
from splitsmith.ensemble.calibration import ClassThresholds, EnsembleCalibration, ModelSet, load_calibration


def _thresholds(b: float | None) -> ClassThresholds:
    return ClassThresholds(
        voter_a_floor=0.01, voter_b_threshold=b, voter_c_threshold=0.5,
        n_calibration_candidates=10, n_calibration_positives=5, calibration_fixtures=["x"],
    )


def _cal() -> EnsembleCalibration:
    base = ModelSet(
        columns=list(feat.model_set_columns("base")), voter_c_feature_dim=len(feat.model_set_columns("base")),
        voter_c_onnx_artifacts={"headcam": "b_h.onnx", "handheld": "b_a.onnx"},
        thresholds_by_camera_class={"headcam": _thresholds(None), "handheld": _thresholds(None)},
    )
    clap = base.model_copy(update={
        "columns": list(feat.VOTER_C_COLUMN_NAMES), "voter_c_feature_dim": feat.VOTER_C_FEATURE_DIM,
        "thresholds_by_camera_class": {"headcam": _thresholds(0.03), "handheld": _thresholds(0.007)},
        "requires_artifacts": ["clap_audio_encoder", "clap_text_embeddings"],
    })
    return EnsembleCalibration(
        voter_c_target_recall=0.95, tolerance_ms=75.0, clap_prompts_shot=list(feat.CLAP_PROMPTS_SHOT),
        clap_prompts=list(feat.CLAP_PROMPTS), calibration_fixtures=["x"], n_calibration_candidates=10,
        n_calibration_positives=5, built_at="2026-10-26T00:00:00+00:00", default_camera_class="headcam",
        model_sets={"base": base, "clap": clap},
    )


def test_each_set_is_read_by_name_and_an_unknown_set_names_the_others() -> None:
    cal = _cal()
    assert cal.model_set("base").requires_artifacts == []
    assert cal.model_set("clap").requires_artifacts == ["clap_audio_encoder", "clap_text_embeddings"]
    with pytest.raises(RuntimeError, match="base, clap"):
        cal.model_set("pann")


def test_thresholds_come_from_the_named_set() -> None:
    cal = _cal()
    assert cal.thresholds_for("handheld", model_set="clap").voter_b_threshold == 0.007
    assert cal.thresholds_for("handheld", model_set="base").voter_b_threshold is None


def test_unknown_camera_class_falls_to_the_default_class() -> None:
    cal = _cal()
    assert cal.thresholds_for(None, model_set="base") == cal.model_set("base").thresholds_by_camera_class["headcam"]
    assert cal.thresholds_for("drone", model_set="base") == cal.model_set("base").thresholds_by_camera_class["headcam"]


def test_a_flat_calibration_names_the_rebuild(tmp_path, monkeypatch) -> None:
    flat = {"voter_a_floor": 0.01, "voter_c_threshold": 0.5, "built_at": "x"}
    (tmp_path / "ensemble_calibration.json").write_text(json.dumps(flat))
    monkeypatch.setenv("SPLITSMITH_ARTIFACTS_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="build_ensemble_artifacts.py"):
        load_calibration()


def test_the_shipped_calibration_validates_with_two_sets() -> None:
    cal = load_calibration()
    assert set(cal.model_sets) == {"base", "clap"}
    for name, ms in cal.model_sets.items():
        assert ms.columns == list(feat.model_set_columns(name))
        assert ms.voter_c_feature_dim == len(ms.columns)
        assert set(ms.decoders_by_camera_class) == set(ms.thresholds_by_camera_class)
```
The last test fails until Task 10 ships the rebuilt calibration; mark it `@pytest.mark.xfail(strict=True, reason="until the rebuild in Task 10")` now and remove the marker in Task 10.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_calibration_model_sets.py -n0 -v`
Expected: FAIL with `ImportError: cannot import name 'ModelSet'`.

- [ ] **Step 3: Implement `ModelSet` and the new `EnsembleCalibration` in `calibration.py`**

`load_calibration()` reads the JSON, checks `"model_sets" in payload` before `model_validate`, and raises the rebuild error otherwise. `runtime.py`'s `SPLITSMITH_ARTIFACTS_DIR` already redirects `artifact()`; confirm the test's `monkeypatch.setenv` is honoured by `resolve_runtime` (the `runtime()` accessor may cache; use whatever reset the existing `tests/test_runtime.py` uses).

- [ ] **Step 4: Move the lab's readers onto `model_set_block`**

In `lab/model_map.py` every `cal.get("thresholds_by_camera_class")`, `cal.get("voter_c_onnx_artifacts")`, `cal.get("voter_c_metrics_by_camera_class")`, `cal.get("voter_c_feature_dim")` goes through `model_set_block(cal, name)`; the page iterates `cal["model_sets"]` and labels each head with its set. Update the calibration fixture in `tests/test_lab_model_map.py` to the two-set shape and the expectations that depend on it. `model_bundle.calibration_of` is unchanged (it returns the dict).

- [ ] **Step 5: Run the tests, then the suite, and mark the shipped-calibration readers**

Run: `uv run pytest tests/test_calibration_model_sets.py tests/test_lab_model_map.py tests/test_runtime.py -n0 -q`
Expected: PASS except the xfail.

Run: `uv run pytest -n 4 -q`
Expected: the only failures read the shipped calibration or graphs. Mark each with the strict xfail from Global Constraints and list them in the commit message; a failure of any other kind is a defect in this task.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ensemble/calibration.py src/splitsmith/lab/model_map.py tests/test_calibration_model_sets.py tests/test_lab_model_map.py
git commit -m "feat(ensemble): calibration carries model sets, each with its own thresholds and decoders"
```

---

### Task 3: Decoder features from the decoder's own list

**Files:**
- Modify: `src/splitsmith/ensemble/decoder.py:41-48, 98-118`
- Modify: `scripts/fit_stage_decoder.py:109-125` (`_x`, `_logistic`), `:150-243` (`fit_stage_decoder` signature)
- Test: `tests/test_stage_decoder.py`

**Interfaces:**
- Produces, in `decoder.py`:
  - `FEATURES: tuple[str, ...] = ("score_c", "clap_diff", "confidence", "log_peak_amplitude", "vote_b")` (every known name; `gunshot_prob` gone).
  - `DECODER_FEATURES_BY_SET: dict[str, tuple[str, ...]] = {"base": ("score_c", "confidence", "log_peak_amplitude"), "clap": FEATURES}`.
  - `decoder_features(model: StageDecoder, signals: Mapping[str, np.ndarray]) -> np.ndarray`: columns in `model.features` order; `log_peak_amplitude` is computed from `signals["peak_amplitude"]` as today (`log(max(x, 1e-4))`); a name in `model.features` that is neither a signal key nor `log_peak_amplitude` raises `KeyError`.
- Produces, in `fit_stage_decoder.py`: `fit_stage_decoder(rows, stages, *, features: Sequence[str], tolerance_ms, fast_floor_s=None, log=print) -> StageDecoder | None`; rows carry `score_c`, `confidence`, `peak_amp`, and `clap_diff`, `vote_b` only when `features` name them. The fitted decoder's `features` equals `list(features)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stage_decoder.py`:
```python
def test_decoder_features_follow_the_decoder_s_own_list() -> None:
    from splitsmith.ensemble.decoder import DECODER_FEATURES_BY_SET, StageDecoder, decoder_features

    base = _decoder(features=list(DECODER_FEATURES_BY_SET["base"]))  # reuse the module's decoder factory
    x = decoder_features(base, {"score_c": np.array([0.9]), "confidence": np.array([0.5]), "peak_amplitude": np.array([0.1])})
    assert x.shape == (1, 3)
    assert x[0, 2] == pytest.approx(np.log(0.1))
    with pytest.raises(KeyError, match="clap_diff"):
        decoder_features(_decoder(features=["score_c", "clap_diff"]), {"score_c": np.array([0.9])})


def test_the_fit_without_clap_signals_uses_three_features() -> None:
    from splitsmith.ensemble.decoder import DECODER_FEATURES_BY_SET

    rows, stages = _two_match_rows()  # the fixture the existing fit test uses, without clap_diff / vote_b keys
    model = fit_stage_decoder.fit_stage_decoder(rows, stages, features=DECODER_FEATURES_BY_SET["base"], tolerance_ms=75.0)
    assert model is not None
    assert model.features == list(DECODER_FEATURES_BY_SET["base"])
    assert len(model.coef) == 3
```
Read the module's existing helpers first (`test_the_fit_learns_to_keep_shots_and_drop_their_echoes` builds rows); name `_decoder` and `_two_match_rows` after what exists, adding `features` to the factory.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_stage_decoder.py -n0 -v -k "own_list or three_features"`
Expected: FAIL with `ImportError: cannot import name 'DECODER_FEATURES_BY_SET'`.

- [ ] **Step 3: Implement in `decoder.py` and `fit_stage_decoder.py`**

`fit_stage_decoder._x(rows, features)` builds the signals dict from the row keys and calls `decoder_features` on a throwaway `StageDecoder` with `features` (or a small helper that takes the list directly; both call sites must use the same code path, the spec's "cannot drift" rule).

- [ ] **Step 4: Run the whole decoder test file**

Run: `uv run pytest tests/test_stage_decoder.py -n0 -q`
Expected: PASS. `test_the_calibration_carries_a_decoder_and_loads_without_one` and the two runtime tests at the end of the file may fail on the calibration shape: convert them to the `model_sets` shape from Task 2 (`decoders_by_camera_class` under a set) and keep their assertions.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ensemble/decoder.py scripts/fit_stage_decoder.py tests/test_stage_decoder.py
git commit -m "feat(decoder): build the recalibration matrix from the decoder's own feature list"
```

---

### Task 4: Retire PANN

**Files:**
- Modify: `src/splitsmith/ensemble/features.py:60-62, 168-180, 631-748`; `src/splitsmith/ensemble/api.py:143-280`; `src/splitsmith/ensemble/backend.py` (docstring and the torch-first comment); `src/splitsmith/models/manifest.py:37-45` (docstring); `src/splitsmith/detection_merge.py:166-200`; `src/splitsmith/lab/core.py:402, 972`; `src/splitsmith/mcp/detect_tools.py`; `src/splitsmith/cli.py`, `src/splitsmith/runtime.py`, `src/splitsmith/ensemble/__init__.py`, `tta.py`, `onnx_session.py`, `visual.py` (one mention each)
- Delete: `scripts/export_pann_onnx.py`, `scripts/extract_audio_embeddings.py`
- Modify: `scripts/feature_caches.py:32, 69-90`; `scripts/build_ensemble_artifacts.py:149-255, 370-400`; `scripts/eval_ensemble.py`, `scripts/build_sweep_signals.py`, `scripts/build_ensemble_fixture.py`, `scripts/verify_onnx_parity.py`, `scripts/upload_model_artifacts.py`, `scripts/train_classifier.py`, `scripts/lofo_mining_experiment.py`, `scripts/warm_numba_cache.py`; `scripts/ci/assert_slim_import_surface.py`
- Test: `tests/test_onnx_parity.py:57-137` (delete the three PANN tests), `tests/test_ensemble.py:420-450`, `tests/test_export_run_record.py`, `tests/test_fcpxml_gen.py`, `tests/test_coach_classify.py`, `tests/test_compare_overlay_summary.py`, `tests/ui/test_stages_api.py`, `tests/test_mcp_detect_tools.py`, `tests/test_models_layer.py`, `tests/test_build_ensemble_preconditions.py`; create `tests/test_pann_is_gone.py`

**Interfaces:**
- Produces: `EnsembleRuntime` has no `pann` field; `EnsembleCandidate` has no `gunshot_prob`; `detection_merge.candidate_dict` writes no `gunshot_prob`; `feature_caches.KINDS == ("clap",)`; `_build_universe` rows have no `gunshot_prob`; `lab.core` candidate model's `gunshot_prob: float | None = None` (old docs still load).

- [ ] **Step 1: Write the failing tests**

`tests/test_pann_is_gone.py`:
```python
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_no_runtime_or_build_code_references_pann() -> None:
    out = subprocess.run(
        ["git", "grep", "-i", "-n", "-E", r"pann|gunshot_prob", "--", "src/splitsmith", "scripts"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    code = [l for l in out if not any(t in l for t in ("#", '"""', "docs/", "findings"))]
    allowed = {"src/splitsmith/lab/core.py"}  # optional field for old audit docs
    assert [l for l in code if l.split(":")[0] not in allowed] == []


def test_fetch_models_lists_only_clap() -> None:
    from splitsmith.models.registry import load_spec_from_calibration

    spec = load_spec_from_calibration()
    assert spec is not None
    assert sorted(spec.slugs()) == ["clap_audio_encoder", "clap_text_embeddings"]


def test_old_candidate_rows_still_load() -> None:
    from splitsmith.lab.core import LabCandidate  # name per lab/core.py:402's model

    row = {"candidate_number": 1, "time": 1.0, "score_c": 0.5, "clap_diff": 0.0, "gunshot_prob": 0.9}
    assert LabCandidate.model_validate({**row, **_required_fields()}).gunshot_prob == 0.9
    assert LabCandidate.model_validate({k: v for k, v in {**row, **_required_fields()}.items() if k != "gunshot_prob"}).gunshot_prob is None
```
`_required_fields()` fills whatever else the lab model requires; read the model at `lab/core.py:402` first and name it exactly. `test_fetch_models_lists_only_clap` fails until Task 10 drops the manifest entry; mark it `xfail(strict=True)` until then.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pann_is_gone.py -n0 -v`
Expected: the grep test FAILS listing the PANN sites; the lab test fails or passes depending on the current model (the field is required today, so the second validate raises).

- [ ] **Step 3: Remove PANN from the runtime**

`features.py`: delete `PANN_*` constants, `PannRuntime`, `_build_pann_runtime_torch`, `PANN_ARTIFACT_SLUG`, `ENV_ONNX_PANN_OVERRIDE`, `_resolve_onnx_pann_path`, `_build_pann_runtime_onnx`, `load_pann_runtime`, `compute_pann_gunshot_probs`. `api.py`: drop `pann` from `EnsembleRuntime` and `load_ensemble_runtime`, drop `gunshot_prob` from `EnsembleCandidate` and the detect loop; the decoder call becomes `dec.decoder_features(decoder, {"score_c": score_c, "clap_diff": clap_diff, "confidence": confidences, "peak_amplitude": peak_amps, "vote_b": vb})` (Task 7 wires the decoder lookup per set; here read it from `cal.model_set("clap")` so the suite stays green between tasks). `backend.py`: the resolution order comment says CLAP and voter E are ONNX now, onnxruntime wins when both are importable (the comment's own stated intent); keep the env override. `detection_merge.py`: drop the key; `CANDIDATES_NOTE` becomes `"3-voter ensemble (A envelope, B CLAP when the clap model set is active, C GBDT). ..."` keeping the rest of the sentence. `lab/core.py`: optional field. The single-mention files: update the comment or docstring.

- [ ] **Step 4: Remove PANN from the build and the lab scripts**

`feature_caches.py`: `KINDS = ("clap",)`, `_extract` loads only CLAP. `build_ensemble_artifacts._build_universe`: no `pann_path`, no `gunshot_prob` key, the skip message names only `extract_clap_features.py`; `_x_from` and `_fit_decoder` drop the column (Task 8 rewrites `_fit_decoder` anyway; here just make it run). Delete the two scripts. In every other listed script, remove the PANN cache read and the `gunshot_prob` column; each must still start: `uv run python scripts/<name>.py --help` exits 0. `scripts/ci/assert_slim_import_surface.py`: remove `panns_inference` from whatever list it checks.

- [ ] **Step 5: Fix the tests that carried `gunshot_prob`**

`grep -rln "gunshot_prob\|_StubPannRuntime\|pann" tests` and remove the field from candidate dicts and stub runtimes; delete the three PANN parity tests in `tests/test_onnx_parity.py`; in `tests/test_models_layer.py` replace any `pann_cnn14` slug in fixtures with a CLAP slug.

- [ ] **Step 6: Regenerate the wire types**

Run:
```bash
uv run python scripts/export_openapi.py
cd src/splitsmith/ui_static && pnpm gen:api && cd -
git grep -n "gunshot_prob\|gunshotProb" src/splitsmith/ui_static/src
```
Expected: the grep lists only the three lab test files from the spec's list (`DevFixtureDetail.test.tsx`, `LabelDropdown.test.tsx`, `StepThroughPanel.test.tsx`); remove the field from their fixtures, and from `lib/api.ts` if it is hand-written there.

- [ ] **Step 7: Run the suite**

Run: `uv run pytest -n 4 -q` and `cd src/splitsmith/ui_static && pnpm test -- --run`
Expected: PASS except the two strict xfails. `ruff check src scripts tests` clean.

- [ ] **Step 8: Commit**

```bash
git add -u src scripts tests
git add tests/test_pann_is_gone.py src/splitsmith/ui_static/openapi.json src/splitsmith/ui_static/src/lib/api/types.gen.ts
git rm scripts/export_pann_onnx.py scripts/extract_audio_embeddings.py
git commit -m "refactor(ensemble): retire PANN from the runtime, the build and the caches"
```
(`git add -u` stages tracked files only; check `git status` shows no `uv.lock` or fixture cache.)

---

### Task 5: Fix the ensemble test stubs for the new runtime shape

**Files:**
- Modify: `tests/test_ensemble.py:420-450` and every `EnsembleRuntime(` / `EnsembleCalibration(` construction under `tests/` and `tests/ui/`

**Interfaces:**
- Consumes: Task 2's `ModelSet`, Task 4's `EnsembleRuntime` (no `pann`).
- Produces: `tests/_ensemble_stubs.py` with `stub_calibration(*, with_clap: bool = True) -> EnsembleCalibration` and `stub_runtime(model_set: str = "clap") -> EnsembleRuntime`, used by every test that needs one (tests/ui/_helpers.py included).

- [ ] **Step 1: Write the helper and point the stubs at it**

`stub_calibration` builds the two-set calibration of Task 2's test (`_cal()`), with a decoder per class per set from `tests/test_stage_decoder.py`'s factory; `stub_runtime` builds `EnsembleRuntime(calibration=..., model_set=..., voter_c_model={cls: _StubGBDT()}, clap=_StubClapRuntime() if model_set == "clap" else None, expected_prompts=...)`. Note `model_set` and the optional `clap` land on `EnsembleRuntime` in Task 7; add them here as the dataclass fields now (`model_set: str = "clap"`, `clap: feat.ClapRuntime | None`) so Task 7 only changes behaviour.

- [ ] **Step 2: Run the suite**

Run: `uv run pytest -n 4 -q`
Expected: PASS (minus strict xfails). Every stub-building test now goes through the helper; `git grep -n "EnsembleCalibration(" tests` shows only `tests/_ensemble_stubs.py` and `tests/test_calibration_model_sets.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/_ensemble_stubs.py tests/test_ensemble.py tests/ui/_helpers.py $(git diff --name-only tests)
git commit -m "test: one stub calibration and runtime for the two model sets"
```

---

### Task 6: Model-set resolution and the result stamp

**Files:**
- Modify: `src/splitsmith/ensemble/api.py:33-60, 209-217, 234-280, 325-480`
- Modify: `src/splitsmith/compute.py:31-65`
- Modify: `src/splitsmith/detection_merge.py:218-310`
- Test: `tests/test_ensemble.py`; create `tests/test_detection_merge.py` (the existing `tests/test_detection_merge_clip_video.py` covers another concern; leave it)

**Interfaces:**
- Produces, in `api.py`:
  - `ENV_MODEL_SET = "SPLITSMITH_MODEL_SET"`.
  - `resolve_model_set(preference: str | None = None) -> str`: env wins, then `preference`, then `feat.DEFAULT_MODEL_SET`; a value outside `feat.MODEL_SETS` raises `ValueError` naming them.
  - `EnsembleRuntime.model_set: str`, `EnsembleRuntime.clap: feat.ClapRuntime | None`.
  - `EnsembleResult.model_set: str`, `EnsembleResult.calibration_built_at: str`.
  - `detect_shots_ensemble(...)`: features selected with `feat.select_columns(full, cal.model_set(runtime.model_set).columns)`; `clap_sims`/`clap_diff` computed only when `runtime.clap is not None`, else zeros and `vb` zeros; thresholds and decoder from the runtime's set and the camera class; `vote_total = va + vb + vc` unchanged (under `base` the consensus escape hatch is A and C with `consensus=2`, which the zeros give for free).
- Produces, in `compute.py`: `LocalComputeBackend.detect_stage(..., model_set: str | None = None)`; `runtime_loader: Callable[[str | None], EnsembleRuntime]` (default `load_ensemble_runtime` wrapped as `lambda s: load_ensemble_runtime(model_set=s)`); `_ensure_runtime(model_set)` keeps `self._runtimes: dict[str, EnsembleRuntime]` keyed by the **returned** runtime's `model_set` (same rule as the server in Task 7: a `clap` request that came back `base` is cached under `base` and asked for again next time).
- Produces, in `detection_merge.py`: `_candidates_pending_audit` gains `_model_set` and `_calibration_built_at`; the `shot_detect_run` event payload gains `model_set` and `calibration_built_at`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ensemble.py`:
```python
def test_resolve_model_set_prefers_env_then_preference_then_base(monkeypatch) -> None:
    from splitsmith.ensemble.api import resolve_model_set

    monkeypatch.delenv("SPLITSMITH_MODEL_SET", raising=False)
    assert resolve_model_set() == "base"
    assert resolve_model_set("clap") == "clap"
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "base")
    assert resolve_model_set("clap") == "base"
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "pann")
    with pytest.raises(ValueError, match="base, clap"):
        resolve_model_set()


def test_base_runtime_never_computes_clap(monkeypatch) -> None:
    from tests._ensemble_stubs import stub_runtime

    called = []
    monkeypatch.setattr(feat, "compute_clap_similarities", lambda *a, **k: called.append(1))
    runtime = stub_runtime("base")
    result = detect_shots_ensemble(_audio(), 48000, 1.0, 5.0, runtime, camera_class="handheld")  # module's audio helper
    assert called == []
    assert result.model_set == "base"
    assert all(c.vote_b == 0 and c.clap_diff == 0.0 for c in result.candidates)
    assert result.calibration_built_at == runtime.calibration.built_at


def test_clap_runtime_scores_the_full_column_set() -> None:
    from tests._ensemble_stubs import stub_runtime

    seen = {}

    class _Width(_StubGBDT):
        def predict_proba(self, x):
            seen["width"] = x.shape[1]
            return super().predict_proba(x)

    runtime = stub_runtime("clap")
    runtime.voter_c_model = {"headcam": _Width(), "handheld": _Width()}
    detect_shots_ensemble(_audio(), 48000, 1.0, 5.0, runtime, camera_class="headcam")
    assert seen["width"] == feat.VOTER_C_FEATURE_DIM
```
Add `test_base_width` the same way asserting `len(feat.model_set_columns("base"))`, and:
```python
def test_base_runtime_constructs_no_clap_session(monkeypatch) -> None:
    from tests._ensemble_stubs import stub_calibration

    monkeypatch.setattr(feat, "load_clap_runtime", lambda: (_ for _ in ()).throw(AssertionError("CLAP loaded under base")))
    monkeypatch.setattr(api, "load_calibration", lambda: stub_calibration())
    monkeypatch.setattr(api, "load_voter_c_model", lambda artifacts: {cls: _StubGBDT() for cls in artifacts})
    rt = api.load_ensemble_runtime(model_set="base", with_voter_e=False)
    assert rt.model_set == "base" and rt.clap is None
```

In `tests/test_detection_merge.py`:
```python
def test_the_merge_records_the_model_set_on_the_block_and_the_run_event() -> None:
    doc = merge_detection_into({}, _result(model_set="base", calibration_built_at="2026-10-26T00:00:00+00:00"), base=_base(), stage_rounds=None, reset=False)
    assert doc["_candidates_pending_audit"]["_model_set"] == "base"
    assert doc["_candidates_pending_audit"]["_calibration_built_at"] == "2026-10-26T00:00:00+00:00"
    run = [e for e in doc["audit_events"] if e["kind"] == "shot_detect_run"][-1]
    assert run["payload"]["model_set"] == "base"
    assert run["payload"]["calibration_built_at"] == "2026-10-26T00:00:00+00:00"
```
`_result` builds an `EnsembleResult` with one candidate; `_base` the base-fields mapping the merge requires (see `detection_merge.merge_detection_into`'s docstring).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_ensemble.py tests/test_detection_merge.py -n0 -v -k "model_set or never_computes or column_set"`
Expected: FAIL (`resolve_model_set` missing; `model_set` not a field).

- [ ] **Step 3: Implement in `api.py`, `compute.py`, `detection_merge.py`**

`load_ensemble_runtime(*, model_set: str | None = None, with_voter_e: bool = True)`: `name = resolve_model_set(model_set)`; width check per set (`cal.model_set(name).voter_c_feature_dim` against each graph's `n_features`); `clap = feat.load_clap_runtime() if name == "clap" else None`. The fallback on missing artifacts is Task 7's; here a missing artifact still raises.

- [ ] **Step 4: Run the ensemble and merge tests, then the suite**

Run: `uv run pytest tests/test_ensemble.py tests/test_detection_merge.py tests/test_stage_decoder.py -n0 -q && uv run pytest -n 4 -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ensemble/api.py src/splitsmith/compute.py src/splitsmith/detection_merge.py tests/test_ensemble.py tests/test_detection_merge.py
git commit -m "feat(ensemble): resolve a model set at runtime and stamp it on every detection"
```

---

### Task 7: The server picks the set, caches per set, and falls back to base

**Files:**
- Modify: `src/splitsmith/ui/server.py:1585-1631` (`_get_ensemble_runtime`, `_ensemble_runtime_is_warm`, `warm_ensemble_runtime`), `:4069-4102` (the shot-detect job), `:7001, 9293` (`LocalComputeBackend` wiring)
- Modify: `src/splitsmith/ensemble/api.py:234-280` (fallback)
- Modify: `src/splitsmith/user_config.py:143-166`
- Test: `tests/test_ensemble.py`, `tests/ui/test_stages_api.py`

**Interfaces:**
- Consumes: `splitsmith.models.errors.ModelError` (the registry's base error for a missing, unreachable or mismatched artifact). It subclasses `RuntimeError`, so the fallback catches `ModelError` by its own class and `OSError`, never `RuntimeError`.
- Produces:
  - `GlobalPrefs.detection_model_set: str = "base"` (no schema bump: a missing key takes the default).
  - `api.load_ensemble_runtime`: when `name == "clap"` and loading CLAP raises `ModelError` or `OSError`, log a warning naming the slug and return the `base` runtime (`model_set == "base"`); any other exception propagates.
  - `server._get_ensemble_runtime(model_set: str | None = None) -> EnsembleRuntime`: resolves through `resolve_model_set(prefs.detection_model_set)` in local mode and `resolve_model_set(None)` hosted; caches runtimes in `_ENSEMBLE_RUNTIMES: dict[str, EnsembleRuntime]` keyed by the **returned** runtime's `model_set`, so a `clap` request that fell back is served from the `base` entry and retried next time. `_ensemble_runtime_is_warm(model_set)` and `warm_ensemble_runtime()` (warms the resolved set) follow.
  - The shot-detect job passes `model_set` to `state.compute.detect_stage`, and the job's progress message names the set (`"Detecting shots (base)"` / `"(clap)"`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_ensemble.py`:
```python
def test_clap_requested_without_artifacts_runs_base_and_says_so(monkeypatch, caplog) -> None:
    from splitsmith.models.errors import ModelError

    def _boom():
        raise ModelError("clap_audio_encoder: not cached and no network")

    monkeypatch.setattr(feat, "load_clap_runtime", _boom)
    monkeypatch.setattr(api, "load_calibration", lambda: stub_calibration())
    monkeypatch.setattr(api, "load_voter_c_model", lambda artifacts: {cls: _StubGBDT() for cls in artifacts})
    runtime = api.load_ensemble_runtime(model_set="clap", with_voter_e=False)
    assert runtime.model_set == "base"
    assert runtime.clap is None
    assert "clap_audio_encoder" in caplog.text


def test_other_errors_while_loading_clap_propagate(monkeypatch) -> None:
    monkeypatch.setattr(feat, "load_clap_runtime", lambda: (_ for _ in ()).throw(RuntimeError("prompt bank drift")))
    monkeypatch.setattr(api, "load_calibration", lambda: stub_calibration())
    monkeypatch.setattr(api, "load_voter_c_model", lambda artifacts: {cls: _StubGBDT() for cls in artifacts})
    with pytest.raises(RuntimeError, match="prompt bank"):
        api.load_ensemble_runtime(model_set="clap", with_voter_e=False)
```
In `tests/ui/test_stages_api.py` (follow the file's existing shot-detect job test, which monkeypatches `server._get_ensemble_runtime` and `detect_shots_ensemble`):
```python
def test_shot_detect_uses_the_preference_and_caches_by_the_set_it_got(client, monkeypatch, ...) -> None:
    # prefs say clap; the loader returns a base runtime (fallback)
    seen = []
    monkeypatch.setattr(server.ensemble_module, "load_ensemble_runtime", lambda **kw: seen.append(kw["model_set"]) or stub_runtime("base"))
    ...set GlobalPrefs.detection_model_set = "clap" through user_config.save_global_prefs...
    server._ENSEMBLE_RUNTIMES.clear()
    rt1 = server._get_ensemble_runtime()
    rt2 = server._get_ensemble_runtime()
    assert seen == ["clap", "clap"]          # retried, because the fallback was not cached as clap
    assert rt1.model_set == rt2.model_set == "base"
    assert list(server._ENSEMBLE_RUNTIMES) == ["base"]
```
and one asserting the audit doc the job writes has `_candidates_pending_audit._model_set == "base"` (extend the existing shot-detect job test's assertions).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_ensemble.py tests/ui/test_stages_api.py -n0 -v -k "without_artifacts or propagate or caches_by_the_set"`
Expected: FAIL (`_ENSEMBLE_RUNTIMES` missing; no fallback).

- [ ] **Step 3: Implement**

`server._get_ensemble_runtime` keeps the lock; the hosted check is `_hosted_mode_active()` (already in the file). The job at `server.py:4069-4102` reads the set once, passes it to `detect_stage`, and uses it in the two `handle.update` messages. Replace the three `_ENSEMBLE_RUNTIME` readers listed in the Files block.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -n 4 -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py src/splitsmith/ensemble/api.py src/splitsmith/user_config.py tests/test_ensemble.py tests/ui/test_stages_api.py
git commit -m "feat(ui): detect with the preferred model set, cache runtimes per set, fall back to base"
```

---

### Task 8: The build trains both sets and one decoder per class

**Files:**
- Modify: `scripts/build_ensemble_artifacts.py:362-400, 574-760, 789-885, 1106-1201, 1285-1607`
- Test: `tests/test_build_ensemble_preconditions.py`, `tests/test_model_builds.py` (read it; it pins the bundle layout)

**Interfaces:**
- Consumes: `feat.model_set_columns`, `feat.select_columns`, `dec.DECODER_FEATURES_BY_SET`, `fit_stage_decoder(features=...)`.
- Produces, in the build script (importable by Task 10's test):
  - `_train_model_set(name: str, by_class: dict[str, list[dict]], *, target_recall: float, log) -> ModelSetBuild` where `ModelSetBuild` is a dataclass: `models: dict[str, GradientBoostingClassifier]`, `block: dict` (the `ModelSet` JSON: `columns`, `voter_c_feature_dim`, `thresholds_by_camera_class`, `voter_c_metrics_by_camera_class`, `requires_artifacts`; `voter_c_onnx_artifacts` filled at export), `heldout_report: dict[str, dict]`, `heldout_rows: list[dict]`.
  - `_fit_decoders_by_class(name: str, universe, heldout_rows, thresholds_by_class, tolerance_ms, fast_floor_s, log) -> dict[str, StageDecoder]`: one `fit_stage_decoder(..., features=DECODER_FEATURES_BY_SET[name])` call per class over that class's rows; `clap_diff`/`vote_b` rows only for `clap`.
  - The calibration JSON: `model_sets: {name: block}`, `default_model_set: "base"`, the kept top-level fields; `voter_c_onnx_artifacts[cls] == f"voter_c_gbdt_{name}_{cls}.onnx"`.
  - `build/ensemble_heldout/report.json` becomes `{"model_sets": {name: {"by_camera_class": {...}}}, "target_recall": ..., "tolerance_ms": ...}`; `voter_c_oof.parquet` gets a `model_set` column. `scripts/build_sweep_signals.py` reads the `clap` set's rows.
  - Parity references: `tests/data/voter_c_parity_reference_{name}.npz`, written by `_write_voter_c_parity_reference(name, ...)`; `tests/test_onnx_parity.py` loops over `feat.MODEL_SETS`.
  - `_carry_model_artifacts` drops `pann_cnn14` from the carried block.
  - `_assert_sets_agree(blocks: dict[str, dict]) -> None` raises `BuildError` (message contains `fixture`) when any two sets' per-class `calibration_fixtures` differ (they are computed from one universe, so the check guards the code, not the data); the build also raises when an exported graph's input width differs from its block's `voter_c_feature_dim`.
  - `--decoder-only` refits every class's decoder for both sets.

- [ ] **Step 1: Write the failing tests**

In `tests/test_build_ensemble_preconditions.py`:
```python
def test_both_sets_share_one_fixture_list_and_the_build_refuses_otherwise(monkeypatch) -> None:
    import build_ensemble_artifacts as b

    universe = _tiny_universe()  # two classes, two matches, a handful of rows with hand_feats / clap_sims / clap_diff
    by_class = b._split_by_camera_class(universe)
    base = b._train_model_set("base", by_class, target_recall=0.95, log=lambda s: None)
    clap = b._train_model_set("clap", by_class, target_recall=0.95, log=lambda s: None)
    assert base.block["columns"] == list(feat.model_set_columns("base"))
    assert clap.block["requires_artifacts"] == ["clap_audio_encoder", "clap_text_embeddings"]
    assert base.block["thresholds_by_camera_class"]["headcam"]["voter_b_threshold"] is None
    for cls in by_class:
        assert base.block["thresholds_by_camera_class"][cls]["calibration_fixtures"] == clap.block["thresholds_by_camera_class"][cls]["calibration_fixtures"]
    clap.block["thresholds_by_camera_class"]["headcam"]["calibration_fixtures"] = ["other"]
    with pytest.raises(b.BuildError, match="fixture"):
        b._assert_sets_agree({"base": base.block, "clap": clap.block})


def test_the_carried_artifact_block_drops_pann(tmp_path) -> None:
    cal = {"model_artifacts": {"pann_cnn14": {...}, "clap_audio_encoder": {...}, "clap_text_embeddings": {...}}}
    (tmp_path / "ensemble_calibration.json").write_text(json.dumps(cal))
    carried = b._carry_model_artifacts(tmp_path / "ensemble_calibration.json")
    assert set(carried) == {"clap_audio_encoder", "clap_text_embeddings"}
```
`_tiny_universe()` can be the one the existing `test_model_builds.py` or the stage-decoder tests use for a fit; the GBDT needs at least 5 positives per class (`_train_voter_c_for_class`'s rule), so give each class 6 positives and 6 negatives across two matches with distinct `event_id`-derived groups (`_event_groups` reads the fixture JSON; monkeypatch it to return `(row["match"], row["match"] + ":1")`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_build_ensemble_preconditions.py -n0 -v`
Expected: FAIL with `AttributeError: module has no attribute '_train_model_set'`.

- [ ] **Step 3: Implement the per-set training, the per-class decoders, the schema write and the checks**

Keep `_train_voter_c_for_class` as the inner worker, now taking the column list (`X = feat.select_columns(_x_from(rows), columns)`). The old top-level `voter_a_floor` etc. are not written. The held-out report writer takes the set name. The parity writer takes the set name and writes `tests/data/voter_c_parity_reference_{name}.npz`; delete the old `voter_c_parity_reference.npz` and update `tests/test_onnx_parity.py::test_voter_c_onnx_matches_frozen_sklearn_reference` and `..._vote_decisions_match_reference` to loop over the sets (they stay red until Task 10 regenerates the references; mark them strict xfail until then).

- [ ] **Step 4: Run the build-side tests and the suite**

Run: `uv run pytest tests/test_build_ensemble_preconditions.py tests/test_model_builds.py tests/test_stage_decoder.py -n0 -q && uv run pytest -n 4 -q`
Expected: PASS (minus strict xfails).

- [ ] **Step 5: Commit**

```bash
git add scripts/build_ensemble_artifacts.py scripts/build_sweep_signals.py tests/test_build_ensemble_preconditions.py tests/test_onnx_parity.py tests/test_model_builds.py
git commit -m "feat(build): train base and clap model sets from one universe, one decoder per class"
```

---

### Task 9: CI artifact assertion and `fetch-models` follow the sets

**Files:**
- Modify: `scripts/ci/assert_ensemble_artifacts.py:60-120`, `src/splitsmith/model_cli.py:32-75` (copy mentions PANN sizes), `scripts/upload_model_artifacts.py`
- Test: `tests/test_models_layer.py`

**Interfaces:**
- Produces: `assert_ensemble_artifacts.py` iterates `calibration["model_sets"]` and checks each class graph's width against that set's `voter_c_feature_dim`; exit 1 names the set and class.

- [ ] **Step 1: Write the failing test**

In `tests/test_models_layer.py`:
```python
def test_artifact_assertion_checks_every_set(tmp_path, monkeypatch) -> None:
    # two sets, one graph per class; the clap headcam graph is replaced by a base-width graph
    ...write a calibration with model_sets and copy the shipped onnx files into tmp_path under the set names...
    proc = subprocess.run([sys.executable, "scripts/ci/assert_ensemble_artifacts.py"], env={**os.environ, "SPLITSMITH_ARTIFACTS_DIR": str(tmp_path)}, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 1
    assert "clap" in proc.stderr and "headcam" in proc.stderr
```
(The shipped graphs exist only after Task 10; until then build two throwaway graphs with skl2onnx from a 3-row fit inside the test, the way `tests/test_onnx_parity.py` builds its references, or mark the test strict xfail until Task 10.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_models_layer.py -n0 -v -k every_set`
Expected: FAIL (the script reads `voter_c_onnx_artifacts` at the top level and exits 1 for the wrong reason: assert the message names the set, which it cannot yet).

- [ ] **Step 3: Implement**

The script's `main` loops sets then classes; `model_cli._no_artifacts_block` and `_print_status_table` copy no longer mention 430 MB or PANN; `upload_model_artifacts.py` uploads only the slugs in the manifest.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_models_layer.py -n0 -q`
Expected: PASS.
```bash
git add scripts/ci/assert_ensemble_artifacts.py src/splitsmith/model_cli.py scripts/upload_model_artifacts.py tests/test_models_layer.py
git commit -m "ci: assert every model set's graphs against its own feature width"
```

---

### Task 10: Rebuild on the whole corpus, ship it, and clear the gate

**Files:**
- Modify (generated): `src/splitsmith/data/ensemble_calibration.json`, `src/splitsmith/data/voter_c_gbdt_{base,clap}_{headcam,handheld}.onnx` (delete the two old graphs), `tests/data/voter_c_parity_reference_{base,clap}.npz` (delete the old one), `build/ensemble_heldout/` (not committed)
- Modify: `tests/test_calibration_model_sets.py`, `tests/test_pann_is_gone.py`, `tests/test_onnx_parity.py`, `tests/test_models_layer.py` (remove the strict xfails)
- Create: `docs/ensemble_dashboard/findings/2026-10-26_thin_detection_model_sets.md`
- Modify: `docs/cameras.md`, `.claude/rules/shot-detection.md`

- [ ] **Step 1: Build**

Run (on gaspode, nothing else heavy running):
```bash
uv run python scripts/fixture_audio.py fetch
uv run python scripts/build_ensemble_artifacts.py --extract 2>&1 | tee ~/.claude-tmp/thin-detect/build-full.log
uv run python scripts/build_ensemble_artifacts.py --reviewed-only --out build/models/reviewed-only-2026-10-26 2>&1 | tee ~/.claude-tmp/thin-detect/build-reviewed.log
```
Expected: both complete. The first writes the shipped artifacts and `build/ensemble_heldout/report.json`; the second a bundle for the comparison the rule file asks for.

- [ ] **Step 2: Produce the gate table**

Run:
```bash
uv run python - <<'EOF'
import json
new = json.load(open("build/ensemble_heldout/report.json"))["model_sets"]
old = json.load(open("build/models/baseline-2026-10-26/heldout/report.json"))["by_camera_class"]
cal = json.load(open("src/splitsmith/data/ensemble_calibration.json"))
for cls in ("handheld", "headcam"):
    o = old[cls]["lomo"]["at_own_threshold"]["f1"]
    for name in ("clap", "base"):
        n = new[name]["by_camera_class"][cls]["lomo"]["at_own_threshold"]["f1"]
        print(f"{cls:9s} {name:5s} candidate LOMO F1 {n:.4f}  (old columns {o:.4f}, delta {n-o:+.4f})")
        for m, v in new[name]["by_camera_class"][cls]["lomo_by_match"].items():
            d = v["f1"] - old[cls]["lomo_by_match"].get(m, {"f1": float("nan")})["f1"]
            print(f"    {m:36s} {v['f1']:.3f}  delta vs old {d:+.3f}")
        dec = cal["model_sets"][name]["decoders_by_camera_class"][cls]["fit_report"]["out_of_fold"]
        print(f"    decoder event F1 {dec['f1']:.4f}  tp {dec['tp']} fp {dec['fp']} fn {dec['fn']}")
EOF
```
Expected: `clap` within 0.005 of old per class and no match down more than 0.02; `base` decoder event F1 >= 0.92 headcam, >= 0.97 handheld. If `clap` misses on a match, that is a finding: stop, write it up, do not tune thresholds to pass. Paste the table into the finding note and the PR body.

- [ ] **Step 3: Drop the xfails and run everything**

Remove the four strict xfail markers (Tasks 2, 4, 8, 9). Run:
```bash
uv run pytest -n 4 -q
uv run python scripts/ci/assert_ensemble_artifacts.py
uv run splitsmith fetch-models --list
```
Expected: suite green; the assertion script exits 0; `fetch-models --list` prints exactly two rows (`clap_audio_encoder`, `clap_text_embeddings`).

- [ ] **Step 4: The slim smoke without network**

Run:
```bash
uv build
uv venv ~/.claude-tmp/thin-detect/slim --python 3.11
~/.claude-tmp/thin-detect/slim/bin/pip install dist/splitsmith-*.whl
SPLITSMITH_CONFIG_DIR=$(mktemp -d ~/.claude-tmp/thin-detect/cfg.XXXX) SPLITSMITH_NO_MODEL_PREFETCH=1 \
  ~/.claude-tmp/thin-detect/slim/bin/python - <<'EOF'
import os, socket
socket.socket = None  # any download attempt now fails loudly
from splitsmith.beep.detect import load_audio
from splitsmith.ensemble.api import load_ensemble_runtime, detect_shots_ensemble
rt = load_ensemble_runtime(with_voter_e=False)
assert rt.model_set == "base" and rt.clap is None
audio, sr = load_audio("tests/fixtures/stage-shots-tallmilan-2026-stage3-s97dcec94-pin.wav")
r = detect_shots_ensemble(audio, sr, 0.0, 10.0, rt, camera_class="headcam")  # beep/stage time from the fixture JSON
print(sum(c.kept for c in r.candidates), "shots kept with", r.model_set)
EOF
```
Expected: prints a positive count and `base`; no exception. Read the beep time and stage time from the fixture's JSON rather than the placeholders above.

- [ ] **Step 5: Docs**

- `docs/cameras.md`: a dated 2026-10-26 entry under the DJI Osmo Action 4 (24 FP per 100 shots against 3 to 5 for the GO 3S and Vanguard; 8 fixtures, one shooter, Hostfinalen; median FP 0.7 s from the nearest true shot, so mostly not echoes) and under the GO 3S (misses 11 per 100, concentrated in Satra and HFO Masters; Satra's labels not yet checked on a spectrogram).
- `.claude/rules/shot-detection.md`: replace the voter B / voter C / decoder bullets with the two sets, the selection order (`SPLITSMITH_MODEL_SET`, then `GlobalPrefs.detection_model_set`, then `base`), the fallback rule (a `clap` request without artifacts runs `base` and the audit doc says so), PANN gone, one decoder per class per set, the parity references per set. Keep it ASCII.
- The finding note: the spike tables from the spec's "Why", the gate table from Step 2, the reviewed-only comparison, and the per-camera error table.

- [ ] **Step 6: Commit**

```bash
git rm src/splitsmith/data/voter_c_gbdt_headcam.onnx src/splitsmith/data/voter_c_gbdt_handheld.onnx tests/data/voter_c_parity_reference.npz
git add src/splitsmith/data/ensemble_calibration.json src/splitsmith/data/voter_c_gbdt_*.onnx tests/data/voter_c_parity_reference_*.npz
git add tests/test_calibration_model_sets.py tests/test_pann_is_gone.py tests/test_onnx_parity.py tests/test_models_layer.py
git add docs/ensemble_dashboard/findings/2026-10-26_thin_detection_model_sets.md docs/cameras.md .claude/rules/shot-detection.md
git commit -m "feat(model): ship base and clap model sets built on the whole corpus"
```
Check `git status`: no WAV, no `.cache`, no `uv.lock`.

---

### Task 11: Hosted runs clap; drop the dead dependency

**Files:**
- Modify: `Dockerfile:196`, `Dockerfile.gpu:169` (the `ENV SPLITSMITH_CONFIG_DIR` line gains `SPLITSMITH_MODEL_SET=clap`), `pyproject.toml` (remove `huggingface_hub`), `uv.lock`
- Test: `tests/test_pann_is_gone.py`

- [ ] **Step 1: Write the failing test**

```python
def test_hosted_images_pin_the_clap_set_and_the_wheel_has_no_hub_dependency() -> None:
    for name in ("Dockerfile", "Dockerfile.gpu"):
        assert "SPLITSMITH_MODEL_SET=clap" in (REPO / name).read_text(encoding="utf-8")
    import tomllib

    deps = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert not any(d.startswith("huggingface_hub") for d in deps)
```

- [ ] **Step 2: Run it to verify it fails, then implement**

Run: `uv run pytest tests/test_pann_is_gone.py -n0 -v -k hosted_images`
Expected: FAIL on the Dockerfile assertion. Edit the two `ENV` lines and `pyproject.toml`, then `uv lock` and confirm with `uv run python -c "import splitsmith.ensemble.api"` that nothing imports the removed package (`git grep -n huggingface_hub src` is empty).

- [ ] **Step 3: Run the suite and the slim import check**

Run: `uv run pytest -n 4 -q && uv run python scripts/ci/assert_slim_import_surface.py`
Expected: PASS, exit 0.

- [ ] **Step 4: Commit and open PR 1**

```bash
git add Dockerfile Dockerfile.gpu pyproject.toml uv.lock tests/test_pann_is_gone.py
git commit -m "chore: hosted detects with the clap set; drop the unused huggingface_hub dependency"
```
PR body: the gate table, the reviewed-only comparison, the per-camera error table, and the list of tests that fail without their change (CLAUDE.md's review practice: for each new test, delete the fix and watch it fail, once, before opening). Request the detection-pipeline review pass with these claims to verify: the gate figures reproduce from `build/ensemble_heldout/report.json`; no code path constructs a CLAP session under `base` (`git grep load_clap_runtime` has one caller, guarded); each decoder's `features` matches its set; `candidate_dict` writes no `gunshot_prob` and old docs still load.

---

### Task 12: The preference route and the startup prefetch (PR 2)

**Files:**
- Modify: `src/splitsmith/ui/server.py:2944-2975` (`_maybe_submit_model_download`), `:9649-9666` (add the route after the scrub one), `:10212-10261` (`/api/models/status`)
- Test: create `tests/ui/test_detection_model_settings.py`

**Interfaces:**
- Produces:
  - `class DetectionModelSettingsRequest(BaseModel)`: `model_set: Literal["base", "clap"]`.
  - `GET /api/settings/detection-model` -> `{"model_set": str, "required": list[str], "present": bool, "active_job": str | None}` (404 hosted).
  - `PUT /api/settings/detection-model` with the request body: saves the preference; when `model_set == "clap"` and any required artifact is not `present` and no `model_download` job is active, submits one; returns the GET shape.
  - `_maybe_submit_model_download` runs only when `resolve_model_set(prefs.detection_model_set) == "clap"` (hosted: `resolve_model_set(None)`), and only for the `clap` set's `requires_artifacts`.
  - `/api/models/status` adds `"model_set": <resolved>` and `"required": [...]`; `missing`/`mismatched` are filtered to the required slugs.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_reports_base_by_default(client) -> None:
    r = client.get("/api/settings/detection-model")
    assert r.status_code == 200
    assert r.json()["model_set"] == "base" and r.json()["required"] == []


def test_put_clap_saves_and_submits_one_download(client, monkeypatch) -> None:
    submitted = []
    monkeypatch.setattr(server_state.jobs, "submit", lambda **kw: submitted.append(kw["kind"]) or _fake_job("j1"))
    ...registry status monkeypatched to "missing" for both clap slugs...
    r = client.put("/api/settings/detection-model", json={"model_set": "clap"})
    assert r.json()["model_set"] == "clap" and r.json()["active_job"] == "j1"
    assert user_config.load_global_prefs().detection_model_set == "clap"
    assert submitted == ["model_download"]


def test_put_clap_does_not_submit_a_second_download(client, monkeypatch) -> None:
    ...find_active returns a live job...
    client.put("/api/settings/detection-model", json={"model_set": "clap"})
    assert submitted == []


def test_startup_prefetch_is_silent_under_base(monkeypatch) -> None:
    ...prefs base, registry says missing...
    asyncio.run(server._maybe_submit_model_download(state))
    assert submitted == []


def test_hosted_404s_the_route(hosted_client) -> None:
    assert hosted_client.get("/api/settings/detection-model").status_code == 404
```
Use the fixtures `tests/ui/test_stages_api.py` and `tests/test_scrub_rendition.py` use for a local client and a hosted client; point `SPLITSMITH_CONFIG_DIR` at `tmp_path` so prefs are isolated.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/ui/test_detection_model_settings.py -n0 -v`
Expected: 404 / FAIL on every test.

- [ ] **Step 3: Implement, then regenerate the wire types**

Run after the routes exist: `uv run python scripts/export_openapi.py && (cd src/splitsmith/ui_static && pnpm gen:api)`.

- [ ] **Step 4: Run the tests and the suite, commit**

Run: `uv run pytest tests/ui/test_detection_model_settings.py -n0 -q && uv run pytest -n 4 -q`
Expected: PASS.
```bash
git add src/splitsmith/ui/server.py tests/ui/test_detection_model_settings.py src/splitsmith/ui_static/openapi.json src/splitsmith/ui_static/src/lib/api/types.gen.ts
git commit -m "feat(ui): a detection model preference, with the download job behind it"
```

---

### Task 13: The Settings control and the model-set chip in the SPA

**Files:**
- Create: `src/splitsmith/ui_static/src/components/home/DetectionModelCard.tsx`, `DetectionModelCard.test.tsx`
- Modify: `src/splitsmith/ui_static/src/lib/api/misc.ts` (wrappers `getDetectionModel()` / `putDetectionModel(modelSet)` beside the scrub ones), `src/splitsmith/ui_static/src/pages/Home.tsx` (mount beside `SyncCard`, local mode only), `src/splitsmith/ui_static/src/lib/audit-doc.ts:50-75` (`_model_set`, `_calibration_built_at` on the pending block type), `src/splitsmith/ui_static/src/pages/Audit.tsx` (the chip)
- Test: `Audit.fixture.test.tsx` or a new `Audit.modelSet.test.tsx` (call `allowLazyPages()` as the route tests do; see memory)

**Interfaces:**
- Consumes: Task 12's routes and generated types; `_candidates_pending_audit._model_set` from Task 6.
- Produces: the card: title "Audio model for head cameras", a switch, copy "Downloads a 120 MB model once. Improves shot detection on head cameras a little." (ASCII, no dash), a status line while a download is active ("Downloading..." from `/api/models/status` polling the way the existing overlay does) and "Ready" when present. The Audit page shows a small chip near the detection status reading `Detected with base model` / `Detected with clap model` / nothing when the block has no `_model_set`. Read `.claude/rules/ui-budget.md` before placing either; use the existing primitives only.

- [ ] **Step 1: Write the failing vitest tests**

```tsx
it("turns the preference on and shows the download state", async () => {
  mockApi({ "GET /api/settings/detection-model": { model_set: "base", required: [], present: true, active_job: null },
            "PUT /api/settings/detection-model": { model_set: "clap", required: ["clap_audio_encoder", "clap_text_embeddings"], present: false, active_job: "j1" } });
  render(<DetectionModelCard />);
  await user.click(screen.getByRole("switch", { name: /audio model for head cameras/i }));
  expect(await screen.findByText(/downloading/i)).toBeInTheDocument();
  expect(putCalls).toEqual([{ model_set: "clap" }]);
});

it("shows which model set detected the stage, and nothing for old docs", async () => {
  ...render Audit with a doc whose pending block has _model_set: "base"...
  expect(await screen.findByText(/detected with base model/i)).toBeInTheDocument();
  ...re-render with a doc without _model_set...
  expect(screen.queryByText(/detected with/i)).toBeNull();
});
```
Use the file's existing mock helper names (read `Audit.fixture.test.tsx` for the harness).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm test -- --run DetectionModelCard Audit.modelSet`
Expected: FAIL (component missing; no chip).

- [ ] **Step 3: Implement, run, screenshot**

Run: `pnpm test -- --run && pnpm lint && pnpm typecheck` (whatever the SPA's CI job runs; see `ci.yml`'s spa job).
Expected: green. Then the demo-match harness from CLAUDE.md ("Verifying a screen locally") and a Playwright screenshot of Home with the card and of Audit with the chip; show both before calling the task done.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/components/home/DetectionModelCard.tsx src/splitsmith/ui_static/src/components/home/DetectionModelCard.test.tsx src/splitsmith/ui_static/src/lib/api/misc.ts src/splitsmith/ui_static/src/pages/Home.tsx src/splitsmith/ui_static/src/lib/audit-doc.ts src/splitsmith/ui_static/src/pages/Audit.tsx src/splitsmith/ui_static/src/pages/Audit.modelSet.test.tsx
git commit -m "feat(ui): opt into the head camera audio model from Home; Audit names the model set"
```

---

### Task 14: What's new, and PR 2

**Files:**
- Modify: `src/splitsmith/data/whats_new.json`
- Test: `tests/test_whats_new.py` (exists)

- [ ] **Step 1: Add the entry with the `whats-new` skill**

Invoke the `whats-new` skill; the entry (id `thin-detection`, date the PR date) says, in the house style, that shot detection no longer downloads models and that an optional 120 MB audio model for head cameras is on the Home page. Run `uv run pytest tests/test_whats_new.py -n0 -q`. Expected: PASS.

- [ ] **Step 2: Commit and open PR 2**

```bash
git add src/splitsmith/data/whats_new.json
git commit -m "docs: what's new for the thin detection install"
```
PR body: screenshots from Task 13, the test list, and a note that hosted is unaffected (its set is the image's `ENV`).
