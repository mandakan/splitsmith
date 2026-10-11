"""The lab's token gate for a server bound beyond loopback.

The lab serves any file a path names and writes fixture files, so a lab
listening on the home network must answer only the phone holding its token:
``?lab_token=`` once (answered with a cookie and a redirect to the same URL
without it), the cookie after that. Everything else is the same 404 as an
unknown route, so the server does not even admit to being there.

The cookie is ``SameSite=Lax``, not ``Strict``: opening a bookmark or a link
from another app is a cross-site navigation, and Strict would drop the
cookie there. The lab has no state-changing GET for Lax to expose.
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

COOKIE = "splitsmith_lab_token"
PARAM = "lab_token"
# A year: the token itself is what gets rotated (review_session.py --new-token).
_MAX_AGE = 365 * 24 * 3600


def is_loopback(host: str) -> bool:
    """Whether ``host`` is reachable from this machine only."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_middleware(
    token: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """An ``http`` middleware admitting only requests that carry ``token``."""
    expected = token.encode()

    def _ok(value: str | None) -> bool:
        return value is not None and hmac.compare_digest(value.encode(), expected)

    async def lab_token_gate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        offered = request.query_params.get(PARAM)
        if offered is not None:
            if not _ok(offered):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != PARAM]
            target = request.url.path + (f"?{urlencode(rest)}" if rest else "")
            resp = RedirectResponse(target, status_code=303)
            resp.set_cookie(COOKIE, token, max_age=_MAX_AGE, httponly=True, samesite="lax")
            return resp
        if not _ok(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    return lab_token_gate
