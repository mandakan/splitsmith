"""Unit tests for the in-house magic-link auth backend (doc 02).

SQLite-backed (aiosqlite), in-process -- the same model code that builds
the Postgres schema. These prove the token + session lifecycle without a
container; the docker smoke proves the HTTP login dance end to end.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from splitsmith.db import (
    Base,
    MagicLinkAuth,
    MagicLinkTokenRow,
    SessionRow,
    create_engine,
    sessionmaker,
)
from splitsmith.db import (
    User as UserRow,
)
from splitsmith.db.email import (
    CONSOLE_MAGIC_LINK_MARKER,
    LETTERMINT_API_URL,
    ConsoleEmailSender,
    LettermintEmailSender,
    build_email_sender,
)
from splitsmith.db.magic_link import (
    SESSION_COOKIE_NAME,
    InvalidMagicLinkError,
    _hash,
)
from splitsmith.db.signup_policy import SignupPolicy, build_signup_policy

BASE_URL = "https://splitsmith.test"


class _CapturingSender:
    """Records the links it would send so a test can redeem them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_magic_link(self, *, to: str, link: str) -> None:
        self.sent.append((to, link))

    def last_token(self) -> str:
        _, link = self.sent[-1]
        return parse_qs(urlparse(link).query)["token"][0]


