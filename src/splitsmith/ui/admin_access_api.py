"""Hosted-only ``/api/admin`` routes for access requests and account tiers
(spec 2026-10-03, "Admin API").

Same gate as ``/api/admin/workers``: 404 outside hosted mode, 403 for a
signed-in user whose email is not in ``SPLITSMITH_ADMIN_EMAILS``.

The db imports stay inside the functions so a slim local install (no
``hosted`` extra) still imports this module and registers the router;
every route just 404s there. That is also why the response models below
are declared here rather than reusing the db layer's views: a module-level
import of ``splitsmith.db.access_requests`` would pull sqlalchemy into the
local ``create_app`` path.

The stores write a tier raw; the registry check lives here, on approve and
on the account PATCH alike, so an unknown tier is a 422 before anything is
written.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AdminAccessRequest(BaseModel):
    """Wire shape of ``db.access_requests.AccessRequestView`` (field set
    pinned by ``test_wire_models_match_the_db_views``)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    note: str | None
    source: str
    status: str
    requested_at: datetime
    last_requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    tier_granted: str | None
    email_sent_at: datetime | None


class AdminAccountView(BaseModel):
    """``db.access_requests.AccountView`` plus whether the account is an
    env admin (an env admin has every feature whatever its tier)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    display_name: str | None
    access_tier: str
    created_at: datetime
    is_admin: bool


class TierInfo(BaseModel):
    name: str
    features: list[str]


class TiersResponse(BaseModel):
    tiers: list[TierInfo]
    default_tier: str


class ApproveBody(BaseModel):
    tier: str


class SetTierBody(BaseModel):
    access_tier: str


# ---------------------------------------------------------------------------
# Gate and helpers
# ---------------------------------------------------------------------------


def _state(request: Request) -> Any:
    return request.app.state.splitsmith_state


def _admin_gate(request: Request) -> None:
    """404 outside hosted mode, 403 unless the caller is an env admin."""
    from .server import _hosted_mode_active

    state = _state(request)
    if not _hosted_mode_active() or state.access_requests is None or state.accounts is None:
        raise HTTPException(status_code=404, detail="not found")
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if user.email.lower() not in state.admin_emails:
        raise HTTPException(status_code=403, detail="admin access required")


router = APIRouter(prefix="/api/admin", dependencies=[Depends(_admin_gate)])


def _require_known_tier(state: Any, tier: str) -> None:
    if tier not in state.access.tiers:
        raise HTTPException(status_code=422, detail=f"unknown tier: {tier}")


def _request_out(view: Any) -> AdminAccessRequest:
    return AdminAccessRequest.model_validate(view.model_dump())


def _account_out(state: Any, view: Any) -> AdminAccountView:
    return AdminAccountView(**view.model_dump(), is_admin=view.email.lower() in state.admin_emails)


async def _send_granted(state: Any, view: Any) -> AdminAccessRequest:
    """Mint a fresh sign-in link and send the "you're in" mail. A failure is
    logged and leaves ``email_sent_at`` null; the decision stands and
    resend retries it."""
    try:
        link = await state.auth.backends[0].mint_link(view.email, base_url=state.public_base_url)
        await state.email_sender.send_access_granted(to=view.email, link=link)
    except Exception:
        logger.exception("access granted mail failed")
        return _request_out(await state.access_requests.get(view.id))
    await state.access_requests.mark_email_sent(view.id)
    return _request_out(await state.access_requests.get(view.id))


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


@router.get("/access-requests", response_model=list[AdminAccessRequest])
async def list_access_requests(
    request: Request, status: Literal["pending", "approved", "declined"] | None = None
) -> list[AdminAccessRequest]:
    """Every request, pending first; ``?status=`` narrows to one status."""
    views = await _state(request).access_requests.list(status=status)
    return [_request_out(v) for v in views]


@router.post("/access-requests/{request_id}/approve", response_model=AdminAccessRequest)
async def approve_access_request(request: Request, request_id: str, body: ApproveBody) -> AdminAccessRequest:
    from ..db.access_requests import AlreadyDecidedError, NotFoundError

    state = _state(request)
    _require_known_tier(state, body.tier)
    try:
        view = await state.access_requests.approve(
            request_id, tier=body.tier, admin_email=request.state.user.email.lower()
        )
    except NotFoundError as exc:
        # The store raises NotFoundError both for an unknown id and for a
        # request whose email belongs to a soft-deleted account; the
        # request row existing tells the two apart.
        try:
            await state.access_requests.get(request_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found") from exc
        raise HTTPException(status_code=404, detail="account deleted") from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail="already decided") from exc
    return await _send_granted(state, view)


@router.post("/access-requests/{request_id}/decline", response_model=AdminAccessRequest)
async def decline_access_request(request: Request, request_id: str) -> AdminAccessRequest:
    from ..db.access_requests import AlreadyDecidedError, NotFoundError

    state = _state(request)
    try:
        view = await state.access_requests.decline(request_id, admin_email=request.state.user.email.lower())
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail="already decided") from exc
    return _request_out(view)


@router.post("/access-requests/{request_id}/resend", response_model=AdminAccessRequest)
async def resend_access_granted(request: Request, request_id: str) -> AdminAccessRequest:
    """Mint a fresh link and resend the "you're in" mail; only an approved
    request has one to resend."""
    from ..db.access_requests import NotFoundError

    state = _state(request)
    try:
        view = await state.access_requests.get(request_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    if view.status != "approved":
        raise HTTPException(status_code=409, detail="not approved")
    return await _send_granted(state, view)


# ---------------------------------------------------------------------------
# Accounts and the tier registry
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[AdminAccountView])
async def list_users(request: Request) -> list[AdminAccountView]:
    state = _state(request)
    return [_account_out(state, v) for v in await state.accounts.list()]


@router.patch("/users/{user_id}", response_model=AdminAccountView)
async def set_user_tier(request: Request, user_id: str, body: SetTierBody) -> AdminAccountView:
    """Change an account's tier. An env admin's tier can change too; it has
    no effect on what they can do."""
    from ..db.access_requests import NotFoundError

    state = _state(request)
    _require_known_tier(state, body.access_tier)
    try:
        view = await state.accounts.set_tier(user_id, body.access_tier)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    return _account_out(state, view)


@router.get("/access-tiers", response_model=TiersResponse)
async def list_access_tiers(request: Request) -> TiersResponse:
    """The tier registry, for the admin tier picker."""
    access = _state(request).access
    return TiersResponse(
        tiers=[
            TierInfo(name=name, features=sorted(f.value for f in features))
            for name, features in access.tiers.items()
        ],
        default_tier=access.default_tier,
    )
