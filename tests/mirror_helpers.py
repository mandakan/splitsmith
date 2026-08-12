"""Seeding vocabulary for tests that drive a desktop *mirror* match.

Extracted from ``tests/test_mirror_read_only.py`` (#845). Three test files
were importing ``_alias_url`` / ``_seed_mirror`` from that module by their
private names, and ``test_shot_id.py`` had grown its own near-duplicate of
the seed-a-stage-with-an-audit-doc dance. Half a fixture vocabulary in each
of two test modules is worse than one shared module, so this is it.

Everything here goes through the *real* routes a desktop sync uses --
``POST /api/sync/matches`` then the ``docs/...`` upserts -- rather than
writing rows directly. That is deliberate: a helper that bypasses the
adoption path would seed a match the mirror gate does not actually treat
as ``desktop``-origin, and the gate is what most of these tests are about.
Native (hosted-origin) matches need a different seed; those helpers stay in
``test_mirror_read_only.py`` next to their tests.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry

CREATE_URL = "/api/sync/matches"


def sync_docs_url(match_id: str, kind: str) -> str:
    """The desktop-sync doc upsert route for one doc ``kind``."""
    return f"/api/sync/matches/{match_id}/docs/{kind}"


def alias_url(match_id: str, rest: str) -> str:
    """A match-scoped app route, the shape a phone actually calls."""
    return f"/api/matches/{match_id}/{rest}"


def seed_mirror(client: TestClient, match_id: str, name: str) -> None:
    """Adopt ``match_id`` as a desktop mirror and push a minimal match doc.

    Mirrors the create + doc-upsert dance real desktop sync (Task 4) does:
    ``POST /api/sync/matches`` then ``PUT .../docs/match``. An empty
    roster is enough for the gate + shooter-list surfaces under test.
    """
    created = client.post(CREATE_URL, json={"match_id": match_id, "name": name})
    assert created.status_code == 200, created.text
    doc = match_model.Match(match_id=match_id, name=name, shooters=[], stages=[]).model_dump(mode="json")
    put = client.put(sync_docs_url(match_id, "match"), params={"expected_version": 0}, json=doc)
    assert put.status_code == 200, put.text


def legacy_audit_doc(time: float) -> dict:
    """One kept manual shot with no id and no candidate_number.

    The one shape whose derived id is not convergent across two sides:
    ``derive_shot_id`` keys it off the rounded time, so a nudge moves it.
    """
    return {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": None,
                "time": time,
                "ms_after_beep": int(round((time - 5.0) * 1000)),
            }
        ],
        "audit_events": [],
    }


def seed_mirror_stage_with_audit(client: TestClient, match_id: str, name: str, doc: dict) -> None:
    """Mirror with shooter "alice", stage 1, and ``doc`` as her stage-1 audit.

    The roster PUT expects version 1, not 0: :func:`seed_mirror` already
    inserted the match doc at version 0 and the insert moved it on.
    """
    seed_mirror(client, match_id, name)
    roster = match_model.Match(match_id=match_id, name=name, shooters=["alice"], stages=[]).model_dump(
        mode="json"
    )
    put_roster = client.put(sync_docs_url(match_id, "match"), params={"expected_version": 1}, json=roster)
    assert put_roster.status_code == 200, put_roster.text
    stage = StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)
    project_doc = MatchProject(name=name, competitor_name="Alice", stages=[stage]).model_dump(mode="json")
    put_project = client.put(
        sync_docs_url(match_id, "project/alice"),
        params={"expected_version": 0},
        json=project_doc,
    )
    assert put_project.status_code == 200, put_project.text
    put_audit = client.put(sync_docs_url(match_id, "audit/alice/1"), params={"expected_version": 0}, json=doc)
    assert put_audit.status_code == 200, put_audit.text
