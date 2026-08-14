"""Hosted-only ``/api/device/*`` routes: the browser-assisted device flow
(#719, design doc 2026-08-07).

Six routes across three auth boundaries:

- ``authorize`` / ``token`` are **public** (both in ``_PUBLIC_API_PATHS``).
  The desktop install has no cookie jar and no bearer yet; the device code
  in the request is the authorization, and an unknown one is answered
  identically to an expired one so nothing can be probed for.
- The three ``pending`` routes need a **session cookie**: they are the
  approval screen and its two buttons, driven by a signed-in browser.
- ``DELETE /session`` needs the **sync bearer** and is the one route the
  scope gate lets a sync-scoped token reach outside ``/api/sync/*`` - it
  is how the local UI unlinks without holding a cookie.

Local mode has no accounts to authorize against, so every route 404s
there, same guard idiom as ``sync_api.py`` and the desktop-token
management routes. Imports of the db layer stay inside the functions for
the same reason they do there: the local-slim wheel imports this module
and must not pull sqlalchemy in with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

if TYPE_CHECKING:
    from ..db.device_auth import DeviceAuthStore

router = APIRouter(prefix="/api/device")

#: Where the SPA renders the approval screen. Kept here rather than
#: inlined so the two URL builders below cannot drift apart.
_APPROVE_PATH = "/desktop/approve"


class DeviceAuthorizeRequest(BaseModel):
    """Body for ``POST /api/device/authorize``."""

    device_name: str


class DeviceAuthorizeResponse(BaseModel):
    """Response for ``POST /api/device/authorize``.

    ``device_code`` appears here and only here; the row keeps its hash.
    ``verification_uri_complete`` is the prefilled approval screen the
    desktop UI opens in the operator's own browser - which is what makes
    the remote-host topology work, since the SPA runs where the operator
    is even when the server does not.
    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceTokenRequest(BaseModel):
    """Body for ``POST /api/device/token``."""

    device_code: str


class DeviceAccountInfo(BaseModel):
    """The linked account, as the desktop install will cache it."""

    id: str
    email: str
    display_name: str | None = None


class DeviceTokenResponse(BaseModel):
    """One poll verdict. Only ``approved`` carries a credential, once."""

    status: str
    token: str | None = None
    account: DeviceAccountInfo | None = None
    device_name: str | None = None


class DevicePendingResponse(BaseModel):
    """What the approval screen shows."""

    user_code: str
    device_name: str
    scope: str
    created_at: str
    expires_at: str


class DeviceDecisionResponse(BaseModel):
    """Result of approve / deny."""

    approved: bool


class DeviceSessionResponse(BaseModel):
    """Result of ``DELETE /api/device/session``."""

    revoked: bool


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode. Lazy import, same as sync_api."""
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _store(request: Request) -> DeviceAuthStore:
    store = request.app.state.splitsmith_state.device_auth
    if store is None:
        raise HTTPException(status_code=500, detail="device auth store unavailable")
    return store


def _current_user(request: Request) -> Any:
    """The user ``_auth_gate`` already resolved. The three session routes
    sit behind that gate, so an anonymous caller 401s before arriving."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def _public_base(request: Request) -> str:
    """The origin to build verification URLs against.

    ``SPLITSMITH_PUBLIC_URL`` is what the magic-link mailer already uses,
    so the approve link and the sign-in link the operator may need first
    always point at the same host.
    """
    import os

    from .server import SPLITSMITH_PUBLIC_URL_ENV

    configured = os.environ.get(SPLITSMITH_PUBLIC_URL_ENV, "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.post("/authorize", response_model=DeviceAuthorizeResponse)
async def authorize_device(body: DeviceAuthorizeRequest, request: Request) -> DeviceAuthorizeResponse:
    """Start a device authorization. Public - there is no credential yet."""
    _hosted_gate()
    req = await _store(request).authorize(body.device_name.strip() or "desktop")
    base = _public_base(request)
    return DeviceAuthorizeResponse(
        device_code=req.device_code,
        user_code=req.user_code,
        verification_uri=f"{base}{_APPROVE_PATH}",
        verification_uri_complete=f"{base}{_APPROVE_PATH}?code={req.user_code}",
        expires_in=req.expires_in,
        interval=req.interval,
    )


@router.post("/token", response_model=DeviceTokenResponse)
async def poll_device_token(body: DeviceTokenRequest, request: Request) -> DeviceTokenResponse:
    """Poll for the outcome. Public - the device code is the credential.

    Always 200: the verdict is in the body. An unknown device code, a
    consumed one and an expired one all report ``expired``, so a caller
    cannot use the status code to learn which codes exist.
    """
    _hosted_gate()
    result = await _store(request).poll(body.device_code)
    account = (
        DeviceAccountInfo(
            id=result.account.id,
            email=result.account.email,
            # Live read, not a cache - this is where the account's current
            # display_name crosses from DB state onto the wire. The desktop
            # client caches this response (see server.py's
            # get_device_status, ``prefs.hosted_account = ...``), and since
            # #877 that cache refreshes itself from /api/sync/whoami rather
            # than holding whatever this poll happened to return. Which
            # matters here: at this moment display_name is usually still
            # NULL, because an account sets its name after linking.
            display_name=result.account.display_name,
        )
        if result.account is not None
        else None
    )
    return DeviceTokenResponse(
        status=result.status,
        token=result.token,
        account=account,
        device_name=result.device_name,
    )


@router.get("/pending/{user_code}", response_model=DevicePendingResponse)
async def get_pending_device(user_code: str, request: Request) -> DevicePendingResponse:
    """Data for the approval screen. Session cookie required."""
    _hosted_gate()
    _current_user(request)
    pending = await _store(request).pending(user_code)
    if pending is None:
        raise HTTPException(status_code=404, detail="not found")
    return DevicePendingResponse(
        user_code=pending.user_code,
        device_name=pending.device_name,
        scope=pending.scope,
        created_at=pending.created_at.isoformat(),
        expires_at=pending.expires_at.isoformat(),
    )


@router.post("/pending/{user_code}/approve", response_model=DeviceDecisionResponse)
async def approve_pending_device(user_code: str, request: Request) -> DeviceDecisionResponse:
    """Approve. Records status + the approving user; mints nothing."""
    _hosted_gate()
    user = _current_user(request)
    ok = await _store(request).decide(user_code, user_id=user.id, approved=True)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return DeviceDecisionResponse(approved=True)


@router.post("/pending/{user_code}/deny", response_model=DeviceDecisionResponse)
async def deny_pending_device(user_code: str, request: Request) -> DeviceDecisionResponse:
    """Deny. The polling install gets a distinct terminal verdict."""
    _hosted_gate()
    user = _current_user(request)
    ok = await _store(request).decide(user_code, user_id=user.id, approved=False)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return DeviceDecisionResponse(approved=False)


@router.delete("/session", response_model=DeviceSessionResponse)
async def delete_device_session(request: Request) -> DeviceSessionResponse:
    """Revoke the calling token's own row.

    The one route the scope gate lets a sync-scoped token reach outside
    ``/api/sync/*``. Reads the bearer straight off the header rather than
    from the resolved user: the row to revoke is the credential that was
    presented, not every credential that user holds.
    """
    _hosted_gate()
    _current_user(request)
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    token = bearer.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=400, detail="a bearer token is required")
    revoked = await _store(request).revoke_token(token)
    return DeviceSessionResponse(revoked=revoked)
