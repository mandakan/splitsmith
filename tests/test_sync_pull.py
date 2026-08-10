"""Pull planning: manifest diff against recorded doc_versions."""

from splitsmith.sync.pull import plan_pull, remote_doc_key
from splitsmith.sync.state import SyncState

M = [
    {"doc_kind": "match", "slug": None, "stage_number": None, "version": 3, "updated_at": "2026-08-10T10:00:00+00:00"},
    {"doc_kind": "project", "slug": "anna", "stage_number": None, "version": 7, "updated_at": "2026-08-10T10:00:00+00:00"},
    {"doc_kind": "audit", "slug": "anna", "stage_number": 3, "version": 2, "updated_at": "2026-08-10T10:00:00+00:00"},
]


def test_plan_pull_diffs_versions():
    state = SyncState(doc_versions={"match": 3, "project/anna": 6})
    changed = plan_pull(M, state)
    keys = {remote_doc_key(rd) for rd in changed}
    assert keys == {"project/anna", "audit/anna/3"}  # match unchanged; audit never seen


def test_plan_pull_empty_manifest():
    assert plan_pull([], SyncState()) == []
