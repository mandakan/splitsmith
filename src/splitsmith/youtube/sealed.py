"""Sealing the hosted refresh token at rest (issue #1000, phase 2).

One account's YouTube refresh token lives in ``users.youtube_connection``.
A database dump must not hand out every connected channel, so the token
is Fernet-sealed under a key the API process and every worker share
through :data:`ENV_TOKEN_KEY`. Nothing else in the codebase touches the
key: ``seal`` on the way into the row, ``open_sealed`` on the way out.

The key is a Fernet key (32 url-safe base64 bytes). ``generate_key``
makes one for the operator; it is printed by ``splitsmith youtube
keygen`` and never derived from anything else.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from .oauth import NotConfiguredError, YouTubeError

ENV_TOKEN_KEY = "SPLITSMITH_YOUTUBE_TOKEN_KEY"


class SealedTokenError(YouTubeError):
    """A sealed value did not open under the configured key."""


def token_key_from_env() -> str | None:
    """The configured key, or ``None``; a blank value reads as unset."""
    return os.environ.get(ENV_TOKEN_KEY, "").strip() or None


def is_key_configured() -> bool:
    return token_key_from_env() is not None


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _fernet(key: str | None) -> Fernet:
    key = key or token_key_from_env()
    if key is None:
        raise NotConfiguredError(f"no token key is configured; set {ENV_TOKEN_KEY}")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise NotConfiguredError(f"{ENV_TOKEN_KEY} is not a Fernet key: {exc}") from exc


def seal(plaintext: str, *, key: str | None = None) -> str:
    """Encrypt ``plaintext`` under ``key`` (the env key when omitted)."""
    return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def open_sealed(sealed: str, *, key: str | None = None) -> str:
    """Decrypt a value :func:`seal` produced, or raise :class:`SealedTokenError`."""
    try:
        return _fernet(key).decrypt(sealed.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise SealedTokenError(
            f"the stored YouTube token does not open under {ENV_TOKEN_KEY}; "
            "the key changed, or the row was written under another one. Connect YouTube again."
        ) from exc
