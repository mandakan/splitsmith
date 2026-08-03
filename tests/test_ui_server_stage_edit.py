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


@pytest.fixture(autouse=True)
def _reset_match_root_context() -> None:
    """``_seeded_match`` leaves ``current_match_root`` set (so callers can
    read audit paths directly via ``state._audit_file(...)`` without a
    separate token dance) rather than resetting it inline like
    ``_local_state`` does. Clear it after every test so a leaked value
    from one test's match root never leaks into the next test's direct
    ``AppState`` calls.
    """
    yield
    from splitsmith.ui.server import current_match_root

    current_match_root.set(None)


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


def _seeded_match(tmp_path: Path, *, stages: int, shooters: list[str]):
    """Seed a Match folder with stages numbered ``1..stages`` and a
    ``MatchProject`` for each slug in ``shooters``, then bind an app +
    ``_MatchClient`` to it.

    Returns ``(client, state)``. ``current_match_root`` is left set to the
    match root (see ``_reset_match_root_context`` above) so a test can call
    ``state._audit_file(...)`` directly, the same way it would go through
    HTTP requests via ``client``.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject
    from splitsmith.ui.server import current_match_root
    from tests.test_ui_server import _match_create_app, _MatchClient

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Test Match")
    for slug in shooters:
        match.add_shooter(root, match_model.Shooter(slug=slug, name=slug))
    match.stages = [
        match_model.MatchStageDefinition(stage_number=n, stage_name=f"Stage {n}")
        for n in range(1, stages + 1)
    ]
    match.save(root)

    for slug in shooters:
        shooter_root = match_model.Match.shooter_root(root, slug)
        project = MatchProject.init(shooter_root, name="Test Match")
        project.init_placeholder_stages(stages)
        project.save(shooter_root)

    app = _match_create_app(project_root=root, project_name="Test Match")
    client = _MatchClient(app)
    state = app.state.splitsmith_state
    current_match_root.set(root)
    return client, state


class TestEditMatchStages:
    def test_rename_preserves_the_audit_doc(self, tmp_path: Path) -> None:
        """The discriminating assertion is the artifact, not the status code:
        a rename returns 200 both before and after this change exists."""
        client, state = _seeded_match(tmp_path, stages=3, shooters=["me"])
        audit_file = state._audit_file("me", 2)
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text(json.dumps({"shots": [1.0, 2.0]}), encoding="utf-8")

        resp = client.put(
            "/api/match/stages",
            json={
                "stages": [
                    {"stage_number": 1, "stage_name": "Stage 1"},
                    {"stage_number": 2, "stage_name": "El Presidente"},
                    {"stage_number": 3, "stage_name": "Stage 3"},
                ]
            },
        )

        assert resp.status_code == 200
        assert resp.json()["renamed"] == [2]
        assert json.loads(audit_file.read_text())["shots"] == [1.0, 2.0]

    def test_removing_a_stage_keeps_later_stages_audits_byte_identical(self, tmp_path: Path) -> None:
        client, state = _seeded_match(tmp_path, stages=5, shooters=["me"])
        for n in (3, 4, 5):
            f = state._audit_file("me", n)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"stage": n}), encoding="utf-8")
        before_4 = state._audit_file("me", 4).read_bytes()
        before_5 = state._audit_file("me", 5).read_bytes()

        resp = client.put(
            "/api/match/stages",
            json={"stages": [{"stage_number": n, "stage_name": f"Stage {n}"} for n in (1, 2, 4, 5)]},
        )

        assert resp.status_code == 200
        assert resp.json()["removed"] == [3]
        assert resp.json()["errors"] == []
        assert not state._audit_file("me", 3).exists()
        assert state._audit_file("me", 4).read_bytes() == before_4
        assert state._audit_file("me", 5).read_bytes() == before_5

    def test_add_after_remove_allocates_six_not_the_freed_three(self, tmp_path: Path) -> None:
        client, _state = _seeded_match(tmp_path, stages=5, shooters=["me"])
        first = client.put(
            "/api/match/stages",
            json={"stages": [{"stage_number": n, "stage_name": f"Stage {n}"} for n in (1, 2, 4, 5)]},
        )
        assert first.status_code == 200

        resp = client.put(
            "/api/match/stages",
            json={
                "stages": [{"stage_number": n, "stage_name": f"Stage {n}"} for n in (1, 2, 4, 5)]
                + [{"stage_number": None, "stage_name": "Standards"}]
            },
        )

        assert resp.status_code == 200
        assert resp.json()["added"] == [6]

    def test_removing_every_stage_is_rejected(self, tmp_path: Path) -> None:
        client, _state = _seeded_match(tmp_path, stages=2, shooters=["me"])
        resp = client.put("/api/match/stages", json={"stages": []})
        assert resp.status_code == 400
        assert "at least one stage" in json.dumps(resp.json())

    def test_unknown_stage_number_is_a_400(self, tmp_path: Path) -> None:
        client, _state = _seeded_match(tmp_path, stages=2, shooters=["me"])
        resp = client.put(
            "/api/match/stages",
            json={
                "stages": [
                    {"stage_number": 1, "stage_name": "Stage 1"},
                    {"stage_number": 2, "stage_name": "Stage 2"},
                    {"stage_number": 99, "stage_name": "Ghost"},
                ]
            },
        )
        assert resp.status_code == 400

    def test_removal_fans_out_to_every_shooter(self, tmp_path: Path) -> None:
        client, _state = _seeded_match(tmp_path, stages=3, shooters=["anna", "erik"])
        resp = client.put(
            "/api/match/stages",
            json={
                "stages": [
                    {"stage_number": 1, "stage_name": "Stage 1"},
                    {"stage_number": 2, "stage_name": "Stage 2"},
                ]
            },
        )

        assert resp.status_code == 200
        assert resp.json()["errors"] == []
        assert sorted(s["slug"] for s in resp.json()["shooters"]) == ["anna", "erik"]
        for slug in ("anna", "erik"):
            project = client.get(f"/api/shooters/{slug}/project").json()
            assert [s["stage_number"] for s in project["stages"]] == [1, 2]

    def test_shooter_project_save_conflict_is_a_409_not_a_200(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost optimistic-lock race on a shooter project's save must not
        be swallowed into ``errors`` as an ordinary per-shooter failure --
        that would return 200 with the stage-list edit silently unsaved for
        that shooter (Task 6 review finding 2)."""
        from splitsmith.db import StateConflictError
        from splitsmith.ui.project import MatchProject

        client, _state = _seeded_match(tmp_path, stages=3, shooters=["me"])

        def _raise_conflict(self: MatchProject, root: Path) -> None:
            raise StateConflictError("scripted conflict")

        monkeypatch.setattr(MatchProject, "save", _raise_conflict)

        resp = client.put(
            "/api/match/stages",
            json={
                "stages": [
                    {"stage_number": 1, "stage_name": "Stage 1"},
                    {"stage_number": 2, "stage_name": "El Presidente"},
                    {"stage_number": 3, "stage_name": "Stage 3"},
                ]
            },
        )

        assert resp.status_code == 409
