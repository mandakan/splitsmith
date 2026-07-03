"""Unit tests for the worker launcher seam - no network, httpx.MockTransport only."""

from __future__ import annotations

import pytest

from splitsmith.worker_trigger import (
    ENV_RAILWAY_ENVIRONMENT_ID,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_SERVICE_ID,
    load_railway_config,
)

_ALL_ENV_VARS = (
    ENV_WORKER_LAUNCHER,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_SERVICE_ID,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_RAILWAY_ENVIRONMENT_ID,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_load_railway_config_disabled_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert load_railway_config() is None


def test_load_railway_config_disabled_when_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token without a service id (or vice versa) must not half-enable the launcher."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    assert load_railway_config() is None


def test_load_railway_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env-id")
    config = load_railway_config()
    assert config is not None
    assert (config.token, config.service_id, config.environment_id) == ("tok", "svc-id", "env-id")


def test_load_railway_config_falls_back_to_railway_env_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Railway injects RAILWAY_ENVIRONMENT_ID into every container; the
    SPLITSMITH_ override exists only for running outside Railway."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_RAILWAY_ENVIRONMENT_ID, "railway-env-id")
    config = load_railway_config()
    assert config is not None
    assert config.environment_id == "railway-env-id"
