"""Match capability model (#756) + share-scope mapping (#779).

One encoding of who may write what. The alias middleware consults
``required_capability`` for its 403 and serializes the computed set on
match payloads, so enforcement and what the SPA renders can never
disagree - the same single-source rationale that put
READ_ONLY_MIRROR_MESSAGE in one constant.

Capabilities:

- ``edit``: the full mutation surface (trims, stages, shooters, ingest,
  exports). The default requirement for any write route not classified
  below - new write routes fail over-restricted, never silently
  writable.
- ``review``: the phone-triage writes mirrors accept (slices 3-5).
- ``share_manage``: the match/shares management routes.
"""

from __future__ import annotations

import re

EDIT = "edit"
REVIEW = "review"
SHARE_MANAGE = "share_manage"


def capabilities_for_origin(origin: str | None) -> frozenset[str]:
    """Capability set of an authenticated (non-share) request, derived
    from where the match's canonical data lives. Today origin fully
    determines writability; when the #631 transfer endgame lands, this
    function keys off sync state instead and no caller changes."""
    if origin == "desktop":
        # A mirror desktop still owns: review actions sync back, editing
        # stays on desktop. Share management is the point of exposing
        # the mirror hosted-side.
        return frozenset({REVIEW, SHARE_MANAGE})
    if origin == "hosted":
        return frozenset({EDIT, REVIEW, SHARE_MANAGE})
    # "local" and None (legacy bare-path local traffic): one operator,
    # full control, no share surface to manage.
    return frozenset({EDIT, REVIEW})


# Share-token scopes -> capability sets. 'read' is the only scope shipped
# (#779); a write-capable scope (e.g. 'coach') is one new entry here.
_SHARE_SCOPE_CAPABILITIES: dict[str, frozenset[str]] = {
    "read": frozenset(),
}


def share_scope_capabilities(scope: str | None) -> frozenset[str]:
    """Capability set a share token's scope grants. Unknown scopes get
    nothing - fail closed."""
    return _SHARE_SCOPE_CAPABILITIES.get(scope or "", frozenset())


# The review-writable route shapes, verbatim from the retired per-slice
# mirror regexes (server.py) - method-gated exactly as the old guard was.
_REVIEW_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^match/beep-queue/confirm$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/videos/[^/]+/beep$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/(audit/accept|attention)$")),
    ("PATCH", re.compile(r"^shooters/[^/]+/stages/\d+/shots/\d+/coach$")),
    ("POST", re.compile(r"^shooters/[^/]+/stages/\d+/coach/reclassify$")),
)


def required_capability(method: str, rest: str) -> str | None:
    """Capability a request needs, or None for safe methods.

    ``rest`` is the alias-relative path (what follows
    ``/api/matches/{id}/``), the same string the old guard matched.
    """
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    if rest == "match/shares" or rest.startswith("match/shares/"):
        return SHARE_MANAGE
    for allowed_method, pattern in _REVIEW_ROUTES:
        if method == allowed_method and pattern.match(rest) is not None:
            return REVIEW
    return EDIT
