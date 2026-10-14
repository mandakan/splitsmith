"""Shared derived files are published whole or not at all.

A derived file (an extracted WAV, a trim or rendition pulled from storage, a
cached JSON) is read by other requests, jobs and processes while it may be
being produced. Writing it in place lets a reader take a half-written file for
the real thing: on 2026-10-13 a phone opening a stage fired three requests at
once, one streamed the 720p rendition from storage into its final path, another
ran ffmpeg on the half file, exited 0 with a short WAV, and that WAV was served
(stage 8 came back as 10.475 s of 28.223 s).

:func:`produce` is the one way to write such a file: it writes a partial no
other process can share, checks it, and renames it into place. Readers need
nothing: the final path only ever holds a complete file. Who produces what,
and when, is the job queue's business (spec 2026-10-14: requests read,
jobs produce); two producers racing only waste work, so the lock is opt-in,
for writers that two local processes (the desktop app and the CLI) can run
at once on one file.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import uuid
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DerivedFileError(RuntimeError):
    """A produced file failed its check and was not published."""


def unique_partial(final: Path) -> Path:
    """A partial name no other process or thread can share (the 2026-09-27
    corruption came from every process writing ``<stem>.partial.mp4``). Keeps
    ``final``'s suffix so ffmpeg infers the format, and contains ``.partial``
    so every glob that skips or sweeps partials still does."""
    return final.with_name(f"{final.stem}.{os.getpid()}-{uuid.uuid4().hex[:8]}.partial{final.suffix}")


def lock_path(final: Path, purpose: str = "") -> Path:
    """The lock beside ``final``. ``<stem>.lock`` matches the rendition writer's
    lock (``trim.write_web_rendition``), so a pull and a cut of one rendition
    exclude each other. ``purpose`` names a second lock on the same file for
    a longer job that itself produces ``final`` (``flock`` on a second open of
    one lock file blocks even in the holding thread)."""
    return final.with_name(f"{final.stem}{'.' + purpose if purpose else ''}.lock")


@contextmanager
def locked(final: Path, purpose: str = "") -> Iterator[None]:
    """Hold the exclusive lock for ``final``. ``flock`` on a fresh open, so
    threads of one process exclude each other as well as processes."""
    final.parent.mkdir(parents=True, exist_ok=True)
    with lock_path(final, purpose).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def produce(
    final: Path,
    *,
    fresh: Callable[[], bool],
    make: Callable[[Path], None],
    check: Callable[[Path], str | None] | None = None,
    lock: bool = False,
) -> bool:
    """Make ``final`` unless ``fresh()``; True when this call published it.

    ``make(partial)`` writes the whole file to ``partial``; ``check(partial)``
    returns why it is unusable, or None. A failed make or check removes the
    partial and leaves any previous ``final`` in place; a failed check raises
    :class:`DerivedFileError`, a failed make re-raises its own error. With
    ``lock``, a second producer waits and then finds ``final`` fresh.
    """
    if fresh():
        return False
    if not lock:
        _publish(final, make, check)
        return True
    with locked(final):
        if fresh():
            return False
        _publish(final, make, check)
    return True


def _publish(final: Path, make: Callable[[Path], None], check: Callable[[Path], str | None] | None) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    partial = unique_partial(final)
    try:
        make(partial)
        problem = check(partial) if check is not None else None
        if problem is not None:
            raise DerivedFileError(f"{final.name}: {problem}")
        partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)


def download(
    storage: Any,
    key: str,
    final: Path,
    *,
    fresh: Callable[[], bool] = lambda: False,
    check: Callable[[Path], str | None] | None = None,
) -> bool:
    """Pull ``key`` from ``storage`` into ``final`` whole. True when ``final``
    is there afterwards (pulled, or found ``fresh`` once the lock was held,
    so concurrent pulls of one file download it once). False when the key is
    absent or the pull or check fails (logged, nothing published):
    best-effort like every storage cache read."""
    try:
        if not storage.exists(key):
            return False
    except Exception as exc:  # noqa: BLE001 -- a storage hiccup is a miss
        logger.info("derived: storage.exists(%s) raised %s", key, exc)
        return False

    def make(partial: Path) -> None:
        with storage.open_stream(key) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst)

    try:
        produce(final, fresh=fresh, make=make, check=check)
    except Exception as exc:  # noqa: BLE001
        logger.info("derived: pull of %s failed: %s", key, exc)
        return False
    return True


def wav_problem(path: Path, *, expect_seconds: float | None = None, tolerance_s: float = 0.25) -> str | None:
    """Why ``path`` is not a usable WAV, or None. With ``expect_seconds``, a
    WAV more than ``tolerance_s`` shorter than that is refused: ffmpeg exits 0
    on a truncated input and writes a short file."""
    try:
        with wave.open(str(path)) as w:
            frames, rate = w.getnframes(), w.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        return f"unreadable WAV ({exc})"
    if frames == 0 or rate <= 0:
        return "no audio"
    seconds = frames / rate
    if expect_seconds is not None and seconds < expect_seconds - tolerance_s:
        return f"short: {seconds:.3f} s of {expect_seconds:.3f} s"
    return None
