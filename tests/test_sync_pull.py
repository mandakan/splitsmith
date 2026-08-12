"""Pull planning: manifest diff against recorded doc_versions.

Also covers ``run_sync`` orchestration (Task 6): pull -> merge -> push,
against an in-memory ``FakeSyncClient`` (no HTTP).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.sync.base import load_base_doc
from splitsmith.sync.client import SyncClientError, SyncVersionConflict
from splitsmith.sync.plan import doc_identity_key
from splitsmith.sync.pull import plan_pull, remote_doc_key
from splitsmith.sync.run import run_sync
from splitsmith.sync.state import SyncState, load_sync_state, save_sync_state

M = [
    {
        "doc_kind": "match",
        "slug": None,
        "stage_number": None,
        "version": 3,
        "updated_at": "2026-08-10T10:00:00+00:00",
    },
    {
        "doc_kind": "project",
        "slug": "anna",
        "stage_number": None,
        "version": 7,
        "updated_at": "2026-08-10T10:00:00+00:00",
    },
    {
        "doc_kind": "audit",
        "slug": "anna",
        "stage_number": 3,
        "version": 2,
        "updated_at": "2026-08-10T10:00:00+00:00",
    },
]


def test_plan_pull_diffs_versions():
    state = SyncState(doc_versions={"match": 3, "project/anna": 6})
    changed = plan_pull(M, state)
    keys = {remote_doc_key(rd) for rd in changed}
    assert keys == {"project/anna", "audit/anna/3"}  # match unchanged; audit never seen


def test_plan_pull_empty_manifest():
    assert plan_pull([], SyncState()) == []


# ---------------------------------------------------------------------------
# run_sync orchestration
# ---------------------------------------------------------------------------


def make_synced_match(tmp_path: Path) -> Path:
    """One match, one shooter ("anna"), one stage with one primary video
    (beep fields at defaults) and one audit doc with a single shot -
    minimal valid local tree for ``run_sync``/``run_push``. Adapted from
    ``tests/test_sync_push.py``'s ``_build_match``; no trimmed media is
    written since these tests never exercise the media phase."""
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Test Match")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(root)

    slug = "anna"
    shooter = match_model.Shooter(slug=slug, name="Anna")
    match.add_shooter(root, shooter)
    shooter_root = match_model.Match.shooter_root(root, slug)

    video = StageVideo(path=Path("raw/stage1_cam1.mp4"), role="primary", stage_number=1)
    project = MatchProject.init(shooter_root, name="Test Match")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0, videos=[video])]
    project.save(shooter_root)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(exist_ok=True)
    # The shot carries a persisted id, a candidate_number and a time because
    # a real one does. Without the id the merge refuses the whole shot
    # section as legacy; without the other two it is held aside as having no
    # convergent id. Either way a stub would quietly stop these tests
    # exercising the coach-field merge at all.
    (audit_dir / "stage1.json").write_text(
        json.dumps(
            {
                "detection": "ensemble",
                "beep_time": 0.5,
                "shots": [{"id": "cand-1", "shot_number": 1, "candidate_number": 1, "time": 1.0}],
            }
        ),
        encoding="utf-8",
    )

    return root


class FakeSyncClient:
    """In-memory HostedSyncClient stand-in - no HTTP.

    ``docs`` maps doc identity key -> (body, version); the manifest and
    get_doc serve from it, put_doc version-bumps into it. ``fail_puts``
    scripts the first N put_doc calls to raise SyncVersionConflict.
    """

    def __init__(self) -> None:
        self.docs: dict[str, tuple[dict, int]] = {}
        self.put_calls: list[tuple[str, int]] = []
        self.fail_puts = 0
        self.get_doc_error: Exception | None = None

    def _identity(self, key: str) -> tuple[str, str | None, int | None]:
        parts = key.split("/")
        if parts[0] == "match":
            return "match", None, None
        if parts[0] == "project":
            return "project", parts[1], None
        return "audit", parts[1], int(parts[2])

    def ensure_match(self, match_id: str, name: str) -> None:
        pass

    def get_doc_manifest(self, match_id: str) -> list[dict]:
        out = []
        for key, (_body, version) in self.docs.items():
            kind, slug, stage = self._identity(key)
            out.append(
                {
                    "doc_kind": kind,
                    "slug": slug,
                    "stage_number": stage,
                    "version": version,
                    "updated_at": datetime(2026, 8, 10, 12, 0, tzinfo=UTC).isoformat(),
                }
            )
        return out

    def get_doc(self, match_id, kind, slug, stage_number):
        if self.get_doc_error is not None:
            raise self.get_doc_error
        return self.docs[doc_identity_key(kind, slug, stage_number)]

    def put_doc(self, match_id, item, *, expected_version: int) -> int:
        if self.fail_puts:
            self.fail_puts -= 1
            raise SyncVersionConflict("scripted conflict")
        key = doc_identity_key(item.kind, item.slug, item.stage_number)
        _, current = self.docs.get(key, ({}, 0))
        self.docs[key] = (item.body, current + 1)
        self.put_calls.append((key, expected_version))
        return current + 1

    def upload_media(self, match_id, item, *, progress) -> str:
        progress(item.size)
        return "0" * 64


def _first_sync(match_root) -> FakeSyncClient:
    """Baseline: one full push so state/bases/versions exist."""
    client = FakeSyncClient()
    run_sync(match_root, client=client)
    return client


def test_run_sync_pulls_and_merges_remote_coach_note(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    # Hosted-side edit: coach note lands on shot 1 of the audit doc.
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    body = {**body, "shots": [{**body["shots"][0], "coaching_note": "from-hosted"}]}
    client.docs[audit_key] = (body, version + 1)

    report = run_sync(match_root, client=client)

    slug, stage = audit_key.split("/")[1], audit_key.split("/")[2]
    audit_path = match_root / "shooters" / slug / "audit" / f"stage{stage}.json"
    assert '"from-hosted"' in audit_path.read_text(encoding="utf-8")
    assert report.pulled == 1 and report.conflicts == []
    state = load_sync_state(match_root)
    # Pushed-back merged doc: base == pushed body, version == PUT response.
    assert load_base_doc(match_root, audit_key) == client.docs[audit_key][0]
    assert state.doc_versions[audit_key] == client.docs[audit_key][1]


def test_run_sync_remote_beep_change_invalidates_and_reports(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    project_key = next(k for k in client.docs if k.startswith("project/"))
    body, version = client.docs[project_key]
    import copy as _copy

    body = _copy.deepcopy(body)
    video = body["stages"][0]["videos"][0]
    video["beep_time"] = 2.5
    video["beep_source"] = "manual"
    video["beep_reviewed"] = True
    client.docs[project_key] = (body, version + 1)

    report = run_sync(match_root, client=client)

    assert report.reprocess_videos == 1
    merged_remote, _ = client.docs[project_key]  # pushed back within same run
    v = merged_remote["stages"][0]["videos"][0]
    assert v["beep_time"] == 2.5 and v["processed"]["trim"] is False


def test_run_sync_version_conflict_retries_then_succeeds(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "x"}]},
        version + 1,
    )
    client.fail_puts = 1  # first PUT of attempt 1 loses the race

    report = run_sync(match_root, client=client)
    assert report.attempts == 2


def test_run_sync_version_conflict_exhausts_after_3(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "x"}]},
        version + 1,
    )
    client.fail_puts = 99

    with pytest.raises(SyncClientError, match="could not converge"):
        run_sync(match_root, client=client)


def test_run_sync_pull_failure_aborts_before_local_writes(tmp_path):
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (body, version + 1)  # something to pull
    client.get_doc_error = httpx.TransportError("offline")

    snapshot = {p: p.read_bytes() for p in sorted(match_root.rglob("*.json")) if p.is_file()}
    with pytest.raises(httpx.TransportError):
        run_sync(match_root, client=client)
    after = {p: p.read_bytes() for p in sorted(match_root.rglob("*.json")) if p.is_file()}
    assert after == snapshot  # nothing local was touched


def test_run_sync_crash_replay_after_merge_before_push(tmp_path):
    """Crash window: merge applied + bases updated, push never ran. The
    next run must push the merged docs as plain local changes - no
    double-merge, no lost data."""
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    body, version = client.docs[audit_key]
    client.docs[audit_key] = (
        {**body, "shots": [{**body["shots"][0], "coaching_note": "survives"}]},
        version + 1,
    )
    client.fail_puts = 99  # every PUT dies -> run raises after merge+base update
    with pytest.raises(SyncClientError):
        run_sync(match_root, client=client)

    client.fail_puts = 0  # "restart": same remote, healthy network
    report = run_sync(match_root, client=client)
    assert report.pulled == 0  # remote version already recorded during crash run
    pushed, _ = client.docs[audit_key]
    assert pushed["shots"][0]["coaching_note"] == "survives"


def test_run_sync_missing_local_audit_is_skipped_not_synthesized(tmp_path):
    """Remote has an audit doc for a stage whose local audit file does not
    exist - pull must note and skip, never synthesize an events-only local
    audit or push it back over hosted's copy."""
    match_root = make_synced_match(tmp_path)
    client = _first_sync(match_root)
    audit_key = next(k for k in client.docs if k.startswith("audit/"))
    slug, stage = audit_key.split("/")[1], audit_key.split("/")[2]
    audit_path = match_root / "shooters" / slug / "audit" / f"stage{stage}.json"

    # Simulate the doc never having been seen locally (e.g. first sync
    # after an upgrade): the local file, its base snapshot, and its
    # recorded version/hash are all gone, but the shooter still exists.
    audit_path.unlink()
    (match_root / "sync_base" / f"{audit_key}.json").unlink()
    state = load_sync_state(match_root)
    del state.doc_versions[audit_key]
    state.doc_hashes.pop(audit_key, None)
    save_sync_state(match_root, state)

    # Hosted-side edit so the doc is pulled (bumped version).
    body, version = client.docs[audit_key]
    hosted_body = {**body, "shots": [{**body["shots"][0], "coaching_note": "hosted-only"}]}
    client.docs[audit_key] = (hosted_body, version + 1)

    report = run_sync(match_root, client=client)

    assert not audit_path.exists()
    unchanged_hosted, _ = client.docs[audit_key]
    assert unchanged_hosted["shots"][0]["shot_number"] == 1
    assert unchanged_hosted["shots"][0].get("coaching_note") == "hosted-only"
    assert any("no local audit doc" in n for n in report.notes)
