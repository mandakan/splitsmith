"""A hosted grid's tiles: planned from storage, pulled one stage at a time (#1691).

The whole-match grid job used to pull every stage's exported trim before it
planned anything: about 11 GB for HFO Masters, onto a Cloud Run disk that
is memory, so it was killed while loading. Planning needs only each tile's
length, size and frame rate, and a render needs one stage's tiles at a time.

``HostedGridTiles`` is handed to ``server._load_compare_bundles`` and then to
``render_grid_mp4``:

- **probe**: ffprobe reads an exported trim's header over a presigned URL
  (a range read or two), never the file. A tile cut from the source (#1681)
  is cut once to be measured and dropped again. The results are
  ``tile_meta``, which the grid job hands its stage jobs so every job plans
  from the same numbers without probing again.
- **identities**: each tile keyed by its stored object (key, size, version),
  or for a cut by its source object and the cut's window, so a segment's
  cache key is the same on every worker whatever its local copy looks like.
- **acquire**: pulls (or cuts) one stage's tiles and keeps them until another
  stage is acquired or :meth:`HostedGridTiles.release` is called, so a stage
  read several times in a row (its hold still, its edges, its encode) is
  pulled once, and the disk never holds two stages.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .. import fcpxml_gen
from ..compare.project_loader import CompareShooterBundle
from ..config import VideoMetadata
from ..match_project import MatchProject, StageEntry, StageVideo
from ..proxy import redact_urls
from . import export_storage

logger = logging.getLogger(__name__)

#: How long a planning probe's URL lives: it reads a header, in seconds.
PROBE_URL_TTL_S = 600
#: How many headers planning reads at once.
PROBE_WORKERS = 8


@dataclass(frozen=True)
class _Cut:
    project: MatchProject
    root: Path
    stage: StageEntry
    video: StageVideo


class HostedGridTiles:
    """The hosted half of a grid's tiles; see the module docstring."""

    def __init__(self, *, tile_meta: Mapping[str, Mapping[str, object]] | None = None) -> None:
        self.tile_meta: dict[str, dict[str, object]] = {k: dict(v) for k, v in (tile_meta or {}).items()}
        self.identities: dict[str, str] = {}
        self._cuts: dict[str, _Cut] = {}
        self._projects: dict[str, MatchProject] = {}
        self._by_stage: dict[int, list[Path]] = {}
        self._held: int | None = None
        self._pulled: list[Path] = []
        # A dry planning pass (:meth:`planning_pass`) records what to measure
        # instead of measuring it, so :meth:`measure` can probe in parallel.
        self._dry = False
        self._to_probe: dict[str, tuple[MatchProject, Path]] = {}
        self._failed_cuts: set[str] = set()

    # -- planning (the loader's hooks) ------------------------------------

    def probe_for(self, project: MatchProject) -> Callable[[Path], VideoMetadata]:
        """The loader's ``probe`` for one shooter's tiles."""

        def probe(path: Path) -> VideoMetadata:
            known = self.tile_meta.get(str(path))
            if known is not None:
                return VideoMetadata.model_validate(known)
            if self._dry:
                if str(path) not in self._cuts:
                    self._to_probe[str(path)] = (project, path)
                return VideoMetadata(
                    width=0, height=0, duration_seconds=0.0, frame_rate_num=0, frame_rate_den=1
                )
            meta = self._probe_stored(project, path)
            self.tile_meta[str(path)] = meta.model_dump()
            return meta

        return probe

    def plan_cut(
        self, path: Path, *, project: MatchProject, root: Path, stage: StageEntry, video: StageVideo
    ) -> Path | None:
        """A tile the job will cut from the source at ``path``. Measured now
        (cut, probed, dropped) unless a planner before this one measured it;
        ``None`` when the cut fails, as for any missing tile."""
        cut = _Cut(project, root, stage, video)
        self._cuts[str(path)] = cut
        if str(path) in self._failed_cuts:
            return None
        if str(path) in self.tile_meta or self._dry:
            return path
        try:
            made = self._cut(path, cut)
            if made is None:
                return None
            self.tile_meta[str(path)] = fcpxml_gen.probe_video(made).model_dump()
        finally:
            path.unlink(missing_ok=True)
        return path

    @contextlib.contextmanager
    def planning_pass(self) -> Iterator[None]:
        """A load inside this ``with`` measures nothing: it records the
        exports to probe and the cuts to make, for :meth:`measure`. Load
        again after it: every tile is then known."""
        self._dry = True
        try:
            yield
        finally:
            self._dry = False

    def measure(self, *, workers: int = PROBE_WORKERS) -> None:
        """Measure what a planning pass recorded: the exports' headers over
        URLs, ``workers`` at a time (each is a range read or two, so a match's
        48 take seconds, not minutes), then the source cuts one at a time,
        since each holds a source on this disk while it cuts."""
        pending = [
            (key, project, path)
            for key, (project, path) in self._to_probe.items()
            if key not in self.tile_meta
        ]
        self._to_probe = {}
        if pending:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                metas = list(pool.map(lambda job: self._probe_stored(job[1], job[2]), pending))
            for (key, _project, _path), meta in zip(pending, metas, strict=True):
                self.tile_meta[key] = meta.model_dump()
        for key, cut in list(self._cuts.items()):
            if key in self.tile_meta or key in self._failed_cuts:
                continue
            path = Path(key)
            try:
                made = self._cut(path, cut)
                if made is None:
                    self._failed_cuts.add(key)
                    continue
                self.tile_meta[key] = fcpxml_gen.probe_video(made).model_dump()
            finally:
                path.unlink(missing_ok=True)

    def index(self, bundles: Sequence[CompareShooterBundle]) -> None:
        """Learn each tile's stage and identity once the bundles are loaded."""
        if self._dry:
            return
        for bundle in bundles:
            if bundle.project is None:
                continue
            for number, stage in bundle.stages_by_number.items():
                paths = [stage.trim_path] + ([stage.inset.trim_path] if stage.inset is not None else [])
                for path in paths:
                    self._projects[str(path)] = bundle.project
                    self._by_stage.setdefault(number, []).append(path)
                    self.identities[str(path)] = self._identity(bundle.project, path)

    # -- rendering --------------------------------------------------------

    @contextlib.contextmanager
    def acquire(self, stage_number: int) -> Iterator[None]:
        """Stage ``stage_number``'s tiles on this disk (``mp4_grid.TileAccess``).
        They stay until another stage is acquired or :meth:`release`: the
        reads of one stage come in a row, and each would otherwise pull it
        again. A file that was here before is never deleted."""
        if self._held != stage_number:
            self.release()
            self._held = stage_number
            for path in self._by_stage.get(stage_number, []):
                if path.exists():
                    continue
                cut = self._cuts.get(str(path))
                made = (
                    self._cut(path, cut) is not None
                    if cut is not None
                    else export_storage.pull_export_file(self._projects[str(path)], path)
                )
                if made:
                    self._pulled.append(path)
        yield

    def release(self) -> None:
        """Delete every tile this object pulled or cut. Call it when the
        render is done; a job's scratch would otherwise keep a stage."""
        for path in self._pulled:
            path.unlink(missing_ok=True)
        self._pulled = []
        self._held = None

    # -- internals --------------------------------------------------------

    def _probe_stored(self, project: MatchProject, path: Path) -> VideoMetadata:
        key = export_storage._storage_export_key(project, path)
        storage = project._storage
        if key is None or storage is None or not storage.supports_presigned_get:
            # No URL to read: the file itself, pulled for the probe only.
            if not export_storage.pull_export_file(project, path):
                raise FileNotFoundError(f"grid tile not in storage: {path.name}")
            try:
                return fcpxml_gen.probe_video(path)
            finally:
                path.unlink(missing_ok=True)
        url = storage.presign_get_url(key, expires_in=PROBE_URL_TTL_S)
        try:
            return fcpxml_gen.probe_video(url)  # type: ignore[arg-type]
        except Exception as exc:
            raise fcpxml_gen.FFprobeError(f"probing {path.name}: {redact_urls(str(exc))}") from None

    def _identity(self, project: MatchProject, path: Path) -> str:
        cut = self._cuts.get(str(path))
        storage = project._storage
        if cut is not None:
            source = storage.stat(cut.video.path.as_posix()) if storage is not None else None
            return ":".join(
                str(part)
                for part in (
                    "source-cut",
                    cut.video.path,
                    source.size if source else "",
                    source.last_modified.isoformat() if source and source.last_modified else "",
                    path.name,
                    cut.video.beep_time,
                    cut.stage.time_seconds,
                    cut.project.trim_pre_buffer_seconds,
                    cut.project.trim_post_buffer_seconds,
                )
            )
        key = export_storage._storage_export_key(project, path)
        stored = storage.stat(key) if storage is not None and key is not None else None
        if stored is None:
            # Unknown version: never reuse a segment keyed by it.
            return f"missing:{path.name}:{uuid.uuid4().hex}"
        modified = stored.last_modified.isoformat() if stored.last_modified else ""
        return f"storage:{key}:{stored.size}:{modified}:{stored.etag or ''}"

    @staticmethod
    def _cut(path: Path, cut: _Cut) -> Path | None:
        from .server import _cut_stage_from_source

        return _cut_stage_from_source(cut.project, cut.root, path, cut.stage, cut.video)
