"""AppState + endpoint tests for the stage-list editor (#521).

Structured to grow: this module starts with ``AppState.delete_audit``
coverage (Task 4) and a later task adds HTTP endpoint tests for the
stage add/remove/rename routes alongside it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# The autouse SPLITSMITH_AUTO_BEEP_DISABLED fixture in test_ui_server.py is
# module-scoped and is NOT inherited here, so auto-beep would otherwise fire
# during seeding.
@pytest.fixture(autouse=True)
def _no_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _local_state(tmp_path: Path):
    """Build a local-mode app bound to a scaffolded Match folder and
    return ``(state, project_root)`` with ``current_match_root`` already
    set to ``project_root`` -- the ContextVar the ``/api/matches/{id}/``
    alias middleware would set per-request, mirrored here so AppState
    methods can be called directly without going through HTTP.

    Callers must reset the returned token via ``current_match_root.reset``
    (a fixture-scoped ``yield`` is not used here since each test needs a
    tight window around its own assertions).
    """
    from splitsmith.ui.server import current_match_root
    from tests.test_ui_server import _match_create_app

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root)
    state = app.state.splitsmith_state
    token = current_match_root.set(project_root)
    return state, project_root, token


class TestDeleteAudit:
    def test_removes_the_local_doc_and_its_backup(self, tmp_path: Path) -> None:
        from splitsmith.ui.server import current_match_root

        state, _project_root, token = _local_state(tmp_path)
        try:
            audit_file = state._audit_file("me", 3)
            audit_file.parent.mkdir(parents=True, exist_ok=True)
            audit_file.write_text(json.dumps({"shots": []}), encoding="utf-8")
            backup = audit_file.with_suffix(audit_file.suffix + ".bak")
            backup.write_text(json.dumps({"shots": ["stale"]}), encoding="utf-8")

            assert state.delete_audit("me", 3) is True
            assert not audit_file.exists()
            assert not backup.exists()
        finally:
            current_match_root.reset(token)

    def test_is_false_when_no_doc_exists(self, tmp_path: Path) -> None:
        from splitsmith.ui.server import current_match_root

        state, _project_root, token = _local_state(tmp_path)
        try:
            assert state.delete_audit("me", 99) is False
        finally:
            current_match_root.reset(token)

    def test_removes_live_doc_when_no_backup_exists(self, tmp_path: Path) -> None:
        """Only the live file present (no prior save rotated a .bak) --
        still reports True and leaves nothing behind."""
        from splitsmith.ui.server import current_match_root

        state, _project_root, token = _local_state(tmp_path)
        try:
            audit_file = state._audit_file("me", 5)
            audit_file.parent.mkdir(parents=True, exist_ok=True)
            audit_file.write_text(json.dumps({"shots": []}), encoding="utf-8")

            assert state.delete_audit("me", 5) is True
            assert not audit_file.exists()
        finally:
            current_match_root.reset(token)
