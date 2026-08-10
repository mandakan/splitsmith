"""Audit event ids (bidirectional sync slice).

Every audit_events entry needs a unique ``id`` so the sync merge can
union event lists from two sides without double-appending. Server-side
appends stamp it at creation; the audit PUT stamps any client-authored
event that arrives without one (the SPA's "save" events).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter + one stage.

    Returns ``(client, url_base)`` - ``url_base`` is the
    ``/api/matches/{match_id}`` prefix the ``/api/shooters/...`` routes
    need (see the alias middleware in ``server.py``). No primary video
    assignment - the audit PUT/GET endpoints only need the stage to
    exist (``project.stage(stage_number)`` must not raise).
    """
    root, shooter_root = scaffold_match(tmp_path, name="Sync Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Sync Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def test_put_stage_audit_stamps_missing_event_ids(local_app_with_stage: tuple[TestClient, str]) -> None:
    """Client-authored events without ids get one; existing ids survive."""
    client, url_base = local_app_with_stage
    payload = {
        "shots": [],
        "audit_events": [
            {"ts": "2026-08-10T10:00:00+00:00", "kind": "save", "payload": {}},
            {"id": "keepme", "ts": "2026-08-10T10:01:00+00:00", "kind": "save", "payload": {}},
        ],
    }
    resp = client.put(f"{url_base}/shooters/me/stages/1/audit", json=payload)
    assert resp.status_code == 200
    events = resp.json()["audit_events"]
    assert events[1]["id"] == "keepme"
    new_id = events[0]["id"]
    assert isinstance(new_id, str) and len(new_id) == 32  # uuid4 hex
    # Re-reading returns the stamped doc, not the raw input.
    saved = client.get(f"{url_base}/shooters/me/stages/1/audit").json()
    assert saved["audit_events"][0]["id"] == new_id
