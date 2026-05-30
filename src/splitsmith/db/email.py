"""Pluggable e-mail transport for magic-link delivery.

The auth backend (:class:`splitsmith.db.magic_link.MagicLinkAuth`) does
not know how mail is sent -- it builds the magic-link URL and hands it to
an :class:`EmailSender`. That keeps the deployment-specific transport out
of the auth logic and lets the two deployments that must stay viable use
different transports without touching the backend:

- **docker-compose / self-host** (no mail credentials): the default
  :class:`ConsoleEmailSender` logs the link. ``docker compose up`` works
  with zero external configuration, and the smoke test reads the link
  back from the container logs.
- **production hosted**: :class:`ResendEmailSender`, selected by
  ``SPLITSMITH_EMAIL_BACKEND=resend`` (+ ``RESEND_API_KEY`` /
  ``SPLITSMITH_EMAIL_FROM``). :func:`build_email_sender` fails loud for any
  unknown backend so a misconfigured prod deploy can't silently drop
  sign-in mail.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

# Env var selecting the transport. Unset -> console (the dev / self-host
# default). Any recognised provider name selects its HTTP sender.
SPLITSMITH_EMAIL_BACKEND_ENV = "SPLITSMITH_EMAIL_BACKEND"
# Resend transport config (only read when the backend is ``resend``).
RESEND_API_KEY_ENV = "RESEND_API_KEY"
# The verified ``From`` address, e.g. ``Splitsmith <login@splitsmith.app>``.
SPLITSMITH_EMAIL_FROM_ENV = "SPLITSMITH_EMAIL_FROM"

# Marker the console transport prefixes each link with, so log scrapers
# (the docker smoke's login dance) can pull the URL back out reliably.
CONSOLE_MAGIC_LINK_MARKER = "MAGIC_LINK"

RESEND_API_URL = "https://api.resend.com/emails"


class EmailSender(Protocol):
    async def send_magic_link(self, *, to: str, link: str) -> None:
        """Deliver a magic-link sign-in URL to ``to``.

        Implementations must not raise on a merely-unknown recipient --
        sign-in must not leak whether an address has an account. A genuine
        transport failure (provider down) may raise; the caller decides
        how to surface it.
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


class ResendEmailSender:
    """Sends magic links via the Resend HTTP API (production transport).

    Uses ``httpx`` (already a runtime dep) rather than the ``resend`` SDK to
    avoid a new dependency for one POST. A transport / provider error raises
    (the contract allows it) -- ``begin_login`` then surfaces it; the SPA
    shows a "couldn't send, retry" message. It never inspects the recipient,
    so it can't leak whether an address has an account.
    """

    def __init__(self, *, api_key: str, from_address: str) -> None:
        self._api_key = api_key
        self._from = from_address

    async def send_magic_link(self, *, to: str, link: str) -> None:
        import httpx

        text, html = _magic_link_email_body(link)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "from": self._from,
                    "to": [to],
                    "subject": "Your Splitsmith sign-in link",
                    "text": text,
                    "html": html,
                },
            )
        resp.raise_for_status()


def build_email_sender(backend: str | None) -> EmailSender:
    """Resolve the :class:`EmailSender` for ``backend`` (the value of
    ``SPLITSMITH_EMAIL_BACKEND``).

    - ``None`` / ``""`` / ``"console"`` -> :class:`ConsoleEmailSender`.
    - ``"resend"`` -> :class:`ResendEmailSender`, configured from
      ``RESEND_API_KEY`` + ``SPLITSMITH_EMAIL_FROM`` (both required; missing
      either fails loud rather than silently dropping sign-in mail).
    - anything else -> raises.
    """
    name = (backend or "console").strip().lower()
    if name == "console":
        return ConsoleEmailSender()
    if name == "resend":
        api_key = os.environ.get(RESEND_API_KEY_ENV, "").strip()
        from_address = os.environ.get(SPLITSMITH_EMAIL_FROM_ENV, "").strip()
        if not api_key or not from_address:
            raise RuntimeError(
                f"{SPLITSMITH_EMAIL_BACKEND_ENV}=resend requires both "
                f"{RESEND_API_KEY_ENV} and {SPLITSMITH_EMAIL_FROM_ENV} "
                "(e.g. 'Splitsmith <login@yourdomain>') to be set."
            )
        return ResendEmailSender(api_key=api_key, from_address=from_address)
    raise RuntimeError(
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}={backend!r} is not supported. "
        f"Use 'console' (dev / self-host) or 'resend' (production). Set "
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}=console (or leave it unset) for "
        "docker-compose / self-host."
    )
