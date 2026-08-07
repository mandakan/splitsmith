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
from splitsmith.storage import S3Storage  # noqa: E402
from splitsmith.sync.client import HostedSyncClient  # noqa: E402
from splitsmith.sync.push import run_push  # noqa: E402
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo  # noqa: E402

from .hosted_helpers import _CapturingSender, login, moto_s3_storage  # noqa: E402

pytestmark = pytest.mark.integration

BUCKET = "splitsmith-sync-integration-test"
EMAIL = "sync-integration@example.com"
SLUG = "alice"


def _build_local_match(tmp_path: Path) -> tuple[Path, str]:
    """One match, one shooter, one stage with a registered primary video
    whose trimmed clip is already on disk. Returns ``(match_root,
    video_path)`` - ``video_path`` is the project-relative path the
    stream endpoint's ``?path=`` query needs to resolve the same
    registered ``StageVideo`` (and therefore the same ``video_id``-keyed
    trim key) on the hosted side.
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

    return root, video_path


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
    match_root, video_path = _build_local_match(tmp_path)

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
        base_url="http://testserver/api/sync",
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
    assert f"matches/{match_id}/shooters/{SLUG}/trimmed/" in location, location

    # A second push with nothing touched on disk uploads 0 media
    # (rsync-style size+mtime skip via sync_state.json).
    report2 = run_push(match_root, client=sync_client)
    assert report2.uploaded == 0
    assert report2.docs == 3
