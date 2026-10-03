"""Access-request intake: no existence leak, no mail to strangers (spec 2026-10-03)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from splitsmith.db import Base, MagicLinkAuth, create_engine, sessionmaker
from splitsmith.db.email import LETTERMINT_API_URL, LettermintEmailSender
from splitsmith.db.signup_policy import SignupPolicy
from tests.hosted_helpers import PUBLIC_URL, _CapturingSender, login

INTAKE_COPY = "If you have access, a sign-in link is on its way. Otherwise your request has been noted."


def _make_closed_app(sender: _CapturingSender):  # noqa: ANN202
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state
    state.auth.backends[0]._email = sender
    state.email_sender = sender
    return app, state


@pytest.fixture
def closed_env(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SPLITSMITH_SIGNUPS_OPEN", "false")
    monkeypatch.setenv("SPLITSMITH_SIGNUP_ALLOWLIST", "me@x.se")
    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", "me@x.se")
    return hosted_env


@pytest.fixture
def closed_app(closed_env: str):  # noqa: ANN201
    sender = _CapturingSender()
    app, state = _make_closed_app(sender)
    with TestClient(app, follow_redirects=False) as client:
        yield client, sender, state


def requests_list(state) -> list:  # noqa: ANN001
    return asyncio.run(state.access_requests.list())


def drain(client: TestClient, state) -> None:  # noqa: ANN001
    """Wait for the app's background tasks (the admin alerts) on the app's
    own event loop."""

    async def _wait() -> None:
        while state.background_tasks:
            await asyncio.gather(*list(state.background_tasks), return_exceptions=True)

    client.portal.call(_wait)


# ---------------------------------------------------------------------------
# Sign-in with an unknown email
# ---------------------------------------------------------------------------


def test_unknown_email_at_login_becomes_a_pending_request(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    assert client.post("/api/v1/auth/begin", json={"email": "Erik@x.se"}).status_code == 200
    [row] = requests_list(state)
    assert (row.email, row.source, row.status) == ("erik@x.se", "login", "pending")
    drain(client, state)
    assert sender.links == []  # nothing to the requester
    assert sender.granted == []
    [alert] = sender.alerts  # one alert, to the admin
    assert alert == {
        "to": "me@x.se",
        "email": "erik@x.se",
        "note": None,
        "source": "login",
        "admin_url": f"{PUBLIC_URL}/admin/access",
    }


def test_allowlisted_login_records_nothing(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    login(client, sender, "me@x.se")
    drain(client, state)
    assert requests_list(state) == []
    assert sender.alerts == []


def test_repeat_request_does_not_realert(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    client.post("/api/v1/auth/begin", json={"email": "erik@x.se"})
    client.post("/api/v1/access-requests", json={"email": "erik@x.se", "note": "Bromma"})
    drain(client, state)
    assert len(sender.alerts) == 1


def test_responses_are_identical_for_every_email_state(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    login(client, sender, "me@x.se")  # allowlisted: a real account
    client.cookies.clear()
    client.post("/api/v1/access-requests", json={"email": "pending@x.se"})
    client.post("/api/v1/access-requests", json={"email": "declined@x.se"})
    rid = next(r.id for r in requests_list(state) if r.email == "declined@x.se")
    asyncio.run(state.access_requests.decline(rid, admin_email="me@x.se"))
    emails = ["me@x.se", "pending@x.se", "declined@x.se", "brand-new@x.se"]

    def shape(resp) -> tuple:  # noqa: ANN001
        # Body bytes plus everything else the client can see except the
        # per-response date header.
        headers = tuple(sorted((k, v) for k, v in resp.headers.items() if k != "date"))
        return resp.status_code, headers, resp.content

    begin = {shape(client.post("/api/v1/auth/begin", json={"email": e})) for e in emails}
    form = {shape(client.post("/api/v1/access-requests", json={"email": e})) for e in emails}
    assert len(begin) == 1, begin
    assert len(form) == 1, form
    [(status, _, body)] = form
    assert status == 202
    assert body == b'{"ok":true,"message":"' + INTAKE_COPY.encode() + b'"}'
    # Only the known account got a link; the strangers got nothing.
    assert {to for to, _ in sender.links} == {"me@x.se"}


def test_form_records_source_and_note(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    client.post("/api/v1/access-requests", json={"email": " A@X.se ", "note": "  Bromma  "})
    client.post("/api/v1/access-requests", json={"email": "b@x.se", "source": "waitlist"})
    drain(client, state)
    rows = {r.email: r for r in requests_list(state)}
    assert (rows["a@x.se"].source, rows["a@x.se"].note) == ("form", "Bromma")
    assert rows["b@x.se"].source == "waitlist"
    assert [(a["email"], a["source"], a["note"]) for a in sender.alerts] == [
        ("a@x.se", "form", "Bromma"),
        ("b@x.se", "waitlist", None),
    ]
    assert sender.links == [] and sender.granted == []


def test_form_route_rate_limit_still_answers_202(closed_app) -> None:  # noqa: ANN001
    client, _, state = closed_app
    bodies = {client.post("/api/v1/access-requests", json={"email": f"u{i}@x.se"}).content for i in range(8)}
    assert len(bodies) == 1
    assert len(requests_list(state)) == 5  # per-IP limit: 5 an hour


def test_rate_limit_is_per_app(closed_env: str) -> None:
    """Limiter state belongs to one app: a second app in the same process
    starts with a fresh budget."""
    for round_ in range(2):
        sender = _CapturingSender()
        app, state = _make_closed_app(sender)
        with TestClient(app) as client:
            for i in range(6):
                client.post("/api/v1/access-requests", json={"email": f"r{round_}v{i}@x.se"})
            drain(client, state)
        assert len(sender.alerts) == 5, round_


def test_honeypot_records_nothing(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app
    resp = client.post("/api/v1/access-requests", json={"email": "bot@x.se", "hp": "filled"})
    assert resp.status_code == 202
    assert resp.json() == {"ok": True, "message": INTAKE_COPY}
    drain(client, state)
    assert requests_list(state) == []
    assert sender.alerts == []


def test_malformed_email_is_400(closed_app) -> None:  # noqa: ANN001
    client, _, _ = closed_app
    assert client.post("/api/v1/access-requests", json={"email": "nope"}).status_code == 400


def test_cors_preflight_only_for_the_marketing_origin(closed_app) -> None:  # noqa: ANN001
    client, _, _ = closed_app
    ok = client.options(
        "/api/v1/access-requests",
        headers={"Origin": "https://splitsmith.app", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "https://splitsmith.app"
    other = client.options(
        "/api/v1/access-requests",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers


def test_cors_header_on_the_post_for_the_marketing_origin(closed_app) -> None:  # noqa: ANN001
    client, _, _ = closed_app
    ok = client.post(
        "/api/v1/access-requests", json={"email": "a@x.se"}, headers={"Origin": "https://www.splitsmith.app"}
    )
    assert ok.headers.get("access-control-allow-origin") == "https://www.splitsmith.app"
    other = client.post(
        "/api/v1/access-requests", json={"email": "b@x.se"}, headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in other.headers


def test_route_404s_in_local_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    from splitsmith.ui.server import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        assert client.post("/api/v1/access-requests", json={"email": "a@x.se"}).status_code == 404
        assert client.options("/api/v1/access-requests").status_code == 404


def test_alert_failure_never_fails_the_request(closed_app) -> None:  # noqa: ANN001
    client, sender, state = closed_app

    async def boom(**_kw: object) -> None:
        raise RuntimeError("provider down")

    sender.send_access_request_alert = boom
    resp = client.post("/api/v1/access-requests", json={"email": "a@x.se"})
    assert resp.status_code == 202
    assert len(requests_list(state)) == 1
    # Same at sign-in: the blocked branch swallows it too.
    assert client.post("/api/v1/auth/begin", json={"email": "b@x.se"}).status_code == 200
    assert len(requests_list(state)) == 2
    drain(client, state)  # the failures are logged, not raised


class _SlowSender(_CapturingSender):
    """An alert that cannot finish until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    async def send_access_request_alert(self, **kw: object) -> None:
        await asyncio.to_thread(self.release.wait, 10.0)
        self.alerts.append(kw)


