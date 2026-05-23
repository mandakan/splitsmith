"""Tests for the embedded sidecar's rotating file logger (issue #368)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from splitsmith.ui import logging_setup


@pytest.fixture(autouse=True)
def _reset_handlers() -> None:
    """Strip the splitsmith file handler between tests to avoid cross-talk."""
    yield
    for name in ("", *logging_setup._UVICORN_LOGGERS):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if handler.get_name() == "splitsmith-file":
                logger.removeHandler(handler)
                handler.close()


def test_creates_dir_and_writes_log_file(tmp_path: Path) -> None:
    log_file = logging_setup.configure_file_logging(log_dir=tmp_path / "logs")
    logging.getLogger("splitsmith.test").info("hello world")

    assert log_file == tmp_path / "logs" / logging_setup.LOG_FILE_NAME
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert "hello world" in contents
    assert "splitsmith.test" in contents


def test_log_dir_priority_explicit_over_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(logging_setup.ENV_LOG_DIR, str(tmp_path / "from-env"))
    resolved = logging_setup.resolve_log_dir(tmp_path / "explicit")
    assert resolved == tmp_path / "explicit"


def test_log_dir_priority_env_over_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(logging_setup.ENV_LOG_DIR, str(tmp_path / "from-env"))
    assert logging_setup.resolve_log_dir(None) == tmp_path / "from-env"


def test_log_dir_default_follows_runtime_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from splitsmith import runtime as runtime_module

    monkeypatch.delenv(logging_setup.ENV_LOG_DIR, raising=False)
    monkeypatch.setenv("SPLITSMITH_CONFIG_DIR", str(tmp_path / "cfg"))
    runtime_module._clear_runtime_cache()
    try:
        assert logging_setup.resolve_log_dir(None) == tmp_path / "cfg" / "logs"
    finally:
        runtime_module._clear_runtime_cache()


def test_log_level_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(logging_setup.ENV_LOG_LEVEL, "WARNING")
    assert logging_setup.resolve_log_level("debug") == logging.DEBUG
    assert logging_setup.resolve_log_level(None) == logging.WARNING


def test_log_level_falls_back_to_info_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(logging_setup.ENV_LOG_LEVEL, raising=False)
    assert logging_setup.resolve_log_level("nonsense-level") == logging.INFO


def test_idempotent_does_not_double_attach(tmp_path: Path) -> None:
    logging_setup.configure_file_logging(log_dir=tmp_path / "logs")
    logging_setup.configure_file_logging(log_dir=tmp_path / "logs")
    matching = [h for h in logging.getLogger().handlers if h.get_name() == "splitsmith-file"]
    assert len(matching) == 1


def test_rotation_triggers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop MAX_BYTES tiny enough that two writes force a rollover."""
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 256)
    log_file = logging_setup.configure_file_logging(log_dir=tmp_path / "logs")

    test_logger = logging.getLogger("splitsmith.rotation")
    payload = "x" * 200
    for _ in range(8):
        test_logger.info(payload)

    rotated = list((tmp_path / "logs").glob(f"{logging_setup.LOG_FILE_NAME}.*"))
    assert log_file.exists()
    assert len(rotated) >= 1, "expected at least one rotated backup file"


def test_uvicorn_loggers_get_file_handler(tmp_path: Path) -> None:
    logging_setup.configure_file_logging(log_dir=tmp_path / "logs")
    for name in logging_setup._UVICORN_LOGGERS:
        handlers = [h for h in logging.getLogger(name).handlers if h.get_name() == "splitsmith-file"]
        assert handlers, f"{name} missing splitsmith file handler"


def test_cli_keeps_stdout_only_by_default() -> None:
    """Sanity: importing the module must not attach a file handler on its own."""
    root = logging.getLogger()
    assert not any(h.get_name() == "splitsmith-file" for h in root.handlers)
