"""Online ``ScoreboardClient`` -- talks to ``scoreboard.urdr.dev/api/v1/``.

Pairs with ``LocalJsonScoreboard`` (issue #48): the same ``MatchData``
shape comes out either way, so the UI consumes only the
``ScoreboardClient`` Protocol and never branches on the source.

The HTTP client is intentionally thin: no caching here. Caching lives in
``CachingScoreboardClient`` so it can be project-local and travel with
the project directory (issue #14 acceptance criterion). Wrap this client
in the cache decorator at construction time, not inside the HTTP layer.

Auth: bearer token from ``SPLITSMITH_SSI_TOKEN`` (or the ``token=``
constructor arg). Missing token raises ``ScoreboardAuthError`` at
construction so the UI can surface a clear setup message instead of
hitting a 401 on first request.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from splitsmith.ui.scoreboard.models import (
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
)

DEFAULT_BASE_URL = "https://scoreboard.urdr.dev/api/v1"
TOKEN_ENV_VAR = "SPLITSMITH_SSI_TOKEN"
API_DOCS_URL = "https://github.com/mandakan/ssi-scoreboard/blob/main/docs/api-v1.md"


class ScoreboardError(RuntimeError):
    """Base class for ``SsiHttpClient`` errors."""


class ScoreboardAuthError(ScoreboardError):
    """Token missing or rejected by the server (401)."""


class ScoreboardRateLimited(ScoreboardError):
    """Server returned 429. ``retry_after`` is seconds if the header was set."""

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ScoreboardUpstreamError(ScoreboardError):
    """5xx from the upstream API; offline JSON path is the suggested fallback."""


class MatchNotFound(ScoreboardError):
    """``GET /api/v1/match/{ct}/{id}`` returned 404."""


class ShooterNotFound(ScoreboardError):
    """``GET /api/v1/shooter/{shooterId}`` returned 404."""


class SsiHttpClient:
    """``ScoreboardClient`` backed by the live ``/api/v1/`` HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        timeout: float = 10.0,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_token = token if token is not None else os.environ.get(TOKEN_ENV_VAR)
        if not resolved_token:
            raise ScoreboardAuthError(
                f"{TOKEN_ENV_VAR} is not set; obtain a bearer token and export it. "
                f"See {API_DOCS_URL}"
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {resolved_token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SsiHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def search_matches(self, query: str) -> list[MatchRef]:
        data = self._get_json("/events", params={"q": query} if query else None)
        if not isinstance(data, list):
            raise ScoreboardUpstreamError(
                f"unexpected /events response shape: {type(data).__name__}"
            )
        return [MatchRef.model_validate(item) for item in data]

    def get_match(self, content_type: int, match_id: int) -> MatchData:
        try:
            data = self._get_json(f"/match/{content_type}/{match_id}")
        except _NotFound as exc:
            raise MatchNotFound(f"match {content_type}/{match_id} not found on scoreboard") from exc
        return MatchData.model_validate(data)

    def find_shooter(self, name: str) -> list[ShooterRef]:
        data = self._get_json("/shooter/search", params={"q": name})
        if not isinstance(data, list):
            raise ScoreboardUpstreamError(
                f"unexpected /shooter/search response shape: {type(data).__name__}"
            )
        return [ShooterRef.model_validate(item) for item in data]

    def get_shooter(self, shooter_id: int) -> ShooterDashboard:
        try:
            data = self._get_json(f"/shooter/{shooter_id}")
        except _NotFound as exc:
            raise ShooterNotFound(f"shooter {shooter_id} not found on scoreboard") from exc
        return ShooterDashboard.model_validate(data)

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ScoreboardUpstreamError(
                f"network error talking to scoreboard ({exc.__class__.__name__}); "
                "try the offline JSON path"
            ) from exc

        status = response.status_code
        if 200 <= status < 300:
            return response.json()
        if status == 401:
            raise ScoreboardAuthError(
                f"scoreboard rejected the bearer token (401). "
                f"Set {TOKEN_ENV_VAR}; see {API_DOCS_URL}"
            )
        if status == 404:
            raise _NotFound(path)
        if status == 429:
            raw = response.headers.get("Retry-After")
            retry_after: float | None
            try:
                retry_after = float(raw) if raw is not None else None
            except ValueError:
                retry_after = None
            wait_hint = f" (retry after {retry_after:g}s)" if retry_after is not None else ""
            raise ScoreboardRateLimited(
                f"scoreboard rate limit hit{wait_hint}",
                retry_after=retry_after,
            )
        if 500 <= status < 600:
            raise ScoreboardUpstreamError(
                f"scoreboard upstream returned {status}; try the offline JSON path"
            )
        raise ScoreboardError(f"unexpected scoreboard response: {status} {response.text[:200]}")


class _NotFound(Exception):
    """Internal: 404 marker so ``get_match``/``get_shooter`` can map to typed errors."""
