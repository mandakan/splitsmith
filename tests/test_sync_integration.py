"""Integration round-trip for the desktop-to-hosted sync MVP (#631, Task 12).

This is the spec's acceptance test: a local match built under ``tmp_path``
pushes to a hosted mirror through the real ``/api/sync/*`` routes (a
moto-backed ``S3Storage`` double stands in for R2), the owner mints a
share token, and an anonymous viewer (session cookie cleared) reads the
shooter list and the pushed trim's stream redirect through the share
surface - desktop push all the way to anonymous share view, in one test.

Media bytes: the local trimmed clip pushed here is a plain byte file, not
a real MP4 built via ``tests/synthetic_media.py``. Nothing on this path
decodes it: ``build_push_plan`` only ``stat()``s it,
``HostedSyncClient.upload_media`` only streams + hashes it, the hosted
media routes only proxy bytes to S3, and
``GET .../videos/stream?kind=trim`` only calls ``storage.exists()``
before presigning a redirect - no ffprobe, no ffmpeg, no player.
``synthetic_media``'s real encodes exist for tests that actually decode
or play the file; this one never does, so plain bytes are the right
(and cheaper) choice here.

Presigned part uploads: moto's decorator-mode ``mock_aws()`` patches
botocore, not the raw HTTP a plain client would PUT to a presigned URL -
``test_sync_media_api.py`` already established the workaround (call
``storage._client.upload_part(...)`` directly instead of a real network
PUT). ``_media_handler`` below reuses that mechanism through an
``httpx.MockTransport`` so ``HostedSyncClient.upload_media`` runs
unmodified end to end: it PUTs to the presigned URL exactly like the real
desktop client would, and the transport translates that into the same
boto3 call the test-by-hand version makes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

moto = pytest.importorskip("moto")
from fastapi.testclient import TestClient  # noqa: E402

from splitsmith import match_model  # noqa: E402
from splitsmith.match_project import MatchProject, StageEntry, StageVideo  # noqa: E402
from splitsmith.storage import S3Storage  # noqa: E402
from splitsmith.sync.client import HostedSyncClient  # noqa: E402
from splitsmith.sync.push import run_push  # noqa: E402
from splitsmith.sync.run import run_sync  # noqa: E402
from splitsmith.sync.state import load_sync_state  # noqa: E402

from .hosted_helpers import _CapturingSender, login, moto_s3_storage  # noqa: E402

pytestmark = pytest.mark.integration

BUCKET = "splitsmith-sync-integration-test"
EMAIL = "sync-integration@example.com"
SLUG = "alice"


def _build_local_match(tmp_path: Path) -> tuple[Path, str, str]:
    """One match, one shooter, one stage with a registered primary video
    whose trimmed clip is already on disk. Returns ``(match_root,
    video_path, trimmed_name)`` - ``video_path`` is the project-relative path the
    stream endpoint's ``?path=`` query needs to resolve the same
    registered ``StageVideo`` (and therefore the same ``video_id``-keyed
    trim key) on the hosted side; ``trimmed_name`` is the filename
    pushed to R2.
    """
    root = tmp_path / "match"
    match = match_model.Match.init(root, name="Integration Match")
    match.stages = [match_model.MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(root)

    shooter = match_model.Shooter(slug=SLUG, name="Alice")
    match.add_shooter(root, shooter)
    shooter_root = match_model.Match.shooter_root(root, SLUG)

    video_path = "raw/stage1_cam1.mp4"
    video = StageVideo(path=Path(video_path), role="primary", stage_number=1)

    project = MatchProject.init(shooter_root, name="Integration Match")
    project.stages = [StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0, videos=[video])]
    project.save(shooter_root)

    trimmed_dir = shooter_root / "trimmed"
    trimmed_dir.mkdir(exist_ok=True)
    trimmed_name = f"stage1_cam_{video.video_id}_trimmed.mp4"
    (trimmed_dir / trimmed_name).write_bytes(b"not a real mp4 - see module docstring " * 64)

    audit_dir = shooter_root / "audit"
    audit_dir.mkdir(exist_ok=True)
    (audit_dir / "stage1.json").write_text(
        json.dumps({"detection": "ensemble", "shots": []}), encoding="utf-8"
    )

    return root, video_path, trimmed_name


def _media_handler(storage: S3Storage):
    """``httpx.MockTransport`` handler standing in for the presigned-part
    PUT endpoint: parses the (real, server-minted) presigned URL for its
    ``uploadId`` / ``partNumber`` and reconstructs the S3 key from the
    URL path, then calls the same ``storage._client.upload_part(...)``
    boto3 method ``test_sync_media_api.py`` calls by hand. See module
    docstring for why a genuine network PUT wouldn't reach moto here.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        qs = parse_qs(request.url.query.decode())
        upload_id = qs["uploadId"][0]
        part_number = int(qs["partNumber"][0])
        bucket = storage.bucket
        path = request.url.path.lstrip("/")
        # Virtual-hosted-style (bucket.s3.amazonaws.com/<key>) is what
        # boto3 mints for a bucket name with no dots; handle path-style
        # (s3.amazonaws.com/<bucket>/<key>) too so this doesn't silently
        # break if that ever changes.
        if request.url.host.startswith(f"{bucket}."):
            key = path
        else:
            key = path[len(bucket) + 1 :]
        out = storage._client.upload_part(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=request.content,
        )
        return httpx.Response(200, headers={"ETag": out["ETag"]})

    return handler


