"""Voter C columns are named, and a model set picks its columns by name."""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from splitsmith.ensemble import features as feat


def test_column_names_cover_the_full_matrix() -> None:
    assert len(feat.VOTER_C_COLUMN_NAMES) == feat.VOTER_C_FEATURE_DIM
    assert len(set(feat.VOTER_C_COLUMN_NAMES)) == feat.VOTER_C_FEATURE_DIM
    assert not any("gunshot" in n for n in feat.VOTER_C_COLUMN_NAMES)


def test_column_names_match_the_matrix_builder() -> None:
    n = 4
    hand = np.random.default_rng(0).random((n, feat.HAND_FEATURE_DIM)) + 0.1
    sims = np.zeros((n, len(feat.CLAP_PROMPTS)))
    sims[:, 2] = 7.0
    diff = np.full(n, 3.0)
    x = feat.voter_c_feature_matrix(hand, sims, diff, "handheld")
    assert x.shape == (n, feat.VOTER_C_FEATURE_DIM)
    names = feat.VOTER_C_COLUMN_NAMES
    assert np.all(x[:, names.index("clap_sim_02")] == 7.0)
    assert np.all(x[:, names.index("clap_diff")] == 3.0)
    assert np.all(x[:, names.index("cam_handheld")] == 1.0)
    assert np.all(x[:, names.index("confidence")] == hand[:, feat._HAND_FEATURE_NAMES.index("confidence")])


def test_base_has_no_clap_columns_and_clap_has_them_all() -> None:
    base = feat.model_set_columns("base")
    clap = feat.model_set_columns("clap")
    assert not any("clap" in n for n in base)
    assert clap == feat.VOTER_C_COLUMN_NAMES
    assert set(base) < set(clap)
    assert "confidence" in base and "cam_headcam" in base and "rel_peak_amp" in base


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
    assert "gunshot_prob" not in inspect.signature(feat.voter_c_feature_matrix).parameters
    assert "gunshot_prob" not in inspect.signature(feat.stage_relative_features).parameters
