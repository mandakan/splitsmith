"""Tests for the hosted sync HTTP client + push executor
(``splitsmith.sync.client`` / ``splitsmith.sync.push``, Task 8 of the
desktop-to-hosted sync MVP, #631).

No real network and no hosted app: both httpx clients ``HostedSyncClient``
wraps are driven by ``httpx.MockTransport`` doubles against an in-memory
fake (``_FakeHosted``) that tracks every request in one ordered list so
tests can assert cross-transport ordering (media before docs) as easily
as same-transport ordering.

Cases:
  1. Happy path: 2 media items (clip + sidecar) + 3 docs (match, project,
     audit) all land, every media request precedes every doc request,
     and the returned ``PushReport`` matches.
  2. A second run against the same match root with nothing touched on
     disk uploads 0 media (rsync-style skip) and, since #797, PUTs 0
     docs too (content-hash skip) - only ``docs_skipped`` moves.
  3. A part PUT failing partway through leaves the already-uploaded
     item's digest recorded in ``sync_state.json`` and never reaches the
     docs phase; a rerun re-uploads only the failed item.
 11. Doc delta skipping (#797): mutating one audit doc between two runs
     re-pushes exactly that doc; a doc PUT failure does not record that
     doc's hash (a rerun retries it, the others stay skipped); and
     ``format_push_message`` names the unchanged count only when it's
     nonzero.
  4. A legacy (no ``match.json``) project aborts with ``SyncClientError``
     before any HTTP request - the planner's ``errors`` guard fires
     pre-network.
  5. A 401 from ``ensure_match`` surfaces the token-revoked message and
     nothing beyond that call happens.
  6. Client-level coverage beyond the push floor: ``ensure_match`` maps
     409 to the "not a mirror" message, ``put_doc`` returns the server's
     version, and ``upload_media`` chunks a file across multiple parts
     (not just the single-part case the push tests exercise) and returns
     the correct sha256.
  7. A part PUT failure triggers exactly one abort call carrying the
     matching key + upload_id, and the original error still propagates -
     even when the abort call itself fails (a 500 from the abort route
     must not mask the original error). The happy path makes no abort
     call at all.
  8. Phase timings (#631 Task 13): a real ``PhaseTimer`` passed as
     ``timer=`` records the same four phases, in order, that
     ``PushReport.timings`` reports internally.
  9. ``PushReport``'s new fields default to empty/zero so pre-Task-13
     callers that construct one directly (without the new kwargs) still
     work.
 10. Concurrent part PUTs (#713): parts overlap in flight, ``complete``
     receives them sorted by part_number with per-part etags, and a
     multi-part failure still aborts exactly once.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.observability import PhaseTimer
from splitsmith.sync.client import HostedSyncClient, SyncClientError
from splitsmith.sync.plan import DocItem, MediaItem, build_push_plan
from splitsmith.sync.push import MediaItemTiming, PushReport, format_push_message, run_push
from splitsmith.sync.state import SyncState, load_sync_state

TRIMMED_NAME = "stage1_cam_abc123_trimmed.mp4"
SIDECAR_NAME = "stage1_cam_abc123_trimmed.params.json"


# ---------------------------------------------------------------------------
# Shared fixture builder (mirrors tests/test_sync_plan.py's basic match)
# ---------------------------------------------------------------------------


def _build_match(tmp_path: Path) -> tuple[Path, str]:
    """One match, one shooter ("alice"), one stage, with a trimmed clip +
    sidecar + audit doc already on disk. Returns (match_root, match_id)."""
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Test Match")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(root)

    slug = "alice"
    shooter = match_model.Shooter(slug=slug, name="Alice")
    match.add_shooter(root, shooter)
    shooter_root = match_model.Match.shooter_root(root, slug)

    project = MatchProject.init(shooter_root, name="Test Match")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0)]
    project.save(shooter_root)

    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(exist_ok=True)
    (trimmed_dir / TRIMMED_NAME).write_bytes(b"x" * 1024)
    (trimmed_dir / SIDECAR_NAME).write_text(json.dumps({"pre_buffer_seconds": 5.0}), encoding="utf-8")

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps({"detection": "ensemble", "shots": []}), encoding="utf-8"
    )

    return root, match.match_id


# ---------------------------------------------------------------------------
# In-memory double for the /api/sync/* surface + presigned media PUTs
# ---------------------------------------------------------------------------


class _FakeHosted:
    """Tracks every request (across both the ``http`` and ``media_http``
    transports) in one ordered ``calls`` list, so a test can assert
    cross-client ordering as easily as same-client ordering."""

    def __init__(self, *, part_size: int = 10 * 1024 * 1024) -> None:
        self.part_size = part_size
        self.calls: list[str] = []
        self.doc_puts: list[tuple[str, dict]] = []
        self.aborts: list[tuple[str, str]] = []  # (key, upload_id)
        self.completes: list[tuple[str, list[dict]]] = []  # (key, parts)
        self.match_status = 200
        self.fail_keys: set[str] = set()  # remote keys whose part PUT 500s
        self.fail_doc_paths: set[str] = set()  # doc URL paths whose PUT 500s (#797)
        self.abort_status = 200
        #: Optional (key, part_number) callback invoked inside each part
        #: PUT handler, before the response is built - lets a test block
        #: one part until another is in flight to prove concurrency (#713).
        self.media_put_hook: Callable[[str, str], None] | None = None
        self._upload_counter = 0

    def clients(self) -> HostedSyncClient:
        http = httpx.Client(
            base_url="https://hosted.example",
            headers={"Authorization": "Bearer test-token"},
            transport=httpx.MockTransport(self._http_handler),
        )
        media_http = httpx.Client(transport=httpx.MockTransport(self._media_handler))
        return HostedSyncClient(http=http, media_http=media_http)

    def _http_handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else {}

        if method == "POST" and path == "/api/sync/matches":
            self.calls.append("ensure_match")
            if self.match_status != 200:
                return httpx.Response(self.match_status, json={"detail": "denied"})
            return httpx.Response(200, json={"match_id": body["match_id"], "origin": "desktop"})

        if method == "POST" and path.endswith("/media/create"):
            self._upload_counter += 1
            self.calls.append(f"media_create:{body['key']}")
            return httpx.Response(
                200,
                json={
                    "upload_id": f"upload-{self._upload_counter}",
                    "key": body["key"],
                    "part_size": self.part_size,
                },
            )

        if method == "POST" and path.endswith("/media/part-url"):
            self.calls.append(f"media_part_url:{body['key']}:{body['part_number']}")
            return httpx.Response(
                200,
                json={"url": f"https://media.example/{body['key']}?part={body['part_number']}"},
            )

        if method == "POST" and path.endswith("/media/complete"):
            self.calls.append(f"media_complete:{body['key']}")
            self.completes.append((body["key"], body["parts"]))
            return httpx.Response(200, json={"size": 0})

        if method == "POST" and path.endswith("/media/abort"):
            self.calls.append(f"media_abort:{body['key']}")
            self.aborts.append((body["key"], body["upload_id"]))
            if self.abort_status != 200:
                return httpx.Response(self.abort_status, json={"detail": "could not abort"})
            return httpx.Response(200, json={})

        if method == "PUT" and "/docs/" in path:
            self.calls.append(f"doc_put:{path}")
            if path in self.fail_doc_paths:
                return httpx.Response(500, text="doc put failed")
            self.doc_puts.append((path, body))
            return httpx.Response(200, json={"version": len(self.doc_puts)})

        raise AssertionError(f"unexpected http request: {method} {path}")

    def _media_handler(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        part = request.url.params.get("part", "")
        self.calls.append(f"media_put:{key}")
        if self.media_put_hook is not None:
            self.media_put_hook(key, part)
        if key in self.fail_keys:
            return httpx.Response(500, text="upload failed")
        return httpx.Response(200, headers={"ETag": f'"etag-{key}-{part}"'})


# ---------------------------------------------------------------------------
# Case 1: happy path
# ---------------------------------------------------------------------------


def test_happy_path_pushes_media_before_docs_and_reports_correctly(tmp_path: Path) -> None:
    root, match_id = _build_match(tmp_path)
    fake = _FakeHosted()

    report = run_push(root, client=fake.clients())

    assert report.uploaded == 2
    assert report.skipped == 0
    assert report.docs == 3
    assert report.docs_skipped == 0

    # New instrumentation fields (#631 Task 13): the four phases always
    # run on a successful push, bytes_uploaded is the sum of the two
    # uploaded items' sizes, and media_items lists exactly those two
    # items (skipped items - none here - are excluded).
    assert set(report.timings) == {"plan", "ensure_match", "media", "docs"}
    for seconds in report.timings.values():
        assert seconds >= 0
    trimmed_dir = root / "shooters" / "alice" / "trimmed"
    expected_bytes = (trimmed_dir / TRIMMED_NAME).stat().st_size + (trimmed_dir / SIDECAR_NAME).stat().st_size
    assert report.bytes_uploaded == expected_bytes
    assert {mi.remote_key for mi in report.media_items} == {
        f"matches/{match_id}/shooters/alice/trimmed/{TRIMMED_NAME}",
        f"matches/{match_id}/shooters/alice/trimmed/{SIDECAR_NAME}",
    }
    assert sum(mi.bytes for mi in report.media_items) == expected_bytes
    for mi in report.media_items:
        assert mi.seconds >= 0

    media_indices = [
        i
        for i, c in enumerate(fake.calls)
        if c.startswith(("media_create", "media_part_url", "media_put", "media_complete"))
    ]
    doc_indices = [i for i, c in enumerate(fake.calls) if c.startswith("doc_put")]
    assert media_indices, "expected media calls"
    assert doc_indices, "expected doc calls"
    assert max(media_indices) < min(doc_indices), fake.calls

    assert fake.calls[0] == "ensure_match"
    assert not any(c.startswith("media_abort") for c in fake.calls), "happy path must not abort"

    doc_paths = {path for path, _ in fake.doc_puts}
    assert doc_paths == {
        f"/api/sync/matches/{match_id}/docs/match",
        f"/api/sync/matches/{match_id}/docs/project/alice",
        f"/api/sync/matches/{match_id}/docs/audit/alice/1",
    }

    state = load_sync_state(root)
    assert state.last_synced_at is not None
    clip_key = f"matches/{match_id}/shooters/alice/trimmed/{TRIMMED_NAME}"
    sidecar_key = f"matches/{match_id}/shooters/alice/trimmed/{SIDECAR_NAME}"
    assert set(state.items) == {clip_key, sidecar_key}
    assert (
        state.items[clip_key].sha256
        == hashlib.sha256((root / "shooters" / "alice" / "trimmed" / TRIMMED_NAME).read_bytes()).hexdigest()
    )


# ---------------------------------------------------------------------------
# Case 2: second run uploads 0 media
# ---------------------------------------------------------------------------


def test_second_run_with_nothing_touched_uploads_zero_media_and_zero_docs(tmp_path: Path) -> None:
    root, _match_id = _build_match(tmp_path)
    run_push(root, client=_FakeHosted().clients())

    fake2 = _FakeHosted()
    report2 = run_push(root, client=fake2.clients())

    assert report2.uploaded == 0
    assert report2.skipped == 2
    # #797: docs are content-hash skipped the same way media is
    # size/mtime skipped - nothing changed, so nothing is PUT.
    assert report2.docs == 0
    assert report2.docs_skipped == 3
    assert not any(c.startswith("media_") for c in fake2.calls)
    assert fake2.calls[0] == "ensure_match"
    assert sum(c.startswith("doc_put") for c in fake2.calls) == 0

    # A skipped run uploads nothing: media_items is empty and
    # bytes_uploaded is 0, even though the phases still ran.
    assert report2.media_items == []
    assert report2.bytes_uploaded == 0
    assert set(report2.timings) == {"plan", "ensure_match", "media", "docs"}


# ---------------------------------------------------------------------------
# Case 3: mid-push part failure is crash-safe
# ---------------------------------------------------------------------------


def test_mid_push_part_failure_leaves_prior_items_recorded_and_rerun_only_retries_failed(
    tmp_path: Path,
) -> None:
    root, match_id = _build_match(tmp_path)
    sidecar_key = f"matches/{match_id}/shooters/alice/trimmed/{SIDECAR_NAME}"
    clip_key = f"matches/{match_id}/shooters/alice/trimmed/{TRIMMED_NAME}"

    fake = _FakeHosted()
    fake.fail_keys = {sidecar_key}

    with pytest.raises(httpx.HTTPStatusError):
        run_push(root, client=fake.clients())

    # Docs never reached; the clip (processed before the sidecar - sorted
    # glob order) is recorded, the sidecar is not; the sidecar's half-open
    # multipart upload was aborted rather than left orphaned on R2.
    assert not any(c.startswith("doc_put") for c in fake.calls)
    assert [c for c in fake.calls if c.startswith("media_abort")] == [f"media_abort:{sidecar_key}"]
    assert len(fake.aborts) == 1
    assert fake.aborts[0][0] == sidecar_key
    state = load_sync_state(root)
    assert set(state.items) == {clip_key}

    # Rerun with the failure fixed: only the sidecar re-uploads.
    fake2 = _FakeHosted()
    report2 = run_push(root, client=fake2.clients())

    assert report2.uploaded == 1
    assert report2.skipped == 1
    assert report2.docs == 3
    assert [mi.remote_key for mi in report2.media_items] == [sidecar_key]
    media_create_calls = [c for c in fake2.calls if c.startswith("media_create")]
    assert media_create_calls == [f"media_create:{sidecar_key}"]

    state2 = load_sync_state(root)
    assert set(state2.items) == {clip_key, sidecar_key}


# ---------------------------------------------------------------------------
# Case 4: validation errors abort before any network call
# ---------------------------------------------------------------------------


def test_legacy_project_aborts_before_any_network_call(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    project = MatchProject.init(root, name="Legacy Project")
    project.save(root)

    fake = _FakeHosted()
    with pytest.raises(SyncClientError, match="convert it to a match"):
        run_push(root, client=fake.clients())

    assert fake.calls == []


# ---------------------------------------------------------------------------
# Case 5: 401 surfaces the token message
# ---------------------------------------------------------------------------


def test_401_on_ensure_match_surfaces_token_message(tmp_path: Path) -> None:
    root, _match_id = _build_match(tmp_path)
    fake = _FakeHosted()
    fake.match_status = 401

    with pytest.raises(SyncClientError, match="generate a new one on your account page"):
        run_push(root, client=fake.clients())

    assert fake.calls == ["ensure_match"]


# ---------------------------------------------------------------------------
# Case 6: client-level coverage beyond the push floor
# ---------------------------------------------------------------------------


def test_ensure_match_409_maps_to_not_a_mirror_message(tmp_path: Path) -> None:
    fake = _FakeHosted()
    fake.match_status = 409
    client = fake.clients()

    with pytest.raises(SyncClientError, match="already exists and is not a mirror"):
        client.ensure_match("some-match", "Some Match")


def test_put_doc_returns_server_version(tmp_path: Path) -> None:
    fake = _FakeHosted()
    client = fake.clients()

    item = DocItem(kind="match", body={"name": "Test Match"})
    version = client.put_doc("some-match", item, expected_version=0)

    assert version == 1
    assert fake.doc_puts == [("/api/sync/matches/some-match/docs/match", {"name": "Test Match"})]


def test_upload_media_chunks_across_multiple_parts_and_returns_correct_sha256(tmp_path: Path) -> None:
    data = bytes(range(256)) * 10  # 2560 bytes
    local_path = tmp_path / "clip.mp4"
    local_path.write_bytes(data)

    fake = _FakeHosted(part_size=1000)  # 3 parts: 1000, 1000, 560
    client = fake.clients()

    item = MediaItem(
        local_path=local_path,
        remote_key="matches/m1/shooters/alice/trimmed/clip.mp4",
        size=len(data),
        mtime_ns=0,
    )
    progress_calls: list[int] = []
    sha256 = client.upload_media("m1", item, progress=progress_calls.append)

    assert sha256 == hashlib.sha256(data).hexdigest()
    # Parts upload concurrently (#713), so per-part progress and part-url
    # ordering are nondeterministic; the totals and the set of parts are not.
    assert sorted(progress_calls) == [560, 1000, 1000]
    part_url_calls = [c for c in fake.calls if c.startswith("media_part_url")]
    assert sorted(part_url_calls) == [
        f"media_part_url:{item.remote_key}:1",
        f"media_part_url:{item.remote_key}:2",
        f"media_part_url:{item.remote_key}:3",
    ]
    put_calls = [c for c in fake.calls if c.startswith("media_put")]
    assert put_calls == [f"media_put:{item.remote_key}"] * 3


def test_parts_upload_concurrently_and_complete_stays_ordered(tmp_path: Path) -> None:
    """Part PUTs overlap in flight (#713) and ``complete`` still receives
    parts sorted by part_number with each part's own etag.

    Part 1's PUT handler blocks until part 2's has run: a sequential
    uploader deadlocks there (surfacing as the 10s timeout error below),
    a concurrent one sails through - and completes part 2 before part 1,
    proving the sort before ``complete`` is load-bearing.
    """
    data = bytes(range(256)) * 10  # 2560 bytes
    local_path = tmp_path / "clip.mp4"
    local_path.write_bytes(data)

    fake = _FakeHosted(part_size=1000)  # 3 parts: 1000, 1000, 560
    part2_done = threading.Event()

    def hook(key: str, part: str) -> None:
        if part == "2":
            part2_done.set()
        if part == "1" and not part2_done.wait(timeout=10):
            raise AssertionError("part 2 never started while part 1 was in flight - uploads are sequential")

    fake.media_put_hook = hook
    client = fake.clients()

    item = MediaItem(
        local_path=local_path,
        remote_key="matches/m1/shooters/alice/trimmed/clip.mp4",
        size=len(data),
        mtime_ns=0,
    )
    sha256 = client.upload_media("m1", item)

    assert sha256 == hashlib.sha256(data).hexdigest()
    assert len(fake.completes) == 1
    key, parts = fake.completes[0]
    assert key == item.remote_key
    assert parts == [
        {"part_number": 1, "etag": f'"etag-{item.remote_key}-1"'},
        {"part_number": 2, "etag": f'"etag-{item.remote_key}-2"'},
        {"part_number": 3, "etag": f'"etag-{item.remote_key}-3"'},
    ]


# ---------------------------------------------------------------------------
# Case 7: a failed multipart upload is aborted, never left orphaned on R2
# ---------------------------------------------------------------------------


def test_part_put_failure_triggers_one_abort_with_matching_key_and_upload_id(tmp_path: Path) -> None:
    local_path = tmp_path / "clip.mp4"
    local_path.write_bytes(b"x" * 1024)
    remote_key = "matches/m1/shooters/alice/trimmed/clip.mp4"

    fake = _FakeHosted()
    fake.fail_keys = {remote_key}
    client = fake.clients()

    item = MediaItem(local_path=local_path, remote_key=remote_key, size=1024, mtime_ns=0)

    with pytest.raises(httpx.HTTPStatusError):
        client.upload_media("m1", item)

    abort_calls = [c for c in fake.calls if c.startswith("media_abort")]
    assert abort_calls == [f"media_abort:{remote_key}"]
    assert fake.aborts == [(remote_key, "upload-1")]


def test_multipart_failure_under_concurrency_still_aborts_exactly_once(tmp_path: Path) -> None:
    """Several concurrent part PUTs failing at once (#713) must produce one
    propagated error and exactly one abort call, not one abort per part."""
    data = b"x" * 2560
    local_path = tmp_path / "clip.mp4"
    local_path.write_bytes(data)
    remote_key = "matches/m1/shooters/alice/trimmed/clip.mp4"

    fake = _FakeHosted(part_size=1000)  # 3 parts, every PUT 500s
    fake.fail_keys = {remote_key}
    client = fake.clients()

    item = MediaItem(local_path=local_path, remote_key=remote_key, size=len(data), mtime_ns=0)

    with pytest.raises(httpx.HTTPStatusError):
        client.upload_media("m1", item)

    assert fake.aborts == [(remote_key, "upload-1")]
    assert not any(c.startswith("media_complete") for c in fake.calls)


def test_abort_failing_does_not_mask_the_original_part_put_error(tmp_path: Path) -> None:
    local_path = tmp_path / "clip.mp4"
    local_path.write_bytes(b"x" * 1024)
    remote_key = "matches/m1/shooters/alice/trimmed/clip.mp4"

    fake = _FakeHosted()
    fake.fail_keys = {remote_key}
    fake.abort_status = 500  # the abort route itself is broken too
    client = fake.clients()

    item = MediaItem(local_path=local_path, remote_key=remote_key, size=1024, mtime_ns=0)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.upload_media("m1", item)

    # The propagated error is the original part-PUT 500, not anything
    # about the abort call - the client never calls .raise_for_status()
    # on the abort response, so a broken abort route can't surface here.
    assert exc_info.value.request.url.path == f"/{remote_key}"
    assert fake.aborts == [(remote_key, "upload-1")]


# ---------------------------------------------------------------------------
# Case 8: phase timings mirror onto a caller-provided PhaseTimer
# ---------------------------------------------------------------------------


def test_provided_timer_records_the_four_phases_in_order(tmp_path: Path) -> None:
    root, _match_id = _build_match(tmp_path)
    fake = _FakeHosted()
    timer = PhaseTimer()

    report = run_push(root, client=fake.clients(), timer=timer)

    built = timer.build()
    assert [phase["name"] for phase in built["phases"]] == ["plan", "ensure_match", "media", "docs"]
    for phase in built["phases"]:
        assert phase["ms"] >= 0
    # The internal accounting (always populated, regardless of ``timer``)
    # reports the same four phase names.
    assert set(report.timings) == {"plan", "ensure_match", "media", "docs"}


def test_no_timer_still_populates_internal_timings(tmp_path: Path) -> None:
    root, _match_id = _build_match(tmp_path)
    fake = _FakeHosted()

    report = run_push(root, client=fake.clients())

    assert set(report.timings) == {"plan", "ensure_match", "media", "docs"}
    for seconds in report.timings.values():
        assert seconds >= 0


# ---------------------------------------------------------------------------
# Case 9: PushReport's new fields default to empty/zero
# ---------------------------------------------------------------------------


def test_push_report_new_fields_default_empty() -> None:
    report = PushReport(uploaded=0, skipped=0, docs=0)
    assert report.timings == {}
    assert report.bytes_uploaded == 0
    assert report.media_items == []
    assert report.docs_skipped == 0
    # Defaults are per-instance, not a shared mutable object (the
    # pydantic default_factory footgun this guards against).
    report.timings["x"] = 1.0
    assert PushReport(uploaded=0, skipped=0, docs=0).timings == {}


def test_media_item_timing_round_trips_fields() -> None:
    item = MediaItemTiming(remote_key="matches/m1/shooters/alice/trimmed/clip.mp4", bytes=1024, seconds=0.5)
    assert item.remote_key == "matches/m1/shooters/alice/trimmed/clip.mp4"
    assert item.bytes == 1024
    assert item.seconds == 0.5


# ---------------------------------------------------------------------------
# Sanity: build_push_plan still importable/usable from this module (no
# accidental shadowing of the Task 7 planner by anything Task 8 adds).
# ---------------------------------------------------------------------------


def test_build_push_plan_still_reachable(tmp_path: Path) -> None:
    root, _match_id = _build_match(tmp_path)
    plan = build_push_plan(root, sync_state=SyncState())
    assert not plan.errors
    assert len(plan.media) == 2
    assert len(plan.docs) == 3


# ---------------------------------------------------------------------------
# Case 11: doc delta skipping (#797)
# ---------------------------------------------------------------------------


def test_mutating_one_audit_doc_between_runs_repushes_only_that_doc(tmp_path: Path) -> None:
    root, match_id = _build_match(tmp_path)
    run_push(root, client=_FakeHosted().clients())

    audit_file = root / "shooters" / "alice" / "audit" / "stage1.json"
    audit_file.write_text(json.dumps({"detection": "ensemble", "shots": [{"index": 0}]}), encoding="utf-8")

    fake2 = _FakeHosted()
    report2 = run_push(root, client=fake2.clients())

    assert report2.docs == 1
    assert report2.docs_skipped == 2
    doc_paths = {path for path, _ in fake2.doc_puts}
    assert doc_paths == {f"/api/sync/matches/{match_id}/docs/audit/alice/1"}


def test_doc_put_failure_does_not_record_its_hash_and_rerun_retries_only_it(tmp_path: Path) -> None:
    root, match_id = _build_match(tmp_path)
    project_path = f"/api/sync/matches/{match_id}/docs/project/alice"

    fake = _FakeHosted()
    fake.fail_doc_paths = {project_path}

    with pytest.raises(httpx.HTTPStatusError):
        run_push(root, client=fake.clients())

    # The match doc (planned before the project doc) landed and its hash
    # was recorded; the project doc failed and was never recorded; the
    # audit doc (planned after) was never attempted at all.
    state = load_sync_state(root)
    assert state.doc_hashes.get("match") is not None
    assert "project/alice" not in state.doc_hashes
    assert "audit/alice/1" not in state.doc_hashes

    # Rerun with the failure fixed: only the previously-failed project
    # doc (plus anything never attempted) is still unrecorded, so only
    # those re-push - the match doc is skipped this time.
    fake2 = _FakeHosted()
    report2 = run_push(root, client=fake2.clients())

    doc_paths = {path for path, _ in fake2.doc_puts}
    assert f"/api/sync/matches/{match_id}/docs/match" not in doc_paths
    assert project_path in doc_paths
    assert report2.docs_skipped == 1  # only the match doc was already up to date


def test_format_push_message_omits_unchanged_suffix_when_zero() -> None:
    report = PushReport(uploaded=0, skipped=96, docs=53, docs_skipped=0)
    assert format_push_message(report) == "Synced: 0 uploaded, 96 skipped, 53 docs"


def test_format_push_message_names_unchanged_count_when_nonzero() -> None:
    report = PushReport(uploaded=0, skipped=96, docs=1, docs_skipped=52)
    assert format_push_message(report) == "Synced: 0 uploaded, 96 skipped, 1 docs (52 unchanged)"