@pytest.fixture
def hosted_app_with_storage(
    hosted_env: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _CapturingSender, dict]]:
    """``hosted_app`` extended with a moto-backed ``S3Storage`` - same
    fixture shape as ``test_sync_media_api.py``'s ``hosted_app_with_storage``."""
    from splitsmith.ui.server import create_app

    with moto_s3_storage(monkeypatch, BUCKET) as captured:
        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender, captured


def test_desktop_push_then_anonymous_share_stream_round_trip(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
    tmp_path: Path,
) -> None:
    client, sender, captured = hosted_app_with_storage
    match_root, video_path, trimmed_name = _build_local_match(tmp_path)

    login(client, sender, EMAIL)
    # Trigger tenant resolution so captured["storage"] is populated - every
    # authenticated request rebuilds an equivalent S3Storage (see
    # moto_s3_storage's docstring).
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]

    token_resp = client.post("/api/me/desktop-tokens", json={"name": "integration box"})
    assert token_resp.status_code == 201, token_resp.text
    raw_token = token_resp.json()["token"]

    # The push http client: bearer-authed, no session cookie - the actual
    # desktop-client shape (DesktopTokenAuth is the pre-tenant path a
    # cookie-carrying client would never exercise).
    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    media_http = httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    sync_client = HostedSyncClient(http=sync_http, media_http=media_http)

    report = run_push(match_root, client=sync_client)
    assert report.uploaded == 1  # the trimmed clip; no .params.json sidecar was written
    assert report.docs == 3  # match + one project (alice) + one audit (stage 1)

    match_id = match_model.Match.load(match_root).match_id
    assert match_id is not None

    # As the owner (still-live session on `client`): mint a share token.
    share_resp = client.post(f"/api/matches/{match_id}/match/shares")
    assert share_resp.status_code == 201, share_resp.text
    token = share_resp.json()["url"].rsplit("/share/", 1)[1]

    # Anonymous from here on: drop the session cookie entirely.
    client.cookies.clear()

    shooters_resp = client.get(f"/api/share/{token}/match/shooters")
    assert shooters_resp.status_code == 200, shooters_resp.text
    slugs = [entry["slug"] for entry in shooters_resp.json()["shooters"]]
    assert slugs == [SLUG]

    stream_resp = client.get(
        f"/api/share/{token}/shooters/{SLUG}/videos/stream",
        params={"path": video_path, "kind": "trim"},
    )
    assert stream_resp.status_code == 307, stream_resp.text
    location = stream_resp.headers["location"]
    assert f"matches/{match_id}/shooters/{SLUG}/trimmed/{trimmed_name}" in location, location

    # A second push with nothing touched on disk uploads 0 media
    # (rsync-style size+mtime skip via sync_state.json) and, since #797,
    # PUTs 0 docs too (content-hash skip).
    report2 = run_push(match_root, client=sync_client)
    assert report2.uploaded == 0
    assert report2.docs == 0
    assert report2.docs_skipped == 3


