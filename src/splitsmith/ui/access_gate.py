"""Account-feature gate for hosted routes (spec 2026-10-03).

Routes declare ``Depends(require_feature(Feature.x))``. Local mode is a
no-op: there are no accounts and every feature is on. The job backend's
``submit_allowed`` predicate is the backstop; this dependency exists so
the common entry points answer with a clean, explainable error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from ..access import ALL_FEATURES, Feature, features_for
from ..auth import User


def features_of(request: Request, user: User) -> frozenset[Feature]:
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        return ALL_FEATURES
    state = request.app.state.splitsmith_state
    return features_for(user.access_tier, user.email, state.access, state.admin_emails)


def require_feature(feature: Feature) -> Callable[[Request], Awaitable[None]]:
    async def _dependency(request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if feature not in features_of(request, user):
            raise HTTPException(
                status_code=403, detail={"code": "feature_required", "feature": feature.value}
            )

    return _dependency
