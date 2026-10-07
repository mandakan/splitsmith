"""Shooter identity on Compare and the share views (issue #1249 b): the logo
read route (local, hosted, and through a share link), and identity on the
compare payload."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.hosted_helpers import _CapturingSender, login, moto_s3_storage, seed_match

from .test_compare_stage_endpoint import _bootstrap, _legacy_shots
from .test_share_routes import MID, SLUG, _create_share_token, _seed_state_docs, _share_url


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), (220, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_the_logo_route_serves_the_uploaded_logo(tmp_path: Path) -> None:
    client, _audit, compare_url, _root = _bootstrap(tmp_path, _legacy_shots())
    base = compare_url.split("/match/stage/")[0]
    slug = client.get(compare_url).json()["shooters"][0]["slug"]
    assert client.get(f"{base}/shooters/{slug}/identity/logo").status_code == 404
    up = client.post(f"{base}/shooters/{slug}/identity/logo", files={"file": ("x.png", _png(), "image/png")})
    assert up.status_code == 200, up.text
    r = client.get(f"{base}/shooters/{slug}/identity/logo")
    assert r.status_code == 200 and r.content == _png()
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_compare_payload_carries_each_shooters_identity(tmp_path: Path) -> None:
    client, _audit, compare_url, _root = _bootstrap(tmp_path, _legacy_shots())
    base = compare_url.split("/match/stage/")[0]
    slug = client.get(compare_url).json()["shooters"][0]["slug"]
    client.patch(f"{base}/shooters/{slug}/identity", json={"accent": "#22aaee", "club": "Bromma PK"})
    shooter = client.get(compare_url).json()["shooters"][0]
    assert shooter["identity"]["accent"] == "#22aaee" and shooter["identity"]["club"] == "Bromma PK"


def test_a_share_link_reads_its_shooters_logo_and_nothing_else(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, sender = hosted_app
    with moto_s3_storage(monkeypatch, "identity-views-bucket"):
        login(client, sender, "owner@example.com")
        seed_match(hosted_env, "owner@example.com", MID)
        _seed_state_docs(hosted_env, "owner@example.com", MID, SLUG)
        up = client.post(
            f"/api/matches/{MID}/shooters/{SLUG}/identity/logo",
            files={"file": ("x.png", _png(), "image/png")},
        )
        assert up.status_code == 200, up.text
        token = _create_share_token(client, MID)
        client.cookies.clear()

        r = client.get(_share_url(token, f"shooters/{SLUG}/identity/logo"))
        assert r.status_code == 200, r.text
        assert r.content == _png() and r.headers["x-content-type-options"] == "nosniff"
        assert client.get(_share_url(token, "shooters/nobody/identity/logo")).status_code == 404
        assert client.delete(_share_url(token, f"shooters/{SLUG}/identity/logo")).status_code == 404
