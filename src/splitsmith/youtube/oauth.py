"""OAuth for the YouTube Data API: client config, token store, consent flow.

One Google Cloud project, its OAuth client built into the app. Installed-
app client secrets are non-confidential by Google's own definition, which
is why ``BUILTIN_CLIENT_SECRET`` can be a constant. The env overrides exist
for development before the shipped id lands and for anyone running their
own project. Note that a personal project does not escape YouTube's
private-only lock on unaudited projects; see the spec.

The refresh token lives in ``<user config dir>/youtube.json`` next to
``scoreboard.json``, written through the same atomic helper. Access tokens
are never persisted; :class:`AccessTokenProvider` refreshes on demand.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .. import user_config

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

ENV_CLIENT_ID = "SPLITSMITH_YOUTUBE_CLIENT_ID"
ENV_CLIENT_SECRET = "SPLITSMITH_YOUTUBE_CLIENT_SECRET"

#: Filled in once the splitsmith Google Cloud project's Desktop client
#: exists. Empty means "not configured": ``connect`` refuses rather than
#: opening a consent page Google would reject.
BUILTIN_CLIENT_ID = ""
BUILTIN_CLIENT_SECRET = ""

CONNECTION_FILENAME = "youtube.json"


class YouTubeError(Exception):
    """Base for every error the youtube package raises on purpose."""


class NotConfiguredError(YouTubeError):
    """No OAuth client id / secret is available."""


class NotConnectedError(YouTubeError):
    """No stored connection; run ``splitsmith youtube login``."""


class ReauthorizeError(YouTubeError):
    """The refresh token was rejected (``invalid_grant``); reconnect."""


class OAuthClient(BaseModel):
    client_id: str
    client_secret: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    @classmethod
    def configured(cls) -> OAuthClient:
        """Env overrides first, then the built-in constants."""
        return cls(
            client_id=os.environ.get(ENV_CLIENT_ID) or BUILTIN_CLIENT_ID,
            client_secret=os.environ.get(ENV_CLIENT_SECRET) or BUILTIN_CLIENT_SECRET,
        )


class YouTubeConnection(BaseModel):
    """What ``youtube.json`` holds. The refresh token is the secret; the
    channel fields are for display and are refreshed on every login."""

    schema_version: int = user_config.SCHEMA_VERSION
    refresh_token: str
    channel_id: str
    channel_title: str
    connected_at: datetime
    scopes: list[str] = Field(default_factory=lambda: [SCOPE])


def _connection_path() -> Path:
    return user_config.user_config_dir() / CONNECTION_FILENAME


def load_connection() -> YouTubeConnection | None:
    """The stored connection, or ``None`` when there is none, the file is
    malformed, or user config is disabled. Never raises."""
    if user_config.is_disabled():
        return None
    raw = user_config._read_json(_connection_path())
    if not isinstance(raw, dict):
        return None
    try:
        return YouTubeConnection.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 -- a bad file reads as "not connected"
        logger.warning("Discarding malformed %s: %s", CONNECTION_FILENAME, exc)
        return None


def save_connection(conn: YouTubeConnection) -> None:
    target = user_config._ensure_dir()
    if target is None:
        return
    conn = conn.model_copy(update={"schema_version": user_config.SCHEMA_VERSION})
    payload = conn.model_dump_json(indent=2)
    try:
        user_config._atomic_write_text(target / CONNECTION_FILENAME, payload)
    except OSError as exc:
        logger.warning("Could not write %s: %s", CONNECTION_FILENAME, exc)


def clear_connection() -> None:
    if user_config.is_disabled():
        return
    try:
        _connection_path().unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not delete %s: %s", CONNECTION_FILENAME, exc)
