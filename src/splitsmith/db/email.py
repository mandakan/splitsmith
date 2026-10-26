"""Pluggable e-mail transport for magic-link delivery and access mail.

The auth backend (:class:`splitsmith.db.magic_link.MagicLinkAuth`) does
not know how mail is sent -- it builds the magic-link URL and hands it to
an :class:`EmailSender`. That keeps the deployment-specific transport out
of the auth logic and lets the two deployments that must stay viable use
different transports without touching the backend:

- **docker-compose / self-host** (no mail credentials): the default
  :class:`ConsoleEmailSender` logs the link. ``docker compose up`` works
  with zero external configuration, and the smoke test reads the link
  back from the container logs.
- **production hosted**: :class:`LettermintEmailSender`, selected by
  ``SPLITSMITH_EMAIL_BACKEND=lettermint`` (+ ``LETTERMINT_API_TOKEN`` /
  ``SPLITSMITH_EMAIL_FROM``, with an optional ``LETTERMINT_ROUTE``).
  :func:`build_email_sender` fails loud for any unknown backend so a
  misconfigured prod deploy can't silently drop sign-in mail.
"""

from __future__ import annotations

import html as _html
import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

# Env var selecting the transport. Unset -> console (the dev / self-host
# default). Any recognised provider name selects its HTTP sender.
SPLITSMITH_EMAIL_BACKEND_ENV = "SPLITSMITH_EMAIL_BACKEND"
# Lettermint transport config (only read when the backend is ``lettermint``).
LETTERMINT_API_TOKEN_ENV = "LETTERMINT_API_TOKEN"
# Optional Lettermint route (e.g. ``production``); omitted from the payload
# when unset so the account default applies.
LETTERMINT_ROUTE_ENV = "LETTERMINT_ROUTE"
# The verified ``From`` address, e.g. ``Splitsmith <login@splitsmith.app>``.
SPLITSMITH_EMAIL_FROM_ENV = "SPLITSMITH_EMAIL_FROM"

# Marker the console transport prefixes each link with, so log scrapers
# (the docker smoke's login dance) can pull the URL back out reliably.
CONSOLE_MAGIC_LINK_MARKER = "MAGIC_LINK"

LETTERMINT_API_URL = "https://api.lettermint.co/v1/send"


class EmailSender(Protocol):
    async def send_magic_link(self, *, to: str, link: str) -> None:
        """Deliver a magic-link sign-in URL to ``to``.

        Implementations must not raise on a merely-unknown recipient --
        sign-in must not leak whether an address has an account. A genuine
        transport failure (provider down) may raise; the caller decides
        how to surface it.
        """

    async def send_access_granted(self, *, to: str, link: str) -> None:
        """You're in: one sign-in link, same validity line as the magic link.

        Sent when an admin approves an access request (spec 2026-10-03).
        """

    async def send_access_request_alert(
        self, *, to: str, email: str, note: str | None, source: str, admin_url: str
    ) -> None:
        """To an admin; never to the requester.

        Names the requesting ``email``, its ``note`` and ``source``, and
        links to the admin page. Mailing the requester would let anyone
        make splitsmith mail anyone.
        """


class ConsoleEmailSender:
    """Logs the magic link instead of sending mail.

    The transport for docker-compose dev and credential-less self-host.
    Emits one parseable ``MAGIC_LINK <to> <link>`` line at INFO so the
    operator (or the docker smoke test) can copy the URL from the logs.
    Never sends anything off-box -- safe to leave wired in any deployment
    that hasn't configured a real provider.
    """

    async def send_magic_link(self, *, to: str, link: str) -> None:
        logger.info("%s %s %s", CONSOLE_MAGIC_LINK_MARKER, to, link)

    async def send_access_granted(self, *, to: str, link: str) -> None:
        logger.info("ACCESS_GRANTED %s %s", to, link)

    async def send_access_request_alert(
        self, *, to: str, email: str, note: str | None, source: str, admin_url: str
    ) -> None:
        logger.info("ACCESS_REQUEST %s %s %s", to, email, source)


def _magic_link_email_body(link: str) -> tuple[str, str]:
    """Return ``(text, html)`` bodies for a sign-in e-mail. Deliberately
    plain -- one link, the 15-minute validity, and a no-op-if-you-didn't-ask
    line (a phishing-resistance norm for magic links)."""
    text = (
        "Sign in to Splitsmith:\n\n"
        f"{link}\n\n"
        "This link is valid for 15 minutes and can be used once. "
        "If you didn't request it, you can ignore this e-mail."
    )
    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5">'
        "<p>Sign in to Splitsmith:</p>"
        f'<p><a href="{link}">Sign in</a></p>'
        '<p style="color:#666;font-size:13px">This link is valid for 15 minutes '
        "and can be used once. If you didn't request it, you can ignore this e-mail.</p>"
        "</div>"
    )
    return text, html


