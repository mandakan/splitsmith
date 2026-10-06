"""Content-addressed cache for the MP4 renderer's encoded segments.

A rendered match is a stitch (a stream copy) of per-stage and per-card
segments, each an ffmpeg encode that is by far the slow part: a 4K,
50 fps stage at the YouTube preset runs at a few frames a second. The
same export run twice used to encode every segment twice. With a cache
the second run encodes nothing that did not change and goes straight to
the stitch.

A segment is keyed on the ffmpeg command that makes it, with its output
path factored out and every input file replaced by a fingerprint:

- a file inside the render's ``work_dir`` (the card PNGs, drawn fresh
  each render) by the SHA-256 of its bytes, since its path changes
  every run;
- any other absolute path (trims, overlays, intro clips) by its resolved
  path, size and modification time, which a re-cut or re-rendered
  overlay always moves; hashing gigabytes of footage per render would
  cost more than it saves;
- the ffmpeg binary by the same identity, so an ffmpeg upgrade misses.

Anything else that changes the picture (pads, trims, cards' seconds,
encode params, the lower-third PNG) is in the argv already. A key that
misses is never wrong, only slow; a key that hits must be exactly the
encode that command would make, which is why nothing is left out.

Pure except for the filesystem under ``root``. Entries are touched on a
hit and evicted oldest first past ``max_bytes``; the entries the current
render used are never evicted by it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Bump when the key's recipe changes, so no entry keyed the old way hits.
KEY_VERSION = 3
_SUFFIX = ".mp4"
_STALE_PARTIAL_SECONDS = 24 * 3600


def _file_identity(path: Path) -> str:
    st = path.stat()
    return f"file:{path.resolve()}:{st.st_size}:{st.st_mtime_ns}"


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _binary_identity(binary: str) -> str:
    found = shutil.which(binary)
    if found is None:
        return f"bin:{binary}"
    return "bin:" + _file_identity(Path(found))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class SegmentCache:
    """Encoded segments under ``root``, at most ``max_bytes`` of them."""

    root: Path
    max_bytes: int

    def key(
        self,
        argv: tuple[str, ...],
        *,
        output_path: Path,
        work_dir: Path,
        virtual_inputs: Mapping[str, str] | None = None,
    ) -> str:
        """The content address of the segment ``argv`` writes to ``output_path``.

        ``virtual_inputs`` maps an argv token (a file the encode will
        create first, such as a motion clip) to the digest of what creates
        it, so the key exists before the file does and a cached segment is
        found without making the file."""
        output = str(output_path)
        parts: list[str] = [f"v{KEY_VERSION}", _binary_identity(argv[0])]
        for token in argv[1:]:
            if token == output:
                parts.append("<output>")
                continue
            if virtual_inputs and token in virtual_inputs:
                parts.append(f"virtual:{virtual_inputs[token]}")
                continue
            candidate = Path(token)
            if candidate.is_absolute() and candidate.is_file():
                if _is_within(candidate, work_dir):
                    parts.append(_content_hash(candidate))
                else:
                    parts.append(_file_identity(candidate))
                continue
            parts.append(token)
        return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}{_SUFFIX}"

    def lookup(self, key: str) -> Path | None:
        """The cached segment for ``key``, touched so eviction keeps it."""
        path = self.path_for(key)
        try:
            if path.stat().st_size == 0:
                return None
            os.utime(path)
        except OSError:
            return None
        return path

    def partial_path(self, key: str) -> Path:
        """Where an encode writes before :meth:`commit`: unique per call, so
        two renders encoding the same segment never write one file."""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / f".{key}.{uuid.uuid4().hex}.part{_SUFFIX}"

    def commit(self, partial: Path, key: str) -> Path:
        final = self.path_for(key)
        partial.replace(final)
        return final

    def evict(self, *, keep: set[str]) -> None:
        """Delete the least recently used entries until the cache fits,
        never one of ``keep``. Best effort: a file another render removed
        first, or one that cannot be removed, is skipped."""
        try:
            listing = list(self.root.iterdir())
        except OSError:
            return
        entries: list[Path] = []
        for p in listing:
            if p.name.startswith(".") and p.name.endswith(f".part{_SUFFIX}"):
                # A partial a crashed or cancelled render left behind; one
                # still being written is younger than a day.
                try:
                    if time.time() - p.stat().st_mtime > _STALE_PARTIAL_SECONDS:
                        p.unlink()
                except OSError:
                    pass
            elif p.suffix == _SUFFIX:
                entries.append(p)
        sized: list[tuple[float, int, Path]] = []
        for p in entries:
            try:
                st = p.stat()
            except OSError:
                continue
            sized.append((st.st_mtime, st.st_size, p))
        total = sum(size for _, size, _ in sized)
        for _, size, p in sorted(sized):
            if total <= self.max_bytes:
                break
            if p.stem in keep:
                continue
            try:
                p.unlink()
            except OSError:
                continue
            total -= size
        if total > self.max_bytes:
            logger.info("render segment cache holds %d bytes, over its cap, all in use", total)
