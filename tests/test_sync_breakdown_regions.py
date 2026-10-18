"""Breakdown's regions sync both ways (spec 2026-10-18-breakdown-hosted-sync-design),
end to end through the real ``run_sync`` against a hosted app: the phone
edits regions on the mirror, the desktop edits another, and one sync brings
both sides to the same list."""

from __future__ import annotations

import pytest

pytest.importorskip("moto")

from .test_sync_stage_note import SLUG, _Desktop, hosted  # noqa: E402, F401

DESK = {"id": "evt-1", "kind": "movement", "start": 1.0, "end": 2.0, "source": "manual"}
PHONE = {"id": "evt-2", "kind": "reload", "start": 3.0, "end": 4.0, "source": "manual"}


def _events_url(d: _Desktop) -> str:
    return f"/api/matches/{d.mid}/shooters/{SLUG}/stages/1/events"


def test_a_phone_region_and_a_desktop_edit_converge_in_one_sync(hosted, tmp_path, monkeypatch):  # noqa: F811
    d = _Desktop(hosted, tmp_path, monkeypatch)
    d.edit(events=[DESK], events_seeded=4)
    d.sync()
    assert d.hosted_doc()["events"] == [DESK]

    # The desktop's sync said it merges regions, so the mirror takes the edit.
    version = d.hosted_doc()["_version"]
    resp = d.hosted.put(_events_url(d), json={"events": [DESK, PHONE], "_version": version})
    assert resp.status_code == 200, resp.text

    moved = {**DESK, "end": 2.5}
    d.edit(events=[moved], events_seeded=4)
    report = d.sync()
    assert report.conflicts == []
    assert d.local()["events"] == [moved, PHONE]
    assert d.hosted_doc()["events"] == [moved, PHONE]


def test_a_region_deleted_on_the_phone_stays_deleted_on_the_desktop(
    hosted, tmp_path, monkeypatch  # noqa: F811
):
    d = _Desktop(hosted, tmp_path, monkeypatch)
    d.edit(events=[DESK, PHONE], events_seeded=4)
    d.sync()
    version = d.hosted_doc()["_version"]
    resp = d.hosted.put(_events_url(d), json={"events": [DESK], "_version": version})
    assert resp.status_code == 200, resp.text
    d.sync()
    assert d.local()["events"] == [DESK]
    assert d.hosted_doc()["events"] == [DESK]