def test_pull_materializes_metadata_only_audit_doc_with_no_local_file(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
    tmp_path: Path,
) -> None:
    """A phone flags a stage desktop never audited - the hosted doc holds
    only ``needs_attention`` (plus the beep-confirm stub keys), no
    ``shots``/``audit_events``, and no local audit file exists yet.
    Desktop's next pull must materialize that doc locally instead of
    silently skipping it: the desktop-owned-membership skip exists
    because merging into ``{}`` could let historical audit_events
    synthesize a fabricated "audited" doc, and a metadata-only doc has no
    audit_events for that synthesis to draw on. The flag must also
    survive the following push (recorded doc hash - no redundant
    overwrite clobbers it).
    """
    client, sender, captured = hosted_app_with_storage
    match_root, video_path, trimmed_name = _build_local_match(tmp_path)

    # No local audit file for stage 1 - the scenario under test.
    audit_path = match_root / "shooters" / SLUG / "audit" / "stage1.json"
    audit_path.unlink()

    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]

    token_resp = client.post("/api/me/desktop-tokens", json={"name": "integration box"})
    assert token_resp.status_code == 201, token_resp.text
    raw_token = token_resp.json()["token"]

    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    media_http = httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    sync_client = HostedSyncClient(http=sync_http, media_http=media_http)

    # Desktop pushes match + project only - no local audit doc to push.
    push_report = run_push(match_root, client=sync_client)
    assert push_report.docs == 2

    match_id = match_model.Match.load(match_root).match_id
    assert match_id is not None

    # Phone flags the doc-less stage through the real triage endpoint, as
    # the logged-in owner session (Finding 1's fixed path: the doc is the
    # beep-confirm stub shape plus the flag, not bare {}).
    flag_resp = client.post(
        f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/attention",
        json={"flagged": True, "note": "check this one"},
    )
    assert flag_resp.status_code == 200, flag_resp.text

    # Desktop syncs: pull must materialize the metadata-only doc locally.
    report = run_sync(match_root, client=sync_client)
    assert report.pulled == 1

    assert audit_path.exists()
    local_doc = json.loads(audit_path.read_text(encoding="utf-8"))
    assert local_doc["needs_attention"]["flagged"] is True
    assert local_doc["needs_attention"]["note"] == "check this one"

    state = load_sync_state(match_root)
    audit_key = next(k for k in state.doc_versions if k.startswith(f"audit/{SLUG}/"))
    assert audit_key in state.doc_hashes  # recorded, so the flag isn't re-pulled/re-pushed forever

    # A follow-up sync is a no-op: the doc hash recorded above means the
    # flag is not clobbered by a redundant re-push.
    report2 = run_sync(match_root, client=sync_client)
    assert report2.pulled == 0
    assert report2.docs == 0

    server_doc = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/audit").json()
    assert server_doc["needs_attention"]["flagged"] is True