@pytest.mark.parametrize(
    ("path", "ok"),
    [("/api/v1/access-requests", 202), ("/api/v1/auth/begin", 200)],
)
def test_a_slow_alert_does_not_delay_the_response(closed_env: str, path: str, ok: int) -> None:
    sender = _SlowSender()
    app, state = _make_closed_app(sender)
    with TestClient(app) as client:
        started = time.monotonic()
        resp = client.post(path, json={"email": "slow@x.se"})
        elapsed = time.monotonic() - started
        assert resp.status_code == ok
        # The response came back while the alert was still blocked.
        assert sender.alerts == []
        assert elapsed < 5.0
        assert [r.email for r in requests_list(state)] == ["slow@x.se"]
        sender.release.set()
        drain(client, state)
        assert [a["email"] for a in sender.alerts] == ["slow@x.se"]


# ---------------------------------------------------------------------------
# MagicLinkAuth: mint_link and on_blocked
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[object]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite'}")

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    yield sessionmaker(engine)


def test_mint_link_returns_a_redeemable_link_and_sends_nothing(session_factory) -> None:  # noqa: ANN001
    sender = _CapturingSender()
    auth = MagicLinkAuth(session_factory, sender)
    link = asyncio.run(auth.mint_link("New@X.se", base_url="https://h.test/"))
    assert link.startswith("https://h.test/auth/callback?token=")
    assert sender.links == []
    token = parse_qs(urlparse(link).query)["token"][0]
    issued = asyncio.run(auth.complete_login(token))
    assert issued.user.email == "new@x.se"


