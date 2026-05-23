"""First-launch system-dependency checks (issue #377 -- doc 04).

The slim install ships without ffmpeg / ffprobe -- they're documented
system deps. Before the first detection, we verify they're invocable
and, if not, surface a copy-pasteable install hint per OS instead of
the cryptic ``FileNotFoundError`` the engine would raise mid-trim.

The positive result is cached for 24 hours in ``state.json`` under
``runtime().user_config_dir`` so the check pays its cost once per day
even when the user launches the UI many times.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .runtime import runtime as process_runtime

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"
CACHE_KEY = "ffmpeg_check"
CACHE_TTL_S = 24 * 60 * 60
_VERIFY_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class FfmpegOutcome:
    """Result of a single ffmpeg-presence probe.

    ``ok`` is the gate the caller branches on. ``hint`` is a
    multi-line, copy-pasteable install instruction the CLI / UI prints
    when ``ok`` is False; it's also returned on success (empty string)
    so the dataclass shape stays uniform for templating.
    """

    ok: bool
    binary: str
    hint: str


def _state_path() -> Path:
    return process_runtime().user_config_dir / STATE_FILENAME


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("ignoring unreadable state at %s", path, exc_info=True)
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _install_hint(binary: str) -> str:
    """OS-tailored install instructions for ffmpeg + ffprobe."""
    head = (
        f"Splitsmith couldn't find {binary!r} on this system.\n"
        "Install ffmpeg (which ships ffprobe alongside) with:"
    )
    if sys.platform == "darwin":
        return f"{head}\n\n    brew install ffmpeg\n\nThen re-run splitsmith."
    if sys.platform.startswith("win"):
        return (
            f"{head}\n\n"
            "    winget install --id=Gyan.FFmpeg -e\n\n"
            "Open a new shell after install so PATH refreshes, then re-run splitsmith."
        )
    # Linux + everything else
    return (
        f"{head}\n\n"
        "    Ubuntu / Debian: sudo apt install ffmpeg\n"
        "    Fedora:          sudo dnf install ffmpeg\n"
        "    Arch:            sudo pacman -S ffmpeg\n\n"
        "Then re-run splitsmith."
    )


def _binary_resolves(binary: str) -> bool:
    """``True`` iff ``binary`` is invocable in this environment.

    Absolute paths must exist with the executable bit set; bare names
    must resolve via ``shutil.which`` (PATH-aware). The slim runtime's
    bundled-binary discovery (#370) has already turned a bare name
    into an absolute path when one was found beside the executable, so
    this check is straightforward.
    """
    candidate = Path(binary)
    if candidate.is_absolute():
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(binary) is not None


def _binary_runs(binary: str) -> bool:
    """``True`` iff ``binary -version`` exits 0. Bounded by timeout."""
    try:
        result = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            timeout=_VERIFY_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def check_ffmpeg(*, use_cache: bool = True) -> FfmpegOutcome:
    """Verify ffmpeg + ffprobe are reachable; return a typed outcome.

    Honors a 24-hour positive-result cache so repeat launches don't
    re-spawn subprocesses. Negative results are never cached -- the
    user is told to install the dep + re-run; on the next launch the
    check is fresh.

    ``use_cache=False`` forces the full probe even when a positive
    result exists; useful for ``splitsmith ui --no-system-check`` debug
    flows and the test suite.
    """
    runtime = process_runtime()
    binary = runtime.ffmpeg_binary
    ffprobe = runtime.ffprobe_binary

    state = _load_state()
    cached = state.get(CACHE_KEY) if use_cache else None
    if (
        isinstance(cached, dict)
        and cached.get("ok") is True
        and cached.get("binary") == binary
        and cached.get("ffprobe") == ffprobe
        and isinstance(cached.get("ts"), (int, float))
        and (time.time() - float(cached["ts"])) < CACHE_TTL_S
    ):
        return FfmpegOutcome(ok=True, binary=binary, hint="")

    if not _binary_resolves(binary) or not _binary_resolves(ffprobe):
        return FfmpegOutcome(ok=False, binary=binary, hint=_install_hint("ffmpeg"))
    if not _binary_runs(binary) or not _binary_runs(ffprobe):
        return FfmpegOutcome(ok=False, binary=binary, hint=_install_hint("ffmpeg"))

    state[CACHE_KEY] = {"ok": True, "binary": binary, "ffprobe": ffprobe, "ts": time.time()}
    try:
        _save_state(state)
    except OSError:
        logger.warning("could not persist ffmpeg check cache", exc_info=True)
    return FfmpegOutcome(ok=True, binary=binary, hint="")
