"""HTTP-surface tests for the ``/api/sync/matches/{match_id}/media/*``
presign endpoints (Task 5 of the desktop-to-hosted sync MVP, #631).

A desktop client pushes trimmed clip / audit media direct to object
storage: it asks this router to mint a presigned multipart upload, PUTs
each part straight to storage via the returned URLs, then completes (or
aborts) the upload. No media bytes ever pass through this process - these
tests drive the FastAPI app against a moto-backed ``S3Storage`` so the
round-trip exercises the same boto3 codepath production hits.

Key containment (``_SYNC_MEDIA_KEY_RE``) is the security boundary here: a
mirror push for match A must never be able to plant an object outside its
own ``matches/{match_id}/shooters/*/trimmed/`` prefix, let alone escape
the tenant's bucket entirely.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

moto = pytest.importorskip("moto")
from fastapi.testclient import TestClient  # noqa: E402

from splitsmith.storage import S3Storage  # noqa: E402

from .hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match  # noqa: E402

BUCKET = "splitsmith-sync-media-test"
MATCH_ID = "sync-media-match"
SLUG = "me"
EMAIL = "sync-media@example.com"

VALID_KEY = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123_trimmed.mp4"

CREATE_URL = f"/api/sync/matches/{MATCH_ID}/media/create"
PART_URL_URL = f"/api/sync/matches/{MATCH_ID}/media/part-url"
COMPLETE_URL = f"/api/sync/matches/{MATCH_ID}/media/complete"
ABORT_URL = f"/api/sync/matches/{MATCH_ID}/media/abort"


@pytest.fixture
def hosted_app_with_storage(
    hosted_env: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, _CapturingSender, dict]]:
    """``hosted_app`` (magic-link login) extended with a moto-backed
    ``S3Storage`` so the media routes have somewhere to presign against.

    ``captured["storage"]`` is populated after the first authenticated
    request (every request rebuilds an equivalent ``S3Storage`` against
    the same bucket/prefix/client, since ``_build_tenant`` runs on every
    request regardless of whether the route touches storage).
    """
    from splitsmith.ui.server import create_app

    with moto_s3_storage(monkeypatch, BUCKET) as captured:
        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        with TestClient(app, follow_redirects=False) as client:
            yield client, sender, captured


def _db_url_for(client: TestClient) -> str:
    import os

    return os.environ["SPLITSMITH_DATABASE_URL"]


def _login_and_adopt(client: TestClient, sender: _CapturingSender, captured: dict) -> S3Storage:
    """Log in, adopt MATCH_ID as a desktop mirror, and return the
    resolved S3Storage (populated by the login/adopt requests)."""
    login(client, sender, EMAIL)
    resp = client.post("/api/sync/matches", json={"match_id": MATCH_ID, "name": "Sync Media Match"})
    assert resp.status_code == 200, resp.text
    return captured["storage"]


# --- happy path: create -> part-url -> complete round-trip -----------------


def test_create_part_url_complete_round_trip(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    storage = _login_and_adopt(client, sender, captured)

    create = client.post(CREATE_URL, json={"key": VALID_KEY})
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["key"] == VALID_KEY
    assert body["upload_id"]
    from splitsmith.ui.server import _RAW_UPLOAD_PART_SIZE

    assert body["part_size"] == _RAW_UPLOAD_PART_SIZE
    upload_id = body["upload_id"]

    part_url_resp = client.post(
        PART_URL_URL, json={"key": VALID_KEY, "upload_id": upload_id, "part_number": 1}
    )
    assert part_url_resp.status_code == 200, part_url_resp.text
    assert part_url_resp.json()["url"].startswith("http")

    # Upload straight to the (moto) backend the way a desktop client would
    # PUT to the presigned URL - use the backend client + prefixed key.
    full_key = f"{storage.prefix}{VALID_KEY}"
    payload = b"x" * (5 * 1024 * 1024)
    out = storage._client.upload_part(
        Bucket=storage.bucket, Key=full_key, UploadId=upload_id, PartNumber=1, Body=payload
    )
    etag = out["ETag"]

    complete = client.post(
        COMPLETE_URL,
        json={"key": VALID_KEY, "upload_id": upload_id, "parts": [{"part_number": 1, "etag": etag}]},
    )
    assert complete.status_code == 200, complete.text
    assert complete.json() == {"size": len(payload)}

    # The double holds the object under the exact key.
    assert storage.exists(VALID_KEY)
    assert storage.read_bytes(VALID_KEY) == payload


def test_abort_discards_upload(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    storage = _login_and_adopt(client, sender, captured)

    create = client.post(CREATE_URL, json={"key": VALID_KEY}).json()
    resp = client.post(ABORT_URL, json={"key": VALID_KEY, "upload_id": create["upload_id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}
    assert not storage.exists(VALID_KEY)


# --- key containment ---------------------------------------------------


def test_traversal_key_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    resp = client.post(CREATE_URL, json={"key": "../../users/other/x.mp4"})
    assert resp.status_code == 422, resp.text


def test_key_with_mismatched_embedded_match_id_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    other_key = f"matches/some-other-match/shooters/{SLUG}/trimmed/clip.mp4"
    resp = client.post(CREATE_URL, json={"key": other_key})
    assert resp.status_code == 422, resp.text


def test_wav_extension_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    wav_key = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123_audit.wav"
    resp = client.post(CREATE_URL, json={"key": wav_key})
    assert resp.status_code == 422, resp.text


def test_trimmed_params_json_key_accepted(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    key = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123_trimmed.params.json"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 200, resp.text


# --- beep_review media keys (slice 3, #631) ---------------------------------


def test_beep_review_m4a_key_accepted(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.m4a"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 200, resp.text


def test_beep_review_peaks_json_key_accepted(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.peaks.json"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 200, resp.text


def test_beep_review_foreign_subdir_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    # Not one of the two admitted subdirs (trimmed, beep_review) - the
    # escape-attempt control for this key family.
    bad_key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review_evil/vid123.m4a"
    resp = client.post(CREATE_URL, json={"key": bad_key})
    assert resp.status_code == 422, resp.text


def test_trimmed_m4a_cross_product_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    """#821: the extension set is per-subdir. trimmed/ never holds audio
    snippets; admitting the cross-product widens the write surface."""
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    key = f"matches/{MATCH_ID}/shooters/{SLUG}/trimmed/stage1_cam_abc123.m4a"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 422, resp.text


def test_beep_review_mp4_cross_product_rejected(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    """#821: beep_review/ holds .m4a snippets and .peaks.json only."""
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    key = f"matches/{MATCH_ID}/shooters/{SLUG}/beep_review/vid123.mp4"
    resp = client.post(CREATE_URL, json={"key": key})
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("route", [CREATE_URL, PART_URL_URL, COMPLETE_URL, ABORT_URL])
def test_key_containment_enforced_on_every_route(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict], route: str
) -> None:
    """Every media route validates the key, not just create."""
    client, sender, captured = hosted_app_with_storage
    _login_and_adopt(client, sender, captured)

    bad_key = "../../users/other/x.mp4"
    if route == CREATE_URL:
        body = {"key": bad_key}
    elif route == PART_URL_URL:
        body = {"key": bad_key, "upload_id": "whatever", "part_number": 1}
    elif route == COMPLETE_URL:
        body = {"key": bad_key, "upload_id": "whatever", "parts": [{"part_number": 1, "etag": "x"}]}
    else:
        body = {"key": bad_key, "upload_id": "whatever"}

    resp = client.post(route, json=body)
    assert resp.status_code == 422, resp.text


