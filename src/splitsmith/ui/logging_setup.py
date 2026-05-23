"""File logging setup for the embedded sidecar (issue #368).

The CLI keeps its existing stdout-only behavior; this module is used
exclusively by :mod:`splitsmith.ui.embedded` so a desktop shell with no
terminal can read support diagnostics from a known on-disk location.

Default log path is ``runtime().user_config_dir / "logs" /
"splitsmith-server.log"`` so it follows ``SPLITSMITH_CONFIG_DIR``
overrides. Override the dir explicitly via ``SPLITSMITH_LOG_DIR`` or
the ``--log-dir`` CLI flag; override the level via
``SPLITSMITH_LOG_LEVEL`` or ``--log-level``.

Rotation: 5 MiB per file, 5 backups -- enough to keep a few weeks of
typical use without growing unbounded.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from ..runtime import runtime as process_runtime

ENV_LOG_DIR = "SPLITSMITH_LOG_DIR"
ENV_LOG_LEVEL = "SPLITSMITH_LOG_LEVEL"

LOG_FILE_NAME = "splitsmith-server.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def resolve_log_dir(explicit: Path | str | None) -> Path:
    """Pick the log directory using flag > env > runtime default."""
    if explicit is not None:
        return Path(explicit)
    env_val = os.environ.get(ENV_LOG_DIR)
    if env_val:
        return Path(env_val)
    return process_runtime().user_config_dir / "logs"


def resolve_log_level(explicit: str | None) -> int:
    """Pick the log level using flag > env > INFO."""
    raw = explicit or os.environ.get(ENV_LOG_LEVEL) or "INFO"
    level = logging.getLevelName(raw.upper())
    if not isinstance(level, int):
        return logging.INFO
    return level


def configure_file_logging(
    *,
    log_dir: Path | str | None = None,
    level: str | None = None,
) -> Path:
    """Attach a RotatingFileHandler to the root + uvicorn loggers.

    Idempotent: a second call replaces the previously attached splitsmith
    handler instead of doubling up. Returns the resolved log file path
    so callers can advertise it (e.g. in the ready banner).
    """
    resolved_dir = resolve_log_dir(log_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    log_file = resolved_dir / LOG_FILE_NAME
    resolved_level = resolve_log_level(level)

    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.setLevel(resolved_level)
    handler.set_name("splitsmith-file")

    root = logging.getLogger()
    _replace_named_handler(root, handler)
    if root.level == logging.NOTSET or root.level > resolved_level:
        root.setLevel(resolved_level)

    for name in _UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        _replace_named_handler(uv_logger, handler)
        uv_logger.setLevel(resolved_level)
        # Let uvicorn keep its default stderr handler too; the file
        # mirror is additive. propagate=False stays uvicorn's default.

    return log_file


def _replace_named_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    """Detach any handler with the same name, then attach ``handler``."""
    for existing in list(logger.handlers):
        if existing.get_name() == handler.get_name():
            logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                pass
    logger.addHandler(handler)
