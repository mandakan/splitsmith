"""The desktop's document tree as a :class:`DocStore`."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from ...export_runs import LOG_FILENAME
from ...match_model import MATCH_FILE, Match
from ...match_project import PROJECT_FILE, audit_docs_on_disk
from .port import Doc, MatchBundleDocs, Versioned

AUDIT_DIR = "audit"


def _audit_file(shooter_root: Path, stage: int) -> Path:
    return shooter_root / AUDIT_DIR / f"stage{stage}.json"


def _read(path: Path) -> Versioned:
    if not path.exists():
        return None, 0
    return json.loads(path.read_text(encoding="utf-8")), 0


def _write(path: Path, doc: Doc, *, backup: bool) -> None:
    """Temp file next to ``path`` -> optional ``.bak`` rotate -> rename.

    The temp name is unique (``mkstemp``): a fixed ``<name>.tmp`` is shared by
    every writer to the document, which is the idiom that lost export-run
    records once (#629). The caller holds whatever lock the document needs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")
        if backup and path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            bak.unlink(missing_ok=True)
            path.replace(bak)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


class FileDocStore:
    """One match folder. Versions are always 0: there is no optimistic lock
    on files, the locks are.

    ``audit_lock`` must be the same object the sync pull holds across its
    audit merge (#1075), so a handler's ``update_audit`` and a pull exclude
    each other; ``AppState`` owns it and passes it in. ``shooter_root``
    resolves a slug to its folder; the default is the match layout, and
    ``AppState`` passes its own resolver where the legacy single-shooter
    fallback applies.
    """

    confines_paths = False
    documents_on_disk = True

    def __init__(
        self,
        match_root: Path,
        *,
        audit_lock: AbstractContextManager[object] | None = None,
        export_run_lock: AbstractContextManager[object] | None = None,
        project_lock: AbstractContextManager[object] | None = None,
        shooter_root: Callable[[str], Path] | None = None,
    ) -> None:
        self._root = match_root
        self._audit_lock = audit_lock if audit_lock is not None else nullcontext()
        self._export_run_lock = export_run_lock if export_run_lock is not None else nullcontext()
        self._project_lock = project_lock if project_lock is not None else nullcontext()
        self._shooter_root = shooter_root or (lambda slug: Match.shooter_root(match_root, slug))

    # -- match -------------------------------------------------------------
    def load_match(self) -> Versioned:
        return _read(self._root / MATCH_FILE)

    def save_match(self, doc: Doc, *, version: int) -> int:
        _write(self._root / MATCH_FILE, doc, backup=False)
        return 0

    # -- projects ----------------------------------------------------------
    def list_projects(self) -> dict[str, tuple[Doc, int]]:
        match, _ = self.load_match()
        out: dict[str, tuple[Doc, int]] = {}
        for slug in (match or {}).get("shooters", []):
            doc, version = self.load_project(slug)
            if doc is not None:
                out[slug] = (doc, version)
        return out

    def load_project(self, slug: str) -> Versioned:
        return _read(self._shooter_root(slug) / PROJECT_FILE)

    def save_project(self, slug: str, doc: Doc, *, version: int) -> int:
        _write(self._shooter_root(slug) / PROJECT_FILE, doc, backup=False)
        return 0

    def update_project(self, slug: str, merge: Callable[[Doc], Doc]) -> int:
        path = self._shooter_root(slug) / PROJECT_FILE
        with self._project_lock:
            current, _ = _read(path)
            if current is None:
                raise LookupError(f"shooter {slug!r} has no project document")
            _write(path, merge(current), backup=False)
        return 0

    def delete_match_docs(self) -> int:
        removed = 0
        for slug in self.list_projects():
            root = self._shooter_root(slug)
            removed += len(self.list_audits(slug))
            self._delete_tree_docs(root / AUDIT_DIR)
            for name in (PROJECT_FILE, LOG_FILENAME):
                if (root / name).exists():
                    (root / name).unlink()
                    removed += 1
        if (self._root / MATCH_FILE).exists():
            (self._root / MATCH_FILE).unlink()
            removed += 1
        return removed

    @staticmethod
    def _delete_tree_docs(audit_dir: Path) -> None:
        if audit_dir.is_dir():
            for path in audit_dir.glob("stage*.json*"):
                path.unlink(missing_ok=True)

    def delete_project(self, slug: str) -> None:
        root = self._shooter_root(slug)
        (root / PROJECT_FILE).unlink(missing_ok=True)
        audit_dir = root / AUDIT_DIR
        if audit_dir.is_dir():
            for f in audit_dir.glob("stage*.json*"):
                f.unlink(missing_ok=True)

    # -- audits ------------------------------------------------------------
    def load_audit(self, slug: str, stage: int) -> Versioned:
        return _read(_audit_file(self._shooter_root(slug), stage))

    def list_audits(self, slug: str) -> dict[int, tuple[Doc, int]]:
        audit_dir = self._shooter_root(slug) / AUDIT_DIR
        return {stage: (doc, 0) for stage, doc in audit_docs_on_disk(audit_dir).items()}

    def save_audit(self, slug: str, stage: int, doc: Doc, *, version: int) -> int:
        with self._audit_lock:
            _write(_audit_file(self._shooter_root(slug), stage), doc, backup=True)
        return 0

    def delete_audit(self, slug: str, stage: int) -> bool:
        path = _audit_file(self._shooter_root(slug), stage)
        bak = path.with_suffix(path.suffix + ".bak")
        with self._audit_lock:
            removed = path.exists()
            path.unlink(missing_ok=True)
            bak.unlink(missing_ok=True)
        return removed

    def update_audit(
        self, slug: str, stage: int, merge: Callable[[Doc], Doc], default: Callable[[], Doc]
    ) -> int:
        path = _audit_file(self._shooter_root(slug), stage)
        with self._audit_lock:
            current, _ = _read(path)
            _write(path, merge(current if current is not None else default()), backup=True)
        return 0

    def update_audit_at(
        self, slug: str, stage: int, merge: Callable[[Doc | None, int], Doc]
    ) -> tuple[Doc, int]:
        path = _audit_file(self._shooter_root(slug), stage)
        with self._audit_lock:
            current, version = _read(path)
            merged = merge(current, version)
            _write(path, merged, backup=True)
        return merged, 0

    # -- export runs -------------------------------------------------------
    def load_export_runs(self, slug: str) -> Versioned:
        return _read(self._shooter_root(slug) / LOG_FILENAME)

    def save_export_runs(self, slug: str, doc: Doc, *, version: int) -> int:
        """A plain write: the caller holds whatever lock its read-modify-write
        needs (``update_export_runs`` is the locked form)."""
        _write(self._shooter_root(slug) / LOG_FILENAME, doc, backup=False)
        return 0

    def update_export_runs(self, slug: str, merge: Callable[[Doc | None], Doc]) -> int:
        path = self._shooter_root(slug) / LOG_FILENAME
        with self._export_run_lock:
            current, _ = _read(path)
            _write(path, merge(current), backup=False)
        return 0

    # -- bundle ------------------------------------------------------------
    def bundle(self) -> MatchBundleDocs:
        match, version = self.load_match()
        projects = self.list_projects()
        return MatchBundleDocs(
            match=match,
            match_version=version,
            projects=projects,
            audits={slug: self.list_audits(slug) for slug in projects},
        )
