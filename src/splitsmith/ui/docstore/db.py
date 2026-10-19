"""The hosted ``state_docs`` table as a :class:`DocStore`."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ...async_bridge import run_sync
from .port import Doc, MatchBundleDocs, Versioned

if TYPE_CHECKING:
    # Hosted-only; a module-level import would break the slim install.
    from ...db import ProjectStateStore


def _conflict_excs() -> tuple[type[BaseException], ...]:
    """The optimistic-lock conflict type, resolved lazily so importing this
    module never pulls the hosted db dependencies."""
    try:
        from ...db import StateConflictError
    except ImportError:  # pragma: no cover - slim install never builds a DbDocStore
        return ()
    return (StateConflictError,)


def _backoff(attempt: int) -> None:
    """Nothing on the first try; then a growing, jittered pause so writers
    that just collided do not collide again in lockstep."""
    if attempt:
        time.sleep(random.uniform(0.0, 0.005 * min(attempt, 8)))  # noqa: S311 - timing jitter, not security


class DbDocStore:
    """One match in ``state_docs``, every call bridged through ``run_sync``
    (the hosted rule: never ``asyncio.run`` on this path).

    ``update_*`` loads, merges and saves at the version it read; when a
    concurrent writer bumped the version first it reloads the winner's
    document and re-applies ``merge`` to that, so the concurrent edit
    survives instead of being clobbered. This is what
    ``_save_audit_with_remerge`` and ``_record_export_run`` did by hand,
    with one difference: they gave up after four attempts, which the
    contract suite shows eight simultaneous writers exhaust (every retry
    races the others again). The cap is higher here and each retry waits a
    short, growing, jittered moment first, so a burst of writers to one
    document settles instead of dropping edits.
    """

    confines_paths = True
    documents_on_disk = False

    def __init__(self, store: ProjectStateStore, match_id: str, *, max_attempts: int = 32) -> None:
        self._store = store
        self._mid = match_id
        self._max_attempts = max_attempts

    # -- match -------------------------------------------------------------
    def load_match(self) -> Versioned:
        return run_sync(self._store.load_match(self._mid))

    def save_match(self, doc: Doc, *, version: int) -> int:
        return run_sync(self._store.save_match(self._mid, doc, expected_version=version))

    # -- projects ----------------------------------------------------------
    def list_projects(self) -> dict[str, tuple[Doc, int]]:
        docs = run_sync(self._store.load_docs_for_matches([self._mid]))[self._mid]
        return {slug: (doc, docs.project_versions.get(slug, 0)) for slug, doc in docs.projects.items()}

    def load_project(self, slug: str) -> Versioned:
        return run_sync(self._store.load_project(self._mid, slug))

    def save_project(self, slug: str, doc: Doc, *, version: int) -> int:
        return run_sync(self._store.save_project(self._mid, slug, doc, expected_version=version))

    def delete_project(self, slug: str) -> None:
        run_sync(self._store.delete_shooter(self._mid, slug))

    def delete_match_docs(self) -> int:
        return run_sync(self._store.delete_match(self._mid))

    # -- audits ------------------------------------------------------------
    def load_audit(self, slug: str, stage: int) -> Versioned:
        return run_sync(self._store.load_audit(self._mid, slug, stage))

    def _audit_versions(self, slug: str) -> dict[int, int]:
        return {
            m.stage_number: m.version
            for m in run_sync(self._store.list_doc_meta(self._mid))
            if m.doc_kind == "audit" and m.slug == slug and m.stage_number is not None
        }

    def list_audits(self, slug: str) -> dict[int, tuple[Doc, int]]:
        versions = self._audit_versions(slug)
        return {
            stage: (doc, versions.get(stage, 0))
            for _slug, stage, doc in run_sync(self._store.list_audit_docs(self._mid, slug))
        }

    def save_audit(self, slug: str, stage: int, doc: Doc, *, version: int) -> int:
        return run_sync(self._store.save_audit(self._mid, slug, stage, doc, expected_version=version))

    def delete_audit(self, slug: str, stage: int) -> bool:
        return run_sync(self._store.delete_audit(self._mid, slug, stage)) > 0

    def update_audit(
        self, slug: str, stage: int, merge: Callable[[Doc], Doc], default: Callable[[], Doc]
    ) -> int:
        conflicts = _conflict_excs()
        last: BaseException | None = None
        for attempt in range(self._max_attempts):
            _backoff(attempt)
            current, version = self.load_audit(slug, stage)
            try:
                return self.save_audit(
                    slug, stage, merge(current if current is not None else default()), version=version
                )
            except conflicts as exc:
                last = exc
        assert last is not None
        raise last

    def update_audit_at(
        self, slug: str, stage: int, merge: Callable[[Doc | None, int], Doc]
    ) -> tuple[Doc, int]:
        conflicts = _conflict_excs()
        last: BaseException | None = None
        for attempt in range(self._max_attempts):
            _backoff(attempt)
            current, version = self.load_audit(slug, stage)
            merged = merge(current, version)  # an error raised here is the handler's answer, not a conflict
            try:
                return merged, self.save_audit(slug, stage, merged, version=version)
            except conflicts as exc:
                last = exc
        assert last is not None
        raise last

    # -- export runs -------------------------------------------------------
    def load_export_runs(self, slug: str) -> Versioned:
        return run_sync(self._store.load_export_runs(self._mid, slug))

    def save_export_runs(self, slug: str, doc: Doc, *, version: int) -> int:
        return run_sync(self._store.save_export_runs(self._mid, slug, doc, expected_version=version))

    def update_export_runs(self, slug: str, merge: Callable[[Doc | None], Doc]) -> int:
        conflicts = _conflict_excs()
        last: BaseException | None = None
        for attempt in range(self._max_attempts):
            _backoff(attempt)
            current, version = self.load_export_runs(slug)
            try:
                return run_sync(
                    self._store.save_export_runs(self._mid, slug, merge(current), expected_version=version)
                )
            except conflicts as exc:
                last = exc
        assert last is not None
        raise last

    # -- bundle ------------------------------------------------------------
    def bundle(self) -> MatchBundleDocs:
        docs = run_sync(self._store.load_docs_for_matches([self._mid]))[self._mid]
        _match, match_version = self.load_match()
        meta = run_sync(self._store.list_doc_meta(self._mid))
        audit_versions: dict[str, dict[int, int]] = {}
        for m in meta:
            if m.doc_kind == "audit" and m.slug is not None and m.stage_number is not None:
                audit_versions.setdefault(m.slug, {})[m.stage_number] = m.version
        return MatchBundleDocs(
            match=docs.match,
            match_version=match_version,
            projects={slug: (doc, docs.project_versions.get(slug, 0)) for slug, doc in docs.projects.items()},
            audits={
                slug: {
                    stage: (doc, audit_versions.get(slug, {}).get(stage, 0)) for stage, doc in stages.items()
                }
                for slug, stages in docs.audits.items()
            },
        )
