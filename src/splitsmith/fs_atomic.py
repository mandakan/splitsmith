"""The rename that publishes a file written beside its destination.

Every whole-or-nothing writer here writes a temp file next to the target
and renames it into place. On POSIX that rename always replaces. On
Windows ``os.replace`` fails with ``PermissionError`` while another handle
has the target open: a reader, a second writer finishing its own rename,
or the virus scanner, usually for a few milliseconds. ``replace`` retries
that there for about a second before it gives up; on POSIX a
``PermissionError`` is a real one and is raised at once.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ATTEMPTS = 20
_PAUSE_S = 0.05


def replace(src: Path | str, dst: Path | str) -> None:
    """``Path(src).replace(dst)`` (``os.replace``), riding out Windows' brief sharing violations."""
    if sys.platform != "win32":
        Path(src).replace(dst)
        return
    for attempt in range(_ATTEMPTS):
        try:
            Path(src).replace(dst)
            return
        except PermissionError:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(_PAUSE_S)