def test_phone_beep_confirm_reaches_the_desktop_reconciler(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
    tmp_path: Path,
) -> None:
    """Desktop auto-sync end to end (spec 2026-09-27), minus ffmpeg: a beep
    confirmed on the hosted mirror moves the fingerprint, the desktop
    service notices on its next poll, the auto_sync job pulls the confirm,
    and the reconciler asks for the trim that hosted never cuts for a
    mirror. Both halves run for real: the hosted routes over TestClient,
    the desktop's service, core, job registry, run_sync and reconciler.

    Local and hosted mode cannot share a process (mode is env-driven), so
    the desktop job body here is run_sync + the reconciler directly rather
    than the local server's ``_run_sync_match``; that glue has its own
    tests in ``test_sync_reconcile_server.py``.
    """
    import asyncio
    import threading

    from splitsmith.sync.reconcile import load_reconcile_inputs, plan_reconcile
    from splitsmith.ui.auto_sync import AutoSyncService
    from splitsmith.ui.jobs import JobRegistry
    from splitsmith.user_config import GlobalPrefs

    client, sender, captured = hosted_app_with_storage
    match_root, _, _ = _build_local_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(match_root, SLUG)
    project = MatchProject.load(shooter_root)
    video = project.stages[0].videos[0]
    video.beep_time = 3.0
    video.beep_reviewed = False
    video.processed = {"beep": True, "trim": False, "shot_detect": False}
    project.save(shooter_root)
    for trimmed in (shooter_root / "trimmed").iterdir():
        trimmed.unlink()  # a stage the desktop has not trimmed yet

    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    raw_token = client.post("/api/me/desktop-tokens", json={"name": "auto box"}).json()["token"]
    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    sync_client = HostedSyncClient(
        http=sync_http, media_http=httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    )
    run_sync(match_root, client=sync_client)
    match_id = match_model.Match.load(match_root).match_id

    # The phone confirms the detected beep as-is (beep_time unchanged).
    resp = client.post(
        f"/api/matches/{match_id}/match/beep-queue/confirm",
        json={"slug": SLUG, "stage_number": 1, "video_id": video.video_id},
    )
    assert resp.status_code == 200, resp.text

    steps: list = []
    ran = threading.Event()

    def auto_sync_body(handle) -> None:
        run_sync(match_root, client=sync_client)
        projects, audits = load_reconcile_inputs(match_root)
        steps.extend(plan_reconcile(projects, audits, {}))
        ran.set()

    jobs = JobRegistry(max_concurrent=1)
    jobs.bodies.register("auto_sync", auto_sync_body)

    class _Matches:
        def refresh_from_recent_projects(self) -> int:
            return 1

        def known_ids(self) -> list[str]:
            return [match_id]

        def resolve(self, mid: str) -> Path:
            return match_root

    async def submit(mid: str, root: Path) -> None:
        await jobs.submit(kind="auto_sync")

    service = AutoSyncService(
        jobs=jobs,
        matches=_Matches(),
        submit_auto_sync=submit,
        load_prefs=lambda: GlobalPrefs(hosted_base_url="http://testserver", hosted_token=raw_token),
        fetch_fingerprints=lambda prefs: sync_client.get_fingerprints(),
        clock=lambda: 1000.0,
    )
    service._started = True  # no startup pull: the poll alone must find the change
    asyncio.run(service.tick())
    assert ran.wait(timeout=10.0), "the poll did not trigger an auto_sync"

    pulled = MatchProject.load(shooter_root).stages[0].videos[0]
    assert pulled.beep_reviewed is True
    assert [(s.kind, s.slug, s.stage_number, s.video_id) for s in steps] == [
        ("trim", SLUG, 1, video.video_id)
    ]


# --- web-only mirrors (v1.1): hosted plays and waveforms the rendition --------


