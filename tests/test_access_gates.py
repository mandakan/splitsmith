"""Hosted route gates per account feature (spec 2026-10-03)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from splitsmith.db import User as UserRow
from splitsmith.db import create_engine, sessionmaker
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest


def set_tier(db_url: str, email: str, tier: str) -> None:
    async def go() -> None:
        engine = create_engine(db_url)
        async with sessionmaker(engine)() as s:
            await s.execute(update(UserRow).where(UserRow.email == email).values(access_tier=tier))
            await s.commit()
        await engine.dispose()

    asyncio.run(go())


GATED = [
    # (method, path, json/body kwargs, feature)
    ("post", "/api/match/create-manual", {"json": {"name": "x"}}, "create_match"),
    ("post", "/api/match/create-from-scoreboard", {"json": {}}, "create_match"),
    ("post", "/api/me/projects/import", {"data": {"dest_root": "x"}}, "create_match"),
    ("post", "/api/me/raw/upload", {"files": {"file": ("a.mp4", b"x")}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/create", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/part-url", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/complete", {"json": {}}, "raw_upload"),
    ("post", "/api/me/raw/upload/multipart/abort", {"json": {}}, "raw_upload"),
    ("post", "/api/me/jobs/nope/retry", {}, "hosted_compute"),
    ("get", "/api/sync/fingerprints", {}, "sync"),
]


@pytest.mark.parametrize(("method", "path", "kwargs", "feature"), GATED)
def test_sharing_tier_is_refused_with_the_feature_named(
    hosted_app, hosted_env, method, path, kwargs, feature  # noqa: ANN001
) -> None:
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing" if feature != "sync" else "disabled")
    resp = getattr(client, method)(path, **kwargs)
    if feature == "sync":
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "account_disabled"
        return
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"code": "feature_required", "feature": feature}


@pytest.mark.parametrize(("method", "path", "kwargs", "feature"), GATED)
def test_full_tier_passes_the_gate(hosted_app, method, path, kwargs, feature) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    resp = getattr(client, method)(path, **kwargs)
    # Whatever the handler does next (400, 404, 409, 503 for no storage),
    # it is not the access gate.
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    detail = body.get("detail")
    assert not (isinstance(detail, dict) and detail.get("code") in {"feature_required", "account_disabled"})


def test_share_creation_needs_share(hosted_app, hosted_env) -> None:  # noqa: ANN001
    from tests.hosted_helpers import seed_match

    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    seed_match(hosted_env, "friend@x.se", "m1")
    set_tier(hosted_env, "friend@x.se", "disabled")
    resp = client.post("/api/matches/m1/match/shares", json={"scope": "read"})
    assert resp.status_code == 403


def test_me_reports_tier_and_features(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    set_tier(hosted_env, "friend@x.se", "sharing")
    me = client.get("/api/me").json()
    assert me["access_tier"] == "sharing"
    assert sorted(me["features"]) == ["share", "sync"]


def test_disabled_account_keeps_me_and_logout_only(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "gone@x.se")
    set_tier(hosted_env, "gone@x.se", "disabled")
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/me/recent-projects").status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 200


def test_downgrade_takes_effect_on_the_next_request(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    assert "create_match" in client.get("/api/me").json()["features"]
    set_tier(hosted_env, "me@x.se", "sharing")
    assert "create_match" not in client.get("/api/me").json()["features"]


def test_new_account_gets_the_default_tier(hosted_env, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    cfg = tmp_path / "c.yaml"
    cfg.write_text("access:\n  default_tier: sharing\n")
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(cfg))
    from splitsmith.ui.server import create_app
    from tests.hosted_helpers import _CapturingSender

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "new@x.se")
        assert client.get("/api/me").json()["access_tier"] == "sharing"


def test_new_account_gets_the_env_default_tier(hosted_env, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "sharing")
    from splitsmith.ui.server import create_app
    from tests.hosted_helpers import _CapturingSender

    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "new@x.se")
        assert client.get("/api/me").json()["access_tier"] == "sharing"


def test_returning_account_keeps_its_tier_when_the_default_changes(
    hosted_env, monkeypatch  # noqa: ANN001
) -> None:
    """``default_tier`` applies at account creation only, never on a later sign-in."""
    from splitsmith.ui.server import create_app
    from tests.hosted_helpers import _CapturingSender

    def boot() -> tuple[object, _CapturingSender]:
        app = create_app()
        sender = _CapturingSender()
        app.state.splitsmith_state.auth.backends[0]._email = sender
        return app, sender

    monkeypatch.setenv("SPLITSMITH_ACCESS_DEFAULT_TIER", "sharing")
    app, sender = boot()
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "old@x.se")
        assert client.get("/api/me").json()["access_tier"] == "sharing"
    set_tier(hosted_env, "old@x.se", "full")

    app, sender = boot()
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, "old@x.se")
        assert client.get("/api/me").json()["access_tier"] == "full"


def test_desktop_token_user_carries_the_tier(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """A sync token of a disabled account stops at the next request."""
    client, sender = hosted_app
    login(client, sender, "me@x.se")
    token = client.post("/api/me/desktop-tokens", json={"name": "mac"}).json()["token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/sync/fingerprints", headers=headers).status_code == 200
    set_tier(hosted_env, "me@x.se", "disabled")
    assert client.get("/api/sync/fingerprints", headers=headers).status_code == 403


def test_sync_scoped_token_of_a_sharing_account_still_syncs(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """``sharing`` carries ``sync``: the router-level gate must let it through."""
    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    token = client.post("/api/me/desktop-tokens", json={"name": "mac"}).json()["token"]
    set_tier(hosted_env, "friend@x.se", "sharing")
    client.cookies.clear()
    resp = client.get("/api/sync/fingerprints", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_tier_without_sync_is_refused_on_the_sync_router(
    hosted_app, hosted_env, monkeypatch
) -> None:  # noqa: ANN001
    """A non-empty tier lacking ``sync`` hits the router dependency, not the disabled gate."""
    from splitsmith.access import AccessConfig, Feature

    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    state = client.app.state.splitsmith_state
    monkeypatch.setattr(
        state, "access", AccessConfig(tiers={**state.access.tiers, "viewer": frozenset({Feature.share})})
    )
    set_tier(hosted_env, "friend@x.se", "viewer")
    resp = client.get("/api/sync/fingerprints")
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"code": "feature_required", "feature": "sync"}


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/matches/m1/match/shares", {"json": {"scope": "read"}}),
        ("patch", "/api/matches/m1/match/shares/nope/cameras", {"json": {}}),
        ("delete", "/api/matches/m1/match/shares/nope", {}),
    ],
)
def test_share_management_names_the_share_feature(
    hosted_app, hosted_env, monkeypatch, method, path, kwargs  # noqa: ANN001
) -> None:
    """A tier with features but without ``share`` reaches the route gate, not the disabled gate."""
    from splitsmith.access import AccessConfig, Feature
    from tests.hosted_helpers import seed_match

    client, sender = hosted_app
    login(client, sender, "friend@x.se")
    seed_match(hosted_env, "friend@x.se", "m1")
    state = client.app.state.splitsmith_state
    monkeypatch.setattr(
        state, "access", AccessConfig(tiers={**state.access.tiers, "sync_only": frozenset({Feature.sync})})
    )
    set_tier(hosted_env, "friend@x.se", "sync_only")
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"code": "feature_required", "feature": "share"}


def test_disabled_account_cannot_patch_me(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """The disabled allowlist is method-paired: GET /api/me only, never PATCH."""
    client, sender = hosted_app
    login(client, sender, "gone@x.se")
    set_tier(hosted_env, "gone@x.se", "disabled")
    resp = client.patch("/api/me", json={"display_name": "Someone"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"code": "account_disabled"}


def test_disabled_account_can_sign_its_desktop_token_out(hosted_app, hosted_env) -> None:  # noqa: ANN001
    """Revoking a credential is never blocked, whatever the account's tier."""
    client, sender = hosted_app
    login(client, sender, "gone@x.se")
    token = client.post("/api/me/desktop-tokens", json={"name": "mac"}).json()["token"]
    set_tier(hosted_env, "gone@x.se", "disabled")
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.delete("/api/device/session", headers=headers).status_code == 200
    # The token is gone: the next request is anonymous.
    assert client.get("/api/sync/fingerprints", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# Share links follow the owner's account (ruling on the final review): an
# owner with no features at all has nothing reachable through a link, so a
# link collapses to the same 404 as an unknown token. ``sharing`` keeps it.
# ---------------------------------------------------------------------------


def _owner_with_comment_link(client: TestClient, sender, db_url: str) -> str:  # noqa: ANN001
    from tests.hosted_helpers import seed_match
    from tests.test_comments_moderation import MID, SLUG, _seed_state_docs

    login(client, sender, "owner@example.com")
    seed_match(db_url, "owner@example.com", MID)
    _seed_state_docs(db_url, "owner@example.com", MID, SLUG)
    created = client.post(f"/api/matches/{MID}/match/shares", json={"scope": "comment"})
    assert created.status_code == 201, created.text
    return created.json()["url"].rsplit("/", 1)[-1]


def _share_status(client: TestClient, token: str) -> tuple[int, int]:
    """(read status, comment-post status) through ``token``, anonymously."""
    from splitsmith.ui.comments import AUTHOR_KEY_HEADER
    from tests.test_comments_moderation import SLUG, STAGE

    anon = TestClient(client.app, follow_redirects=False)
    read = anon.get(f"/api/share/{token}/shooters/{SLUG}/stages/{STAGE}/comments")
    write = anon.post(
        f"/api/share/{token}/shooters/{SLUG}/stages/{STAGE}/comments",
        json={"body": "nice draw", "anchor_t": 1.0},
        headers={AUTHOR_KEY_HEADER: "c" * 64},
    )
    return read.status_code, write.status_code


def test_disabled_owners_share_link_is_a_uniform_404(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    token = _owner_with_comment_link(client, sender, hosted_env)
    assert _share_status(client, token) == (200, 201)

    set_tier(hosted_env, "owner@example.com", "disabled")
    anon = TestClient(client.app, follow_redirects=False)
    unknown = anon.get("/api/share/not-a-token/match/shooters")
    assert _share_status(client, token) == (404, 404)
    gone = anon.get(f"/api/share/{token}/match/shooters")
    assert gone.status_code == 404
    assert gone.json() == unknown.json()

    set_tier(hosted_env, "owner@example.com", "full")
    assert _share_status(client, token) == (200, 201)


def test_sharing_owners_share_link_keeps_working(hosted_app, hosted_env) -> None:  # noqa: ANN001
    client, sender = hosted_app
    token = _owner_with_comment_link(client, sender, hosted_env)
    set_tier(hosted_env, "owner@example.com", "sharing")
    assert _share_status(client, token) == (200, 201)