def test_on_blocked_gets_the_normalised_email_and_its_failure_is_swallowed(
    session_factory,
) -> None:  # noqa: ANN001
    seen: list[str] = []

    async def on_blocked(email: str) -> None:
        seen.append(email)
        raise RuntimeError("db down")

    sender = _CapturingSender()
    auth = MagicLinkAuth(
        session_factory, sender, signup_policy=SignupPolicy(signups_open=False), on_blocked=on_blocked
    )
    challenge = asyncio.run(auth.begin_login(" Erik@X.se ", base_url="https://h.test"))
    assert challenge.id == "blocked"
    assert seen == ["erik@x.se"]
    assert sender.links == []


# ---------------------------------------------------------------------------
# Lettermint bodies
# ---------------------------------------------------------------------------


def test_lettermint_alert_escapes_user_strings_in_html() -> None:
    respx = pytest.importorskip("respx")
    import json

    import httpx

    sender = LettermintEmailSender(api_token="lm_test", from_address="x@y.z")
    with respx.mock:
        route = respx.post(LETTERMINT_API_URL).mock(return_value=httpx.Response(200, json={}))
        asyncio.run(
            sender.send_access_request_alert(
                to="admin@x.se",
                email="<b>evil</b>@x.se",
                note='<script>alert("x")</script>',
                source="form",
                admin_url="https://h.test/admin/access",
            )
        )
    payload = json.loads(route.calls.last.request.content)
    assert payload["to"] == ["admin@x.se"]
    assert payload["subject"] == "Splitsmith access request: <b>evil</b>@x.se"
    assert "<script>" not in payload["html"] and "<b>evil</b>" not in payload["html"]
    assert "&lt;script&gt;" in payload["html"] and "&lt;b&gt;evil&lt;/b&gt;@x.se" in payload["html"]
    assert "https://h.test/admin/access" in payload["html"]
    assert "form" in payload["text"] and '<script>alert("x")</script>' in payload["text"]


def test_lettermint_access_granted_carries_the_link() -> None:
    respx = pytest.importorskip("respx")
    import json

    import httpx

    sender = LettermintEmailSender(api_token="lm_test", from_address="x@y.z")
    link = "https://h.test/auth/callback?token=tok123"
    with respx.mock:
        route = respx.post(LETTERMINT_API_URL).mock(return_value=httpx.Response(200, json={}))
        asyncio.run(sender.send_access_granted(to="u@x.se", link=link))
    payload = json.loads(route.calls.last.request.content)
    assert payload["to"] == ["u@x.se"]
    assert payload["subject"] == "You have access to Splitsmith"
    assert link in payload["text"] and "tok123" in payload["html"]


def test_store_normalisers_are_public() -> None:
    from splitsmith.db.access_requests import normalize_email, normalize_note

    assert normalize_email(" A@X.se ") == "a@x.se"
    assert normalize_note("   ") is None
    assert normalize_note(" x" * 600) == ("x " * 600).strip()[:500]