def _web_only_mirror(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path, *, pre_buffer: float
) -> tuple[TestClient, str, str, str, S3Storage]:
    """Push a match whose only media on hosted is the rendition + params.
    Returns ``(client, match_id, video_path, web_name, storage)``."""
    from tests.synthetic_media import build_synthetic_video, ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    client, sender, captured = hosted_app_with_storage
    match_root, video_path, trimmed_name = _build_local_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(match_root, SLUG)
    project = MatchProject.load(shooter_root)
    project.stages[0].videos[0].beep_time = 12.0
    project.save(shooter_root)
    trimmed = shooter_root / "trimmed" / trimmed_name
    web = trimmed.with_name(trimmed_name.replace("_trimmed.mp4", "_web.mp4"))
    build_synthetic_video(web)
    trimmed.with_name(f"{trimmed.stem}.params.json").write_text(
        json.dumps(
            {
                "beep_time": 12.0,
                "stage_time_seconds": 12.0,
                "pre_buffer_seconds": pre_buffer,
                "post_buffer_seconds": 1.0,
            }
        ),
        encoding="utf-8",
    )
    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    raw_token = client.post("/api/me/desktop-tokens", json={"name": "web box"}).json()["token"]
    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    sync_client = HostedSyncClient(
        http=sync_http, media_http=httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    )
    run_push(match_root, client=sync_client, full_media=False)
    match_id = match_model.Match.load(match_root).match_id
    keys = [o.path for o in storage.list(f"matches/{match_id}/")]
    assert any(k.endswith("_web.mp4") for k in keys)
    assert not any(k.endswith("_trimmed.mp4") for k in keys)
    return client, match_id, video_path, web.name, storage


