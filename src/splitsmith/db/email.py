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
- **production hosted**: an HTTP sender (Resend / Postmark / ...) selected
  by ``SPLITSMITH_EMAIL_BACKEND``. Not implemented here -- it lands when
  the provider is picked; :func:`build_email_sender` fails loud for any
  backend other than ``console`` so a misconfigured prod deploy can't
  silently drop sign-in mail.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

# Env var selecting the transport. Unset -> console (the dev / self-host
# default). Any recognised provider name selects its HTTP sender.
SPLITSMITH_EMAIL_BACKEND_ENV = "SPLITSMITH_EMAIL_BACKEND"

# Marker the console transport prefixes each link with, so log scrapers
# (the docker smoke's login dance) can pull the URL back out reliably.
CONSOLE_MAGIC_LINK_MARKER = "MAGIC_LINK"


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


def build_email_sender(backend: str | None) -> EmailSender:
    """Resolve the :class:`EmailSender` for ``backend`` (the value of
    ``SPLITSMITH_EMAIL_BACKEND``).

    ``None`` / ``""`` / ``"console"`` -> :class:`ConsoleEmailSender`. Any
    other value raises: a real provider HTTP sender is not implemented yet,
    and falling back to console in production would silently swallow
    sign-in mail. When a provider is chosen, add its branch here.
    """
    name = (backend or "console").strip().lower()
    if name == "console":
        return ConsoleEmailSender()
    raise RuntimeError(
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}={backend!r} is not supported. "
        "Only 'console' is implemented today; a production HTTP sender "
        "(Resend / Postmark) lands when the provider is picked. Set "
        f"{SPLITSMITH_EMAIL_BACKEND_ENV}=console (or leave it unset) for "
        "docker-compose / self-host."
    )
