"""The ``DocStore`` protocol and the bundle it hands back."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

Doc = dict[str, Any]
#: A document and the version it was read at. ``(None, 0)`` when it does not
#: exist. Files always report 0; the database counts up from 1.
Versioned = tuple[Doc | None, int]


@dataclass
class MatchBundleDocs:
    """Every document of one match, each with its version, read in as few
    round trips as the implementation allows."""

    match: Doc | None
    match_version: int
    projects: dict[str, tuple[Doc, int]] = field(default_factory=dict)
    audits: dict[str, dict[int, tuple[Doc, int]]] = field(default_factory=dict)


class DocStore(Protocol):
    """One match's documents. Every method is synchronous.

    A store is bound to one match when it is built; nothing here reads a
    request's context. ``version`` on a save is the version the caller read
    the document at, and the return value is the new one. ``update_*`` is
    the read-modify-write: ``merge`` is a pure function from the current
    document to the wanted one, applied under a process lock on files and
    under an optimistic version with a bounded re-merge in the database, so
    it must be safe to run more than once.

    ``update_audit_at`` is the handler's shape of the same thing: ``merge``
    receives the document (``None`` when there is none yet) and the version
    it is being applied at, so it can compare a client's expected version
    and answer 409, or raise any other HTTP error, from inside the
    read-modify-write; and the call returns the merged document with its
    new version, which is what the handler's response is built from. An
    error ``merge`` raises is never a conflict and is never retried.
    """

    #: Whether a project document loaded from here may carry another
    #: machine's paths and must be confined to the shooter root.
    confines_paths: bool

    def load_match(self) -> Versioned: ...
    def save_match(self, doc: Doc, *, version: int) -> int: ...

    def list_projects(self) -> dict[str, tuple[Doc, int]]: ...
    def load_project(self, slug: str) -> Versioned: ...
    def save_project(self, slug: str, doc: Doc, *, version: int) -> int: ...
    def delete_project(self, slug: str) -> None: ...

    def load_audit(self, slug: str, stage: int) -> Versioned: ...
    def list_audits(self, slug: str) -> dict[int, tuple[Doc, int]]: ...
    def save_audit(self, slug: str, stage: int, doc: Doc, *, version: int) -> int: ...
    def delete_audit(self, slug: str, stage: int) -> bool: ...
    def update_audit(
        self, slug: str, stage: int, merge: Callable[[Doc], Doc], default: Callable[[], Doc]
    ) -> int: ...
    def update_audit_at(
        self, slug: str, stage: int, merge: Callable[[Doc | None, int], Doc]
    ) -> tuple[Doc, int]: ...

    def load_export_runs(self, slug: str) -> Versioned: ...
    def save_export_runs(self, slug: str, doc: Doc, *, version: int) -> int: ...
    def update_export_runs(self, slug: str, merge: Callable[[Doc | None], Doc]) -> int: ...

    def bundle(self) -> MatchBundleDocs: ...
