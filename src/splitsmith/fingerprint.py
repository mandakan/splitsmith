"""A cheap content fingerprint for a video file (#1124, part 2).

The same clip can reach a match from two places -- a phone export and later
the file server, or one copy in two folders -- and the import, keyed on the
resolved source path, took it twice. Hashing a whole multi-GB camera file
over a NAS is too slow for a scan, so the fingerprint is the size plus a
sha256 of the first and last ``CHUNK`` bytes: camera files with the same
size, head (container header, first frames) and tail (moov / last frames)
are the same recording.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 1024 * 1024


def clip_fingerprint(path: Path) -> str | None:
    """``"<size hex>-<sha256 of head + tail>"``, or ``None`` for an empty
    file (a failed copy is not a recording; two of them are never the same
    clip). Raises ``OSError`` when the file cannot be read. A file shorter
    than two chunks is hashed whole."""
    size = path.stat().st_size
    if size == 0:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        if size <= 2 * CHUNK:
            digest.update(f.read())
        else:
            digest.update(f.read(CHUNK))
            f.seek(size - CHUNK)
            digest.update(f.read(CHUNK))
    return f"{size:x}-{digest.hexdigest()}"