def test_web_only_mirror_streams_the_rendition_for_trim_and_auto(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    client, match_id, video_path, web_name, _ = _web_only_mirror(
        hosted_app_with_storage, tmp_path, pre_buffer=2.0
    )
    for kind in ("trim", "auto", "web"):
        resp = client.get(
            f"/api/matches/{match_id}/shooters/{SLUG}/videos/stream",
            params={"path": video_path, "kind": kind},
        )
        assert resp.status_code == 307, (kind, resp.text)
        assert web_name in resp.headers["location"], kind


def test_web_only_mirror_anchor_comes_from_the_pushed_params(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    """pre_buffer 2.0 differs from the project default, so a default-based
    anchor would land every marker off by the difference."""
    client, match_id, _, _, _ = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    coach = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/coach")
    assert coach.status_code == 200, coach.text
    (entry,) = [v for v in coach.json()["videos"] if v.get("role") == "primary"]
    assert entry["kind"] == "web"
    assert entry["beep_in_clip"] == 2.0


def test_web_only_mirror_serves_stage_peaks(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    client, match_id, _, _, _ = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    peaks = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/peaks")
    assert peaks.status_code == 200, peaks.text
    body = peaks.json()
    assert body is not None and body["trimmed"] is True
    assert body["beep_time"] == 2.0


def test_hosted_native_match_without_a_trim_still_404s_kind_trim(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    """The rendition fallback is for mirrors only. A native match's audit
    scrubbing needs the real GOP, so kind=trim never substitutes there,
    even with a rendition sitting in storage."""
    import asyncio

    from sqlalchemy import select as _select

    from splitsmith.db import ProjectStateStore, create_engine, sessionmaker
    from splitsmith.db.models import User

    from .hosted_helpers import seed_match
    from .test_sync_api import _db_url_for

    client, sender, captured = hosted_app_with_storage
    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    match_id = "01JNATIVEWEBONLYGUARD0001"
    db_url = _db_url_for(client)
    seed_match(db_url, EMAIL, match_id)
    video = StageVideo(path=Path("raw/stage1_cam1.mp4"), role="primary", stage_number=1, beep_time=12.0)
    engine = create_engine(db_url)
    sf = sessionmaker(engine)

    async def _seed() -> None:
        async with sf() as s:
            user_id = (await s.execute(_select(User).where(User.email == EMAIL))).scalar_one().id
        store = ProjectStateStore(sf, user_id=user_id)
        match = match_model.Match(match_id=match_id, name="Native", shooters=[SLUG], stages=[])
        await store.save_match(match_id, match.model_dump(mode="json"), expected_version=0)
        stage = StageEntry(stage_number=1, stage_name="Stage 1", time_seconds=12.0, videos=[video])
        project = MatchProject(name="Native", competitor_name="Alice", stages=[stage])
        await store.save_project(match_id, SLUG, project.model_dump(mode="json"), expected_version=0)

    asyncio.run(_seed())
    project = MatchProject(
        name="Native", stages=[StageEntry(stage_number=1, stage_name="S", time_seconds=12.0, videos=[video])]
    )
    video_id = project.stages[0].videos[0].video_id
    storage.write_bytes(f"matches/{match_id}/shooters/{SLUG}/trimmed/stage1_cam_{video_id}_web.mp4", b"w")

    resp = client.get(
        f"/api/matches/{match_id}/shooters/{SLUG}/videos/stream",
        params={"path": "raw/stage1_cam1.mp4", "kind": "trim"},
    )
    assert resp.status_code == 404, resp.text


def test_web_only_mirror_anchor_does_not_download_the_rendition(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    """The anchor needs the ~200-byte params sidecar, not the rendition."""
    client, match_id, _, _, storage = _web_only_mirror(hosted_app_with_storage, tmp_path, pre_buffer=2.0)
    opened: list[str] = []
    real_open = type(storage).open_stream

    def spy(self, path, *a, **kw):
        opened.append(path)
        return real_open(self, path, *a, **kw)

    import unittest.mock

    with unittest.mock.patch.object(type(storage), "open_stream", spy):
        coach = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/coach")
    assert coach.status_code == 200, coach.text
    assert not any(p.endswith("_web.mp4") for p in opened), opened


def test_web_only_mirror_follows_a_re_push_with_new_params(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path
) -> None:
    """The desktop re-trims around a moved beep and pushes a new rendition +
    params. Hosted must not keep serving the old anchor or waveform window."""
    client, match_id, _, web_name, storage = _web_only_mirror(
        hosted_app_with_storage, tmp_path, pre_buffer=2.0
    )
    base = f"/api/matches/{match_id}/shooters/{SLUG}/stages/1"
    assert client.get(f"{base}/coach").json()["videos"][0]["beep_in_clip"] == 2.0
    assert client.get(f"{base}/peaks").json()["beep_time"] == 2.0

    # What the next push lands on R2 after a re-trim with pre_buffer 3.0.
    web_key = next(o.path for o in storage.list(f"matches/{match_id}/") if o.path.endswith(web_name))
    params_key = web_key.replace("_web.mp4", "_trimmed.params.json")
    params = json.loads(storage.read_bytes(params_key))
    params["pre_buffer_seconds"] = 3.0
    storage.write_bytes(params_key, json.dumps(params).encode())
    storage.write_bytes(web_key, storage.read_bytes(web_key))  # re-uploaded rendition

    assert client.get(f"{base}/coach").json()["videos"][0]["beep_in_clip"] == 3.0
    assert client.get(f"{base}/peaks").json()["beep_time"] == 3.0


def test_web_only_mirror_on_storage_without_presigned_get_serves_the_rendition(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], tmp_path: Path, monkeypatch
) -> None:
    """#1078: on a storage that cannot presign (FilesystemStorage in dev and
    tests) the anchor says "trim" from the pushed params, and the stream
    took the local-bytes branch, which 404ed kind=trim with no full trim."""
    client, match_id, video_path, _, storage = _web_only_mirror(
        hosted_app_with_storage, tmp_path, pre_buffer=2.0
    )
    monkeypatch.setattr(type(storage), "supports_presigned_get", property(lambda self: False))
    coach = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/coach")
    (entry,) = [v for v in coach.json()["videos"] if v.get("role") == "primary"]
    assert (entry["kind"], entry["beep_in_clip"]) == ("trim", 2.0)
    web_key = next(o.path for o in storage.list(f"matches/{match_id}/") if o.path.endswith("_web.mp4"))
    for kind in ("trim", "auto", "web"):
        resp = client.get(
            f"/api/matches/{match_id}/shooters/{SLUG}/videos/stream",
            params={"path": video_path, "kind": kind},
        )
        assert resp.status_code == 200, (kind, resp.text)
        assert resp.content == storage.read_bytes(web_key), kind


def test_a_phone_request_runs_on_the_desktop_and_its_result_reaches_hosted(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
    tmp_path: Path,
) -> None:
    """Desktop command queue end to end (#1100, spec 2026-09-28), minus the
    detector: the phone asks for a re-detect on a desktop-synced stage, the
    desktop's poll sees it, pulls, claims, runs the job, pushes the result
    at once, and only then reports the command done. Both halves are real:
    hosted routes over TestClient; the desktop's service, core, runner,
    job registry and run_sync. The ``shot_detect`` body is a stand-in that
    writes the shots a detection would (the ensemble has its own tests)."""
    import asyncio
    import threading
    import time

    from splitsmith.ui.auto_sync import AutoSyncService
    from splitsmith.ui.jobs import JobRegistry
    from splitsmith.ui.server import current_match_id, current_match_root
    from splitsmith.user_config import GlobalPrefs

    client, sender, captured = hosted_app_with_storage
    match_root, _, _ = _build_local_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(match_root, SLUG)
    project = MatchProject.load(shooter_root)
    project.stages[0].videos[0].beep_time = 3.0
    project.save(shooter_root)

    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    raw_token = client.post("/api/me/desktop-tokens", json={"name": "queue box"}).json()["token"]
    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    sync_client = HostedSyncClient(
        http=sync_http, media_http=httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    )
    run_sync(match_root, client=sync_client)
    match_id = match_model.Match.load(match_root).match_id

    # The phone asks.
    asked = client.post(
        f"/api/matches/{match_id}/match/desktop-commands",
        json={"kind": "shot_detect", "slug": SLUG, "stage_number": 1},
    )
    assert asked.status_code == 201, asked.text
    command_id = asked.json()["id"]

    detected_shots = [{"id": "s-new-1", "shot_number": 1, "time": 4.2, "candidate_number": 1}]
    ran: list[dict] = []

    def auto_sync_body(handle) -> None:
        run_sync(match_root, client=sync_client)

    def shot_detect_body(handle, slug: str, stage_number: int, reset: bool = False) -> None:
        ran.append({"slug": slug, "stage_number": stage_number, "reset": reset})
        path = shooter_root / "audit" / f"stage{stage_number}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["shots"] = detected_shots
        path.write_text(json.dumps(doc), encoding="utf-8")

    jobs = JobRegistry(max_concurrent=2)
    jobs.bodies.register("auto_sync", auto_sync_body)
    jobs.bodies.register("shot_detect", shot_detect_body)

    class _Matches:
        def refresh_from_recent_projects(self) -> int:
            return 1

        def known_ids(self) -> list[str]:
            return [match_id]

        def resolve(self, mid: str) -> Path:
            return match_root

    async def in_match(fn):
        id_token = current_match_id.set(match_id)
        root_token = current_match_root.set(match_root)
        try:
            return await fn()
        finally:
            current_match_root.reset(root_token)
            current_match_id.reset(id_token)

    async def submit(mid: str, root: Path) -> None:
        await in_match(lambda: jobs.submit(kind="auto_sync"))

    async def start(mid: str, root: Path, command: dict):
        job = await in_match(
            lambda: jobs.submit(
                kind="shot_detect",
                stage_number=command["stage_number"],
                shooter_slug=command["slug"],
                args={"slug": command["slug"], "stage_number": command["stage_number"], "reset": True},
            )
        )
        return job.id, None

    class _Api:
        def claim(self, match_ids):
            return sync_client.claim_commands(match_ids)

        def heartbeat(self, command_id, message):
            return sync_client.heartbeat_command(command_id, message)

        def complete(self, command_id, *, status, error=None, result=None):
            sync_client.complete_command(command_id, status=status, error=error, result=result)

    clock = {"t": time.time()}
    service = AutoSyncService(
        jobs=jobs,
        matches=_Matches(),
        submit_auto_sync=submit,
        load_prefs=lambda: GlobalPrefs(hosted_base_url="http://testserver", hosted_token=raw_token),
        fetch_fingerprints=lambda prefs: sync_client.get_poll(),
        clock=lambda: clock["t"],
        start_command=start,
        command_api=lambda prefs: _Api(),
    )
    jobs.add_terminal_listener(service.on_job_terminal)
    service._started = True  # no startup pull: the poll alone must find the request

    def status() -> str:
        rows = client.get(f"/api/matches/{match_id}/match/desktop-commands").json()["commands"]
        return next(c for c in rows if c["id"] == command_id)["status"]

    # Two full syncs through the mocked storage: a couple of seconds here,
    # far more on a loaded CI runner. The loop exits as soon as it is done.
    deadline = time.monotonic() + 90.0
    while status() not in ("succeeded", "failed") and time.monotonic() < deadline:
        clock["t"] = time.time()
        service.core.next_poll_at = 0.0
        asyncio.run(service.tick())
        threading.Event().wait(0.05)

    final = client.get(f"/api/matches/{match_id}/match/desktop-commands").json()["commands"][0]
    diagnostics = {
        "command": final,
        "jobs": [
            (j.kind, j.status.value, j.started_at, j.finished_at, j.error) for j in asyncio.run(jobs.list())
        ],
        "core": service.core._matches.get(match_id),
        "poll_failures": service.core._poll_failures,
        "tracked": service.commands._tracked if service.commands else None,
    }
    assert final["status"] == "succeeded", diagnostics
    assert ran == [{"slug": SLUG, "stage_number": 1, "reset": True}]
    # A sync started after the detection ended: that is what carried it.
    all_jobs = asyncio.run(jobs.list())
    (detect,) = [j for j in all_jobs if j.kind == "shot_detect"]
    assert any(j.kind == "auto_sync" and j.started_at >= detect.finished_at for j in all_jobs)
    hosted_audit = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/stages/1/audit").json()
    assert [s["id"] for s in hosted_audit["shots"]] == ["s-new-1"]


def test_mirror_default_camera_reaches_the_desktop_and_survives_its_push(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
    tmp_path: Path,
) -> None:
    """The share dialog sets a shooter's default camera on the hosted copy
    of a desktop match: the route must be open to the mirror (REVIEW), the
    desktop's pull must take the value (``compare_camera`` is a merge unit
    of its own), and the desktop's next push must not overwrite it."""
    client, sender, captured = hosted_app_with_storage
    match_root, _video_path, _trimmed = _build_local_match(tmp_path)
    shooter_root = match_model.Match.shooter_root(match_root, SLUG)
    assert MatchProject.load(shooter_root).compare_camera is None

    login(client, sender, EMAIL)
    client.get("/api/me/recent-projects")
    storage: S3Storage = captured["storage"]
    raw_token = client.post("/api/me/desktop-tokens", json={"name": "camera box"}).json()["token"]
    sync_http = TestClient(
        client.app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {raw_token}"},
        follow_redirects=False,
    )
    sync_client = HostedSyncClient(
        http=sync_http, media_http=httpx.Client(transport=httpx.MockTransport(_media_handler(storage)))
    )
    run_sync(match_root, client=sync_client)
    match_id = match_model.Match.load(match_root).match_id

    resp = client.patch(f"/api/matches/{match_id}/shooters/{SLUG}/compare-camera", json={"camera": "primary"})
    assert resp.status_code == 200, resp.text

    report = run_sync(match_root, client=sync_client)
    assert report.pulled == 1
    assert MatchProject.load(shooter_root).compare_camera == "primary"

    run_sync(match_root, client=sync_client)
    server_project = client.get(f"/api/matches/{match_id}/shooters/{SLUG}/project").json()
    assert server_project["compare_camera"] == "primary"
    assert MatchProject.load(shooter_root).compare_camera == "primary"
