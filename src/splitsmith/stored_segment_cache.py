"""The segment cache with its entries also in object storage (#1691).

A hosted whole-match grid renders each stage in its own job, on whichever
Cloud Run execution takes it, and stitches them in another. The segments
have to meet somewhere every execution can read: each commit is pushed to
``<prefix>/<key><suffix>`` (``matches/<id>/render-segments``, so they go
with the match), and a lookup that misses this disk pulls the object.

Both directions are best-effort, like every storage cache: a failed pull is
a miss (the caller encodes), a failed push leaves the local commit (the
render still has its segment; only another worker loses the hit).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import derived
from .segment_cache import _SUFFIX, SegmentCache

logger = logging.getLogger(__name__)

#: How long the stitch may read a segment by URL, in seconds: a whole
#: match's stream copy, with room to spare.
STITCH_URL_TTL_S = 6 * 3600


@dataclass(frozen=True)
class StoredSegmentCache(SegmentCache):
    """A :class:`SegmentCache` whose commits are pushed to ``storage`` under
    ``prefix`` and whose lookups pull from there on a local miss."""

    storage: Any = field(kw_only=True)
    prefix: str = field(kw_only=True)
    #: Segments a ``fetch=False`` lookup left in storage: local path -> key.
    _remote: dict[str, str] = field(default_factory=dict, kw_only=True, compare=False, repr=False)

    def object_key(self, key: str, *, suffix: str = _SUFFIX) -> str:
        return f"{self.prefix}/{key}{suffix}"

    def lookup(self, key: str, *, suffix: str = _SUFFIX, fetch: bool = True) -> Path | None:
        hit = super().lookup(key, suffix=suffix)
        if hit is not None:
            return hit
        name = self.object_key(key, suffix=suffix)
        if not fetch and getattr(self.storage, "supports_presigned_get", False):
            # The stitch reads it from storage (:meth:`concat_source`): a 4K
            # match's segments would not fit beside the output on a disk
            # that is memory.
            try:
                present = self.storage.exists(name)
            except Exception as exc:  # noqa: BLE001 -- a storage hiccup is a miss
                logger.info("render segments: exists(%s) failed: %s", name, exc)
                return None
            if not present:
                return None
            local = self.path_for(key, suffix=suffix)
            self._remote[str(local)] = name
            return local
        self.root.mkdir(parents=True, exist_ok=True)
        if not derived.download(
            self.storage, self.object_key(key, suffix=suffix), self.path_for(key, suffix=suffix)
        ):
            return None
        return super().lookup(key, suffix=suffix)

    def commit(self, partial: Path, key: str, *, suffix: str = _SUFFIX) -> Path:
        final = super().commit(partial, key, suffix=suffix)
        name = self.object_key(key, suffix=suffix)
        try:
            with final.open("rb") as fh:
                self.storage.upload_stream(name, fh)
        except Exception as exc:  # noqa: BLE001 -- a lost push costs another worker a hit, not this render
            logger.warning("render segments: push of %s failed: %s", name, exc)
        return final

    def concat_source(self, path: Path) -> str:
        """A presigned URL for a segment left in storage, else the file. The
        URL is a credential: it lives in the concat list only."""
        name = self._remote.get(str(path))
        if name is None or path.exists():
            return super().concat_source(path)
        return self.storage.presign_get_url(name, expires_in=STITCH_URL_TTL_S)

    def forget_local(self, key: str, *, suffix: str = _SUFFIX) -> None:
        """Delete this disk's copy only: a stage job is done with its
        segment once it is stored, and a Cloud Run disk is memory."""
        self.path_for(key, suffix=suffix).unlink(missing_ok=True)
