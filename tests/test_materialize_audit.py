"""``AppState.materialize_audit`` against a hosted state double (#1332).

Hosted, the audit doc lives in ``state_docs`` and an export reads it from
the worker's local ``audit/stage<N>.json``, which ``materialize_audit``
writes first. A worker serves many jobs, so a file an earlier job left
must not outlive the hosted doc it was copied from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from splitsmith.ui import server as server_mod
from splitsmith.ui.server import create_app
from tests.conftest import scaffold_match


def _hosted_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[dict[str, Any] | None]):
    root, _shooter_root = scaffold_match(tmp_path, name="Materialize")
    app = create_app(project_root=root, project_name="Materialize")
    state = app.state.splitsmith_state
    match_id = state.matches.known_ids()[0]
    # Any store makes the state hosted for ``audit_doc_target``; the audit
    # reads come from ``docs`` in order, as the hosted store would answer.
    monkeypatch.setattr(state, "_project_state", object())
    answers = iter(docs)
    monkeypatch.setattr(state, "load_audit", lambda slug, n: (next(answers), 1))
    return state, root, match_id


def test_materialize_removes_a_local_file_when_the_hosted_doc_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = {"stage_number": 1, "beep_time": 5.0, "shots": [{"shot_number": 1, "ms_after_beep": 500}]}
    state, root, match_id = _hosted_state(tmp_path, monkeypatch, [doc, None])
    tok_root = server_mod.current_match_root.set(root)
    tok_id = server_mod.current_match_id.set(match_id)
    try:
        first = state.materialize_audit("me", 1)
        assert json.loads(first.read_text(encoding="utf-8")) == doc

        # The hosted doc was deleted since (a stage removed, a reset): the
        # next job on this worker must not read the earlier job's copy.
        second = state.materialize_audit("me", 1)
    finally:
        server_mod.current_match_root.reset(tok_root)
        server_mod.current_match_id.reset(tok_id)

    assert second == first
    assert not second.exists()


def test_materialize_without_a_match_id_leaves_the_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no match bound, audit docs are files even on a hosted server:
    the local file is the doc, never a stale copy."""
    state, root, _match_id = _hosted_state(tmp_path, monkeypatch, [None])
    tok_root = server_mod.current_match_root.set(root)
    tok_id = server_mod.current_match_id.set(None)
    try:
        audit_file = state._audit_file("me", 1)
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("{}\n", encoding="utf-8")
        assert state.materialize_audit("me", 1) == audit_file
    finally:
        server_mod.current_match_root.reset(tok_root)
        server_mod.current_match_id.reset(tok_id)

    assert audit_file.exists()
