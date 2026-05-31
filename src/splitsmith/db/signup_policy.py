"""Signup gating for hosted magic-link auth.

A small policy object that answers one question: may ``email`` create a
*new* account right now? It gates **signups only** -- returning users
(an email that already has an account) always sign in, gate or not. The
point is anti-spam: close public signups during a beta / launch and let
through only an allowlist, without blocking the people already in.

Resolved once at boot from two env vars (configuration is data, doc 00):

- ``SPLITSMITH_SIGNUPS_OPEN`` -- ``true`` (default) lets anyone sign up;
  ``false`` closes signups to all but the allowlist. The default is open
  so local / self-host hosted mode works with zero config; the hosted
  production deploy sets it to ``false`` explicitly.
- ``SPLITSMITH_SIGNUP_ALLOWLIST`` -- comma/space-separated entries, each
  either an exact address (``a@b.com``) or a domain (``@b.com`` or bare
  ``b.com``) that matches anyone there. Only consulted when signups are
  closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SPLITSMITH_SIGNUPS_OPEN_ENV = "SPLITSMITH_SIGNUPS_OPEN"
SPLITSMITH_SIGNUP_ALLOWLIST_ENV = "SPLITSMITH_SIGNUP_ALLOWLIST"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class SignupPolicy:
    """Whether new-account creation is permitted, and for whom.

    Immutable; built once at boot. ``allows_signup`` is the only thing
    callers need.
    """

    signups_open: bool = True
    allowed_emails: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()

    @classmethod
    def open(cls) -> SignupPolicy:
        """The permissive default: anyone may sign up. Used when no policy
        is configured (local / self-host / tests)."""
        return cls(signups_open=True)

    def allows_signup(self, email: str) -> bool:
        """True if ``email`` may create a new account.

        Open signups -> always True. Closed -> True only if the address or
        its domain is on the allowlist. This does **not** consider whether
        the email already has an account; the caller lets returning users
        through separately.
        """
        if self.signups_open:
            return True
        normalized = email.strip().lower()
        if normalized in self.allowed_emails:
            return True
        domain = normalized.rpartition("@")[2]
        return bool(domain) and domain in self.allowed_domains


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _parse_allowlist(raw: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Split the allowlist string into (exact emails, domains).

    Entries split on commas and whitespace. ``@b.com`` and bare ``b.com``
    are domains; anything else containing ``@`` is an exact address.
    Everything is lower-cased.
    """
    emails: set[str] = set()
    domains: set[str] = set()
    for token in (raw or "").replace(",", " ").split():
        entry = token.strip().lower()
        if not entry:
            continue
        if entry.startswith("@"):
            domains.add(entry[1:])
        elif "@" in entry:
            emails.add(entry)
        else:
            domains.add(entry)
    return frozenset(emails), frozenset(domains)


def build_signup_policy(
    *,
    open_value: str | None = None,
    allowlist_value: str | None = None,
) -> SignupPolicy:
    """Resolve the :class:`SignupPolicy` from env (or the passed overrides,
    which exist for tests). Unset ``SPLITSMITH_SIGNUPS_OPEN`` -> open."""
    open_raw = open_value if open_value is not None else os.environ.get(SPLITSMITH_SIGNUPS_OPEN_ENV)
    allow_raw = (
        allowlist_value if allowlist_value is not None else os.environ.get(SPLITSMITH_SIGNUP_ALLOWLIST_ENV)
    )
    emails, domains = _parse_allowlist(allow_raw)
    return SignupPolicy(
        signups_open=_parse_bool(open_raw, default=True),
        allowed_emails=emails,
        allowed_domains=domains,
    )
