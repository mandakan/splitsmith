"""The storage port: one match's documents behind one synchronous interface.

Spec ``docs/superpowers/specs/2026-10-19-storage-port-design.md`` (#1581).
:class:`FileDocStore` is the desktop's ``match.json`` / ``project.json`` /
``audit/stage<N>.json`` / ``export_runs.json`` tree; :class:`DbDocStore` is
the hosted ``state_docs`` table. ``tests/ui/test_docstore_contract.py`` runs
one suite against both, which is what lets a handler stop asking which
world it is in.
"""

from .db import DbDocStore
from .files import FileDocStore
from .port import Doc, DocStore, MatchBundleDocs, Versioned

__all__ = ["DbDocStore", "Doc", "DocStore", "FileDocStore", "MatchBundleDocs", "Versioned"]
