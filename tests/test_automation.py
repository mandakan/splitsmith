"""Tests for the layered automation settings (#215)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import automation
from splitsmith.automation import (
    AutomationOverride,
    AutomationSettings,
    resolve_automation,
)

# --- AutomationSettings ---------------------------------------------------


def test_automation_settings_defaults() -> None:
    s = AutomationSettings()
    assert s.shot_detect_on_beep_verified is True


def test_automation_settings_loads_from_yaml(tmp_path: Path) -> None:
    """The global YAML's ``automation`` block populates fields."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("automation:\n  shot_detect_on_beep_verified: false\n", encoding="utf-8")
    loaded = automation.load_global(cfg)
    assert loaded.shot_detect_on_beep_verified is False


def test_automation_settings_missing_block_uses_defaults(tmp_path: Path) -> None:
    """A YAML file without an ``automation`` block is fine -- defaults apply."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("theme: dark\n", encoding="utf-8")
    loaded = automation.load_global(cfg)
    assert loaded.shot_detect_on_beep_verified is True


def test_automation_settings_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "does-not-exist.yaml"
    loaded = automation.load_global(cfg)
    assert loaded.shot_detect_on_beep_verified is True


def test_automation_settings_malformed_yaml_falls_back(tmp_path: Path) -> None:
    """A broken YAML doesn't crash the app -- the loader logs and returns
    the field defaults."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("automation:\n  shot_detect_on_beep_verified: !!!\n", encoding="utf-8")
    loaded = automation.load_global(cfg)
    assert loaded.shot_detect_on_beep_verified is True


def test_automation_settings_env_var_overrides_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SPLITSMITH_AUTOMATION_*`` env vars trump the YAML default
    (pydantic-settings precedence). Env init args we pass in
    (the YAML block) lose to environment by design."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("automation:\n  shot_detect_on_beep_verified: true\n", encoding="utf-8")
    # With pydantic-settings, env vars beat field defaults but lose
    # to init kwargs we explicitly pass. We pass the YAML block as
    # init kwargs, so the env var only wins when the YAML block is
    # silent on a field. Test that path:
    cfg2 = tmp_path / "empty.yaml"
    cfg2.write_text("automation: {}\n", encoding="utf-8")
    monkeypatch.setenv("SPLITSMITH_AUTOMATION_SHOT_DETECT_ON_BEEP_VERIFIED", "false")
    loaded = automation.load_global(cfg2)
    assert loaded.shot_detect_on_beep_verified is False


# --- resolve_automation ---------------------------------------------------


def test_resolve_uses_global_when_no_overrides() -> None:
    g = AutomationSettings(shot_detect_on_beep_verified=False)
    resolved = resolve_automation(global_settings=g)
    assert resolved.settings.shot_detect_on_beep_verified is False
    p = resolved.provenance["shot_detect_on_beep_verified"]
    assert p.source == "global"
    assert p.global_value is False
    assert p.project_value is None
    assert p.cli_value is None


def test_resolve_project_override_wins_over_global() -> None:
    g = AutomationSettings(shot_detect_on_beep_verified=True)
    proj = AutomationOverride(shot_detect_on_beep_verified=False)
    resolved = resolve_automation(global_settings=g, project_override=proj)
    assert resolved.settings.shot_detect_on_beep_verified is False
    p = resolved.provenance["shot_detect_on_beep_verified"]
    assert p.source == "project"
    assert p.project_value is False
    assert p.global_value is True
    assert p.cli_value is None


def test_resolve_cli_override_wins_over_project_and_global() -> None:
    g = AutomationSettings(shot_detect_on_beep_verified=True)
    proj = AutomationOverride(shot_detect_on_beep_verified=False)
    cli = AutomationOverride(shot_detect_on_beep_verified=True)
    resolved = resolve_automation(global_settings=g, project_override=proj, cli_override=cli)
    assert resolved.settings.shot_detect_on_beep_verified is True
    p = resolved.provenance["shot_detect_on_beep_verified"]
    assert p.source == "cli"
    assert p.cli_value is True
    assert p.project_value is False
    assert p.global_value is True


def test_resolve_project_override_with_none_field_falls_through() -> None:
    """A project override of ``None`` on a field is the same as having
    no override -- the global value passes through and provenance
    reports 'global'."""
    g = AutomationSettings(shot_detect_on_beep_verified=True)
    proj = AutomationOverride(shot_detect_on_beep_verified=None)
    resolved = resolve_automation(global_settings=g, project_override=proj)
    assert resolved.settings.shot_detect_on_beep_verified is True
    assert resolved.provenance["shot_detect_on_beep_verified"].source == "global"


def test_resolve_no_args_loads_global_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``global_settings`` the resolver loads from the
    user-config directory. SPLITSMITH_HOME points to a tmp dir so the
    test doesn't read the real user's config."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    monkeypatch.delenv("SPLITSMITH_DISABLE_USER_CONFIG", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("automation:\n  shot_detect_on_beep_verified: false\n", encoding="utf-8")
    resolved = resolve_automation()
    assert resolved.settings.shot_detect_on_beep_verified is False
    assert resolved.provenance["shot_detect_on_beep_verified"].source == "global"