class _Clock:
    """Mutable clock so expiry windows are testable without sleeping."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


class _Req:
    """Minimal stand-in for ``fastapi.Request`` -- the backend only reads
    ``request.cookies``."""

    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookies = cookies


@pytest.fixture
def session_factory(tmp_path) -> Iterator[object]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite'}"
    engine = create_engine(url)

    async def _create_all() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    yield sessionmaker(engine)


def _auth(session_factory, *, sender=None, clock=None, signup_policy=None) -> MagicLinkAuth:
    return MagicLinkAuth(
        session_factory,
        sender or _CapturingSender(),
        now=clock or (lambda: datetime.now(UTC)),
        signup_policy=signup_policy,
    )


# ----------------------------------------------------------------------
# begin_login
# ----------------------------------------------------------------------


def test_begin_login_emails_link_and_stores_only_the_hash(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)

    challenge = asyncio.run(auth.begin_login("Person@Example.COM", base_url=BASE_URL))

    # Email normalised; a link was "sent" to it.
    assert challenge.email == "person@example.com"
    assert len(sender.sent) == 1
    to, link = sender.sent[0]
    assert to == "person@example.com"
    assert link.startswith(f"{BASE_URL}/auth/callback?token=")

    raw_token = sender.last_token()

    async def _row() -> MagicLinkTokenRow:
        async with session_factory() as s:
            return (
                await s.execute(select(MagicLinkTokenRow).where(MagicLinkTokenRow.id == challenge.id))
            ).scalar_one()

    row = asyncio.run(_row())
    # The raw token is never persisted; only its hash.
    assert row.token_hash == _hash(raw_token)
    assert raw_token not in row.token_hash
    assert row.email == "person@example.com"
    assert row.consumed_at is None


# ----------------------------------------------------------------------
# complete_login
# ----------------------------------------------------------------------


def test_complete_login_creates_user_and_session(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)
    asyncio.run(auth.begin_login("new@example.com", base_url=BASE_URL))

    issued = asyncio.run(auth.complete_login(sender.last_token()))

    assert issued.user.email == "new@example.com"
    assert issued.secret  # raw session secret for the cookie

    async def _check() -> None:
        async with session_factory() as s:
            user = (await s.execute(select(UserRow).where(UserRow.email == "new@example.com"))).scalar_one()
            assert user.email_verified_at is not None
            sess = (await s.execute(select(SessionRow).where(SessionRow.user_id == user.id))).scalar_one()
            # Session stores only the hash of the cookie secret.
            assert sess.token_hash == _hash(issued.secret)

    asyncio.run(_check())


def test_complete_login_is_single_use(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)
    asyncio.run(auth.begin_login("once@example.com", base_url=BASE_URL))
    token = sender.last_token()

    asyncio.run(auth.complete_login(token))
    with pytest.raises(InvalidMagicLinkError) as exc:
        asyncio.run(auth.complete_login(token))
    assert exc.value.reason == "consumed"


def test_complete_login_rejects_expired_token(session_factory) -> None:
    sender = _CapturingSender()
    clock = _Clock(datetime(2026, 5, 30, 12, 0, tzinfo=UTC))
    auth = _auth(session_factory, sender=sender, clock=clock)
    asyncio.run(auth.begin_login("slow@example.com", base_url=BASE_URL))
    token = sender.last_token()

    clock.advance(timedelta(minutes=16))  # past the 15-minute TTL
    with pytest.raises(InvalidMagicLinkError) as exc:
        asyncio.run(auth.complete_login(token))
    assert exc.value.reason == "expired"


def test_complete_login_rejects_unknown_token(session_factory) -> None:
    auth = _auth(session_factory)
    with pytest.raises(InvalidMagicLinkError) as exc:
        asyncio.run(auth.complete_login("not-a-real-token"))
    assert exc.value.reason == "not_found"


def test_complete_login_reuses_existing_user(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)

    asyncio.run(auth.begin_login("repeat@example.com", base_url=BASE_URL))
    first = asyncio.run(auth.complete_login(sender.last_token()))
    asyncio.run(auth.begin_login("repeat@example.com", base_url=BASE_URL))
    second = asyncio.run(auth.complete_login(sender.last_token()))

    assert first.user.id == second.user.id  # same account, second sign-in

    async def _count() -> int:
        async with session_factory() as s:
            rows = (
                (await s.execute(select(UserRow).where(UserRow.email == "repeat@example.com")))
                .scalars()
                .all()
            )
            return len(rows)

    assert asyncio.run(_count()) == 1


# ----------------------------------------------------------------------
# authenticate_request
# ----------------------------------------------------------------------


def test_authenticate_request_resolves_session_cookie(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)
    asyncio.run(auth.begin_login("auth@example.com", base_url=BASE_URL))
    issued = asyncio.run(auth.complete_login(sender.last_token()))

    req = _Req({SESSION_COOKIE_NAME: issued.secret})
    user = asyncio.run(auth.authenticate_request(req))
    assert user is not None
    assert user.id == issued.user.id


def test_authenticate_request_anonymous_for_missing_or_unknown_cookie(session_factory) -> None:
    auth = _auth(session_factory)
    assert asyncio.run(auth.authenticate_request(_Req({}))) is None
    assert asyncio.run(auth.authenticate_request(_Req({SESSION_COOKIE_NAME: "bogus"}))) is None


def test_authenticate_request_rejects_expired_session(session_factory) -> None:
    sender = _CapturingSender()
    clock = _Clock(datetime(2026, 5, 1, 12, 0, tzinfo=UTC))
    auth = _auth(session_factory, sender=sender, clock=clock)
    asyncio.run(auth.begin_login("stale@example.com", base_url=BASE_URL))
    issued = asyncio.run(auth.complete_login(sender.last_token()))

    clock.advance(timedelta(days=31))  # past the 30-day session TTL
    assert asyncio.run(auth.authenticate_request(_Req({SESSION_COOKIE_NAME: issued.secret}))) is None


def test_authenticate_request_anonymous_for_soft_deleted_user(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)
    asyncio.run(auth.begin_login("gone@example.com", base_url=BASE_URL))
    issued = asyncio.run(auth.complete_login(sender.last_token()))

    async def _soft_delete() -> None:
        async with session_factory() as s:
            user = (await s.execute(select(UserRow).where(UserRow.id == issued.user.id))).scalar_one()
            user.deleted_at = datetime.now(UTC)
            await s.commit()

    asyncio.run(_soft_delete())
    assert asyncio.run(auth.authenticate_request(_Req({SESSION_COOKIE_NAME: issued.secret}))) is None


def test_session_sliding_expiry_bumps_after_touch_interval(session_factory) -> None:
    sender = _CapturingSender()
    clock = _Clock(datetime(2026, 5, 30, 12, 0, tzinfo=UTC))
    auth = _auth(session_factory, sender=sender, clock=clock)
    asyncio.run(auth.begin_login("active@example.com", base_url=BASE_URL))
    issued = asyncio.run(auth.complete_login(sender.last_token()))

    async def _expires_at() -> datetime:
        async with session_factory() as s:
            row = (
                await s.execute(select(SessionRow).where(SessionRow.token_hash == _hash(issued.secret)))
            ).scalar_one()
            return row.expires_at

    before = asyncio.run(_expires_at())
    clock.advance(timedelta(hours=2))  # past the 1-hour touch throttle
    asyncio.run(auth.authenticate_request(_Req({SESSION_COOKIE_NAME: issued.secret})))
    after = asyncio.run(_expires_at())

    # Compare on naive value (SQLite drops tzinfo) -- the bump is +2h.
    assert after.replace(tzinfo=None) > before.replace(tzinfo=None)


def test_end_session_revokes(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender)
    asyncio.run(auth.begin_login("bye@example.com", base_url=BASE_URL))
    issued = asyncio.run(auth.complete_login(sender.last_token()))
    req = _Req({SESSION_COOKIE_NAME: issued.secret})

    assert asyncio.run(auth.authenticate_request(req)) is not None
    asyncio.run(auth.end_session(issued.secret))
    assert asyncio.run(auth.authenticate_request(req)) is None
    # Idempotent: revoking again is a no-op, not an error.
    asyncio.run(auth.end_session(issued.secret))


# ----------------------------------------------------------------------
# EmailSender
# ----------------------------------------------------------------------


def test_console_email_sender_logs_parseable_marker(caplog) -> None:
    sender = ConsoleEmailSender()
    with caplog.at_level(logging.INFO, logger="splitsmith.db.email"):
        asyncio.run(sender.send_magic_link(to="x@example.com", link=f"{BASE_URL}/auth/callback?token=abc"))
    assert CONSOLE_MAGIC_LINK_MARKER in caplog.text
    assert "x@example.com" in caplog.text
    assert "token=abc" in caplog.text


def test_build_email_sender_defaults_to_console() -> None:
    assert isinstance(build_email_sender(None), ConsoleEmailSender)
    assert isinstance(build_email_sender("console"), ConsoleEmailSender)
    assert isinstance(build_email_sender("CONSOLE"), ConsoleEmailSender)


def test_build_email_sender_fails_loud_on_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        build_email_sender("mailgun")


def test_build_email_sender_lettermint_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LETTERMINT_API_TOKEN", raising=False)
    monkeypatch.delenv("SPLITSMITH_EMAIL_FROM", raising=False)
    with pytest.raises(RuntimeError, match="LETTERMINT_API_TOKEN"):
        build_email_sender("lettermint")


def test_build_email_sender_lettermint_with_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LETTERMINT_API_TOKEN", "lm_test")
    monkeypatch.setenv("SPLITSMITH_EMAIL_FROM", "Splitsmith <login@splitsmith.test>")
    monkeypatch.delenv("LETTERMINT_ROUTE", raising=False)
    assert isinstance(build_email_sender("lettermint"), LettermintEmailSender)


def test_lettermint_email_sender_posts_the_link() -> None:
    respx = pytest.importorskip("respx")
    import httpx

    sender = LettermintEmailSender(api_token="lm_test", from_address="Splitsmith <login@splitsmith.test>")
    link = f"{BASE_URL}/auth/callback?token=tok123"
    with respx.mock:
        route = respx.post(LETTERMINT_API_URL).mock(
            return_value=httpx.Response(200, json={"message_id": "e1", "status": "queued"})
        )
        asyncio.run(sender.send_magic_link(to="user@example.com", link=link))

    assert route.called
    req = route.calls.last.request
    assert req.headers["x-lettermint-token"] == "lm_test"
    body = req.content.decode()
    assert "user@example.com" in body
    assert "tok123" in body  # the magic link is carried in the body
    assert "route" not in body  # omitted when unset


def test_lettermint_email_sender_includes_route_when_set() -> None:
    respx = pytest.importorskip("respx")
    import httpx

    sender = LettermintEmailSender(api_token="lm_test", from_address="x@y.z", route="production")
    with respx.mock:
        route = respx.post(LETTERMINT_API_URL).mock(
            return_value=httpx.Response(200, json={"message_id": "e1", "status": "queued"})
        )
        asyncio.run(sender.send_magic_link(to="user@example.com", link=f"{BASE_URL}/auth/callback?token=x"))

    body = route.calls.last.request.content.decode()
    assert '"route"' in body and "production" in body


def test_lettermint_email_sender_raises_on_provider_error() -> None:
    respx = pytest.importorskip("respx")
    import httpx

    sender = LettermintEmailSender(api_token="lm_test", from_address="x@y.z")
    with respx.mock:
        respx.post(LETTERMINT_API_URL).mock(
            return_value=httpx.Response(422, json={"error": "domain not verified"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(
                sender.send_magic_link(to="user@example.com", link=f"{BASE_URL}/auth/callback?token=x")
            )


# ----------------------------------------------------------------------
# Signup policy parsing
# ----------------------------------------------------------------------


def test_signup_policy_defaults_open_when_unset() -> None:
    policy = build_signup_policy(open_value=None, allowlist_value=None)
    assert policy.signups_open is True
    assert policy.allows_signup("anyone@example.com") is True


def test_signup_policy_closed_blocks_unlisted() -> None:
    policy = build_signup_policy(open_value="false", allowlist_value="")
    assert policy.signups_open is False
    assert policy.allows_signup("stranger@example.com") is False


def test_signup_policy_allowlist_matches_email_and_domain() -> None:
    policy = build_signup_policy(
        open_value="false",
        allowlist_value="vip@example.com, @thias.se  bare.dev",
    )
    # exact email (case-insensitive)
    assert policy.allows_signup("VIP@example.com") is True
    # @domain entry
    assert policy.allows_signup("anyone@thias.se") is True
    # bare domain entry
    assert policy.allows_signup("dev@bare.dev") is True
    # not listed
    assert policy.allows_signup("nope@elsewhere.com") is False


@pytest.mark.parametrize("raw,expected", [("1", True), ("yes", True), ("off", False), ("0", False)])
def test_signup_policy_bool_parsing(raw: str, expected: bool) -> None:
    assert build_signup_policy(open_value=raw, allowlist_value="").signups_open is expected


# ----------------------------------------------------------------------
# Signup gating in begin_login
# ----------------------------------------------------------------------

_CLOSED = SignupPolicy(signups_open=False, allowed_emails=frozenset({"vip@example.com"}))


def test_begin_login_blocks_new_email_when_closed(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender, signup_policy=_CLOSED)

    challenge = asyncio.run(auth.begin_login("stranger@example.com", base_url=BASE_URL))

    # Neutral, challenge-shaped response, but nothing minted or sent.
    assert challenge.email == "stranger@example.com"
    assert sender.sent == []

    async def _count_tokens() -> int:
        async with session_factory() as s:
            return len((await s.execute(select(MagicLinkTokenRow))).scalars().all())

    assert asyncio.run(_count_tokens()) == 0


def test_begin_login_allows_allowlisted_email_when_closed(session_factory) -> None:
    sender = _CapturingSender()
    auth = _auth(session_factory, sender=sender, signup_policy=_CLOSED)

    asyncio.run(auth.begin_login("vip@example.com", base_url=BASE_URL))

    assert len(sender.sent) == 1
    assert sender.sent[0][0] == "vip@example.com"


def test_begin_login_allows_returning_user_when_closed(session_factory) -> None:
    # Create the account while open, then close signups: the returning user
    # must still be able to request a link.
    open_sender = _CapturingSender()
    open_auth = _auth(session_factory, sender=open_sender)
    asyncio.run(open_auth.begin_login("member@example.com", base_url=BASE_URL))
    asyncio.run(open_auth.complete_login(open_sender.last_token()))

    closed_sender = _CapturingSender()
    closed_auth = _auth(session_factory, sender=closed_sender, signup_policy=_CLOSED)
    asyncio.run(closed_auth.begin_login("member@example.com", base_url=BASE_URL))

    assert len(closed_sender.sent) == 1


# ----------------------------------------------------------------------
# Signup gating in complete_login (defense in depth)
# ----------------------------------------------------------------------


def test_complete_login_refuses_new_account_when_closed(session_factory) -> None:
    # Mint a token while signups are open, then redeem it after closing.
    open_sender = _CapturingSender()
    open_auth = _auth(session_factory, sender=open_sender)
    asyncio.run(open_auth.begin_login("late@example.com", base_url=BASE_URL))
    token = open_sender.last_token()

    closed_auth = _auth(session_factory, signup_policy=_CLOSED)
    with pytest.raises(InvalidMagicLinkError) as exc:
        asyncio.run(closed_auth.complete_login(token))
    assert exc.value.reason == "signups_closed"

    async def _count_users() -> int:
        async with session_factory() as s:
            return len((await s.execute(select(UserRow))).scalars().all())

    assert asyncio.run(_count_users()) == 0