def _access_granted_email_body(link: str) -> tuple[str, str]:
    """Return ``(text, html)`` bodies for the "you're in" e-mail: one
    sign-in link and the same validity line as the magic link."""
    text = (
        "You have access to Splitsmith. Sign in:\n\n"
        f"{link}\n\n"
        "This link is valid for 15 minutes and can be used once. "
        "After that, sign in with your e-mail address as usual."
    )
    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5">'
        "<p>You have access to Splitsmith.</p>"
        f'<p><a href="{_html.escape(link)}">Sign in</a></p>'
        '<p style="color:#666;font-size:13px">This link is valid for 15 minutes '
        "and can be used once. After that, sign in with your e-mail address as usual.</p>"
        "</div>"
    )
    return text, html


def _access_request_alert_body(
    *, email: str, note: str | None, source: str, admin_url: str
) -> tuple[str, str]:
    """Return ``(text, html)`` bodies for an admin's access-request alert.
    ``email`` and ``note`` are whatever a stranger typed, so the HTML part
    escapes every interpolated string."""
    text_lines = [f"Access request from {email} (source: {source})."]
    if note:
        text_lines.append(f"\nNote: {note}")
    text_lines.append(f"\nReview it: {admin_url}")
    text = "\n".join(text_lines)
    note_html = f"<p>Note: {_html.escape(note)}</p>" if note else ""
    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.5">'
        f"<p>Access request from {_html.escape(email)} (source: {_html.escape(source)}).</p>"
        f"{note_html}"
        f'<p><a href="{_html.escape(admin_url)}">Review it</a></p>'
        "</div>"
    )
    return text, html


class LettermintEmailSender:
    """Sends magic links via the Lettermint HTTP API (production transport).

    Uses ``httpx`` (already a runtime dep) rather than the ``lettermint`` SDK
    to avoid a new dependency for one POST. A transport / provider error
    raises (the contract allows it) -- ``begin_login`` then surfaces it; the
    SPA shows a "couldn't send, retry" message. It never inspects the
    recipient, so it can't leak whether an address has an account.
    """

    def __init__(self, *, api_token: str, from_address: str, route: str | None = None) -> None:
        self._api_token = api_token
        self._from = from_address
        self._route = route

    async def _send(self, *, to: str, subject: str, text: str, html: str) -> None:
        import httpx

        payload: dict[str, object] = {
            "from": self._from,
            "to": [to],
            "subject": subject,
            "text": text,
            "html": html,
        }
        if self._route:
            payload["route"] = self._route
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                LETTERMINT_API_URL,
                headers={"x-lettermint-token": self._api_token},
                json=payload,
            )
        resp.raise_for_status()

    async def send_magic_link(self, *, to: str, link: str) -> None:
        text, html = _magic_link_email_body(link)
        await self._send(to=to, subject="Your Splitsmith sign-in link", text=text, html=html)

    async def send_access_granted(self, *, to: str, link: str) -> None:
        text, html = _access_granted_email_body(link)
        await self._send(to=to, subject="You have access to Splitsmith", text=text, html=html)

    async def send_access_request_alert(
        self, *, to: str, email: str, note: str | None, source: str, admin_url: str
    ) -> None:
        text, html = _access_request_alert_body(email=email, note=note, source=source, admin_url=admin_url)
        # A stranger typed ``email``: keep control characters out of the
        # subject line.
        subject = "Splitsmith access request: " + "".join(c for c in email if c.isprintable())
        await self._send(to=to, subject=subject, text=text, html=html)


def build_email_sender(backend: str | None) -> EmailSender:
    """Resolve the :class:`EmailSender` for ``backend`` (the value of
    ``SPLITSMITH_EMAIL_BACKEND``).

    - ``None`` / ``""`` / ``"console"`` -> :class:`ConsoleEmailSender`.
    - ``"lettermint"`` -> :class:`LettermintEmailSender`, configured from
      ``LETTERMINT_API_TOKEN`` + ``SPLITSMITH_EMAIL_FROM`` (both required;
      missing either fails loud rather than silently dropping sign-in mail)
      and an optional ``LETTERMINT_ROUTE``.
    - anything else -> raises.
    """
    name = (backend or "console").strip().lower()
    if name == "console":
        return ConsoleEmailSender()
    if name == "lettermint":
        api_token = os.environ.get(LETTERMINT_API_TOKEN_ENV, "").strip()
        from_address = os.environ.get(SPLITSMITH_EMAIL_FROM_ENV, "").strip()
        if not api_token or not from_address:
            raise RuntimeError(
                f"{SPLITSMITH_EMAIL_BACKEND_ENV}=lettermint requires both "
                f"{LETTERMINT_API_TOKEN_ENV} and {SPLITSMITH_EMAIL_FROM_ENV} "
                "(e.g. 'Splitsmith <login@yourdomain>') to be set."
            )
        route = os.environ.get(LETTERMINT_ROUTE_ENV, "").strip() or None
        return LettermintEmailSender(api_token=api_token, from_address=from_address, route=route)
    raise RuntimeError(
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}={backend!r} is not supported. "
        f"Use 'console' (dev / self-host) or 'lettermint' (production). Set "
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}=console (or leave it unset) for "
        "docker-compose / self-host."
    )
