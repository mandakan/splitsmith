"""How far a running ffmpeg encode has got, read from its ``-progress`` file.

The export progress panel (spec 2026-10-25) shows each part moving while
it works; a grid stage used to sit still for minutes. The renderers run
ffmpeg through an injectable ``subprocess.run``-shaped runner, which
returns only when ffmpeg exits, so progress cannot come from stderr:
ffmpeg writes ``key=value`` blocks to a file instead, and a thread reads
the last ``out_time_us`` from it while the encode runs.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

#: How often the reader looks at the progress file.
PROGRESS_INTERVAL_S = 2.0
#: How often ffmpeg writes a progress block.
STATS_PERIOD_S = 1.0


def _last_out_time_s(path: Path) -> float | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        key, _, value = line.partition("=")
        if key == "out_time_us":
            try:
                return int(value) / 1e6
            except ValueError:
                return None
    return None


@contextlib.contextmanager
def ffmpeg_progress(
    duration_s: float,
    on_fraction: Callable[[float], None],
    *,
    interval_s: float | None = None,
) -> Iterator[tuple[str, ...]]:
    """Yield the argv that makes ffmpeg report its progress, and call
    ``on_fraction`` with ``out_time / duration_s`` (capped at 1.0) while
    the block runs. The argv is global options: put it right after the
    binary. A clean exit reports 1.0; an error does not."""
    interval = PROGRESS_INTERVAL_S if interval_s is None else interval_s
    with tempfile.TemporaryDirectory(prefix="ffprogress-") as tmp:
        path = Path(tmp) / "progress.txt"
        path.touch()
        stop = threading.Event()
        last: list[float] = []

        def report() -> None:
            seconds = _last_out_time_s(path)
            if seconds is None or duration_s <= 0:
                return
            fraction = max(0.0, min(1.0, seconds / duration_s))
            if not last or fraction != last[-1]:
                last.append(fraction)
                on_fraction(fraction)

        def watch() -> None:
            while not stop.wait(interval):
                report()

        reader = threading.Thread(target=watch, name="ffmpeg-progress", daemon=True)
        reader.start()
        try:
            yield ("-progress", str(path), "-stats_period", str(STATS_PERIOD_S), "-nostats")
        finally:
            stop.set()
            reader.join()
        on_fraction(1.0)
