"""Account features: the tier registry and its resolution (spec 2026-10-03)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith.access import ALL_FEATURES, AccessConfig, Feature, access_config, features_for
from splitsmith.config import Config

ADMINS = frozenset({"boss@example.com"})


def test_default_tiers() -> None:
    cfg = AccessConfig()
    assert features_for("full", "a@x.se", cfg, ADMINS) == ALL_FEATURES
    assert features_for("sharing", "a@x.se", cfg, ADMINS) == {Feature.sync, Feature.share}
    assert features_for("disabled", "a@x.se", cfg, ADMINS) == frozenset()
    assert cfg.default_tier == "full"


def test_env_admin_gets_everything_whatever_the_tier() -> None:
    cfg = AccessConfig()
    assert features_for("disabled", "Boss@Example.com", cfg, ADMINS) == ALL_FEATURES


def test_unknown_tier_fails_closed_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert features_for("platinum", "a@x.se", AccessConfig(), ADMINS) == frozenset()
    assert "platinum" in caplog.text


def test_none_tier_fails_closed() -> None:
    assert features_for(None, "a@x.se", AccessConfig(), ADMINS) == frozenset()


def test_yaml_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "access:\n  default_tier: sharing\n  tiers:\n"
        "    full: [sync, share, create_match, raw_upload, hosted_compute]\n"
        "    sharing: [sync, share]\n    club: [sync, share, hosted_compute]\n"
    )
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(path))
    cfg = access_config()
    assert cfg.default_tier == "sharing"
    assert features_for("club", "a@x.se", cfg, ADMINS) == {
        Feature.sync,
        Feature.share,
        Feature.hosted_compute,
    }


def test_bad_feature_name_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AccessConfig.model_validate({"tiers": {"full": ["sync", "teleport"]}, "default_tier": "full"})


def test_default_tier_must_exist() -> None:
    with pytest.raises(ValidationError):
        AccessConfig.model_validate({"tiers": {"full": ["sync"]}, "default_tier": "sharing"})


def test_config_carries_access() -> None:
    assert Config().access == AccessConfig()


def test_env_default_tier_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_CONFIG", raising=False)
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "sharing")
    cfg = access_config()
    assert cfg.default_tier == "sharing"
    assert cfg.tiers == AccessConfig().tiers


def test_env_default_tier_unknown_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_CONFIG", raising=False)
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "platinum")
    with pytest.raises(ValueError, match="platinum"):
        access_config()


def test_env_default_tier_applies_on_top_of_yaml_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "access:\n  default_tier: full\n  tiers:\n"
        "    full: [sync, share, create_match, raw_upload, hosted_compute]\n"
        "    club: [sync, share, hosted_compute]\n"
    )
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(path))
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "club")
    cfg = access_config()
    assert cfg.default_tier == "club"
    assert set(cfg.tiers) == {"full", "club"}
    # A tier the YAML replaced away is unknown, so naming it fails.
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "sharing")
    with pytest.raises(ValueError, match="sharing"):
        access_config()


def test_blank_env_default_tier_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_CONFIG", raising=False)
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "  ")
    assert access_config().default_tier == "full"