# --- mirror contract -----------------------------------------------------


def test_native_hosted_match_rejected_with_409(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    login(client, sender, EMAIL)
    # Drive one request so the tenant + storage resolve before seeding.
    client.get("/api/me/recent-projects")
    seed_match(_db_url_for(client), EMAIL, MATCH_ID)

    resp = client.post(CREATE_URL, json={"key": VALID_KEY})
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "not_a_mirror"}


def test_unknown_match_404(
    hosted_app_with_storage: tuple[TestClient, _CapturingSender, dict],
) -> None:
    client, sender, captured = hosted_app_with_storage
    login(client, sender, EMAIL)

    resp = client.post(CREATE_URL, json={"key": VALID_KEY})
    assert resp.status_code == 404, resp.text


# --- hosted-only surface ---------------------------------------------------


def test_local_mode_404() -> None:
    from splitsmith.ui.server import create_app

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        resp = client.post(CREATE_URL, json={"key": VALID_KEY})
    assert resp.status_code == 404


def test_503_when_storage_unwired(hosted_env: str) -> None:
    """A mirror exists and belongs to the caller, but storage isn't
    configured - the route must refuse cleanly rather than 500."""
    from splitsmith.ui.server import create_app

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, EMAIL)
        assert (
            client.post("/api/sync/matches", json={"match_id": MATCH_ID, "name": "No Storage"}).status_code
            == 200
        )

        resp = client.post(CREATE_URL, json={"key": VALID_KEY})
    assert resp.status_code == 503, resp.text
