"""Process-wide runtime config for artifacts and binaries (issue #130).

One resolver decides where ensemble artifacts live and which
``ffmpeg`` / ``ffprobe`` to invoke. Read everywhere via :func:`runtime`
so no module hardcodes ``"ffmpeg"`` at the orchestrator layer again.

The motivating use case is the (closed-source) desktop shell from
issue #129: a Tauri app bundles its own ffmpeg and ensemble artifacts
and points the embedded engine at them via env vars, without forking.
The same hooks let OSS users A/B-test custom model artifacts:

    SPLITSMITH_ARTIFACTS_DIR=/path/to/experimental splitsmith ui

Resolution priority for every field:

1. Explicit kwargs to :func:`resolve_runtime`.
2. Environment variables (see ``ENV_*`` constants below).
3. Built-in defaults: package data dir for artifacts, ``shutil.which``
   for binaries, platform-appropriate cache/config dirs.

The first call has a side effect: it ``setdefault``-s ``HF_HOME`` and
``TORCH_HOME`` into the resolved cache dir so CLAP / PANN downloads
land somewhere a packaged app can clean up, instead of the user's
home dir.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_ARTIFACTS_DIR = "SPLITSMITH_ARTIFACTS_DIR"
ENV_FFMPEG = "SPLITSMITH_FFMPEG"
ENV_FFPROBE = "SPLITSMITH_FFPROBE"
ENV_CACHE_DIR = "SPLITSMITH_CACHE_DIR"
ENV_CONFIG_DIR = "SPLITSMITH_CONFIG_DIR"

_PACKAGE_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Runtime:
    """Resolved locations of all process-wide engine resources.

    Read fields directly, or call :meth:`artifact` for files inside
    :attr:`artifacts_dir` (raises ``FileNotFoundError`` with an
    actionable message when the artifact is missing -- typically means
    a stale ``SPLITSMITH_ARTIFACTS_DIR`` override).
    """

    artifacts_dir: Path
    ffmpeg_binary: str
    ffprobe_binary: str
    cache_dir: Path
    user_config_dir: Path

    def artifact(self, name: str) -> Path:
        path = self.artifacts_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"splitsmith artifact {name!r} not found under "
                f"{self.artifacts_dir} -- check ${ENV_ARTIFACTS_DIR} or "
                "rebuild via scripts/build_ensemble_artifacts.py"
            )
        return path


def _platform_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "splitsmith"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "splitsmith"
        return Path.home() / "AppData" / "Local" / "splitsmith"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "splitsmith"
    return Path.home() / ".cache" / "splitsmith"


def _platform_user_config_dir() -> Path:
    # Mirror the convention from ``user_config.py`` so both modules
    # agree on where per-user state lives.
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg) / "splitsmith"
        return Path.home() / ".config" / "splitsmith"
    return Path.home() / ".splitsmith"


def _resolve_binary(explicit: str | None, env_name: str, default: str) -> str:
    if explicit:
        return explicit
    env_val = os.environ.get(env_name)
    if env_val:
        return env_val
    return default


def _validate_binary(value: str, label: str) -> None:
    """Log (do not raise) when ``value`` doesn't look invocable."""
    # Absolute path: must exist on disk.
    candidate = Path(value)
    if candidate.is_absolute():
        if not candidate.exists():
            logger.warning(
                "%s binary %s does not exist on disk; subprocess calls will fail",
                label,
                value,
            )
        return
    if shutil.which(value) is None:
        logger.warning(
            "%s binary %r not found on PATH; subprocess calls will fail",
            label,
            value,
        )


@lru_cache(maxsize=1)
def resolve_runtime(
    *,
    artifacts_dir: Path | str | None = None,
    ffmpeg_binary: str | None = None,
    ffprobe_binary: str | None = None,
    cache_dir: Path | str | None = None,
    user_config_dir: Path | str | None = None,
) -> Runtime:
    """Build and cache the process-wide :class:`Runtime`.

    Each parameter follows the same priority: explicit kwarg > env var
    > built-in default. Pass kwargs to pin an override programmatically
    (tests, embedded host); leave them ``None`` for normal use.

    The result is cached for the lifetime of the process. Tests can
    reset the cache via :func:`_clear_runtime_cache`.
    """
    art_env = os.environ.get(ENV_ARTIFACTS_DIR)
    art = (
        Path(artifacts_dir) if artifacts_dir is not None else Path(art_env) if art_env else _PACKAGE_DATA_DIR
    )

    cache_env = os.environ.get(ENV_CACHE_DIR)
    cache = (
        Path(cache_dir) if cache_dir is not None else Path(cache_env) if cache_env else _platform_cache_dir()
    )

    cfg_env = os.environ.get(ENV_CONFIG_DIR)
    cfg = (
        Path(user_config_dir)
        if user_config_dir is not None
        else Path(cfg_env) if cfg_env else _platform_user_config_dir()
    )

    ffm = _resolve_binary(ffmpeg_binary, ENV_FFMPEG, "ffmpeg")
    ffp = _resolve_binary(ffprobe_binary, ENV_FFPROBE, "ffprobe")
    _validate_binary(ffm, "ffmpeg")
    _validate_binary(ffp, "ffprobe")

    # Point HuggingFace / Torch caches into our cleanable cache dir so
    # CLAP/PANN downloads don't pollute ``~``. ``setdefault`` so users
    # who already point these elsewhere stay in control.
    os.environ.setdefault("HF_HOME", str(cache / "hf"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))

    return Runtime(
        artifacts_dir=art,
        ffmpeg_binary=ffm,
        ffprobe_binary=ffp,
        cache_dir=cache,
        user_config_dir=cfg,
    )


def runtime() -> Runtime:
    """Return the cached process-wide :class:`Runtime` (resolving on first call)."""
    return resolve_runtime()


def _clear_runtime_cache() -> None:
    """Reset the ``lru_cache`` on :func:`resolve_runtime`. Test-only helper."""
    resolve_runtime.cache_clear()
