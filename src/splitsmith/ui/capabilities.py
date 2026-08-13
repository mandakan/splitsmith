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
- ``review``: the phone-triage writes mirrors accept (slices 3-5) plus
  the full stage audit PUT (#631 Task 6), which became safe to expose
  once shots carry a stable id and the sync merge unions their
  membership by it rather than by position.
- ``share_manage``: the match/shares management routes.
- ``comment_write``: posting and self-deleting a timestamped comment on
  the anonymous share surface. Granted only by the ``comment`` share
  scope - never by ``capabilities_for_origin``, because an authenticated
  operator editing their own match has no use for it.
"""

from __future__ import annotations

import re

EDIT = "edit"
REVIEW = "review"
SHARE_MANAGE = "share_manage"
COMMENT_WRITE = "comment_write"


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


# Share-token scopes -> capability sets. 'read' grants nothing; 'comment'
# is the first write-capable scope (the one #779 anticipated).
_SHARE_SCOPE_CAPABILITIES: dict[str, frozenset[str]] = {
    "read": frozenset(),
    "comment": frozenset({COMMENT_WRITE}),
}


def share_scope_capabilities(scope: str | None) -> frozenset[str]:
    """Capability set a share token's scope grants. Unknown scopes get
    nothing - fail closed."""
    return _SHARE_SCOPE_CAPABILITIES.get(scope or "", frozenset())


# The review-writable route shapes, carried over from the retired
# per-slice mirror regexes (server.py) and method-gated exactly as the old
# guard was. Two entries have since widened past what any of those regexes
# held: the by-id coach PATCH and the full audit PUT (#631 shots-as-a-
# synced-entity), both of which a mirror accepts for the same reason the
# slice 3-5 writes are here - they sync back rather than editing in place.
#
# Every entry anchors with ``\A``/``\Z``, not ``^``/``$``: plain ``$``
# also matches just before a single trailing ``\n``, so ``.../audit$``
# reads as "that path, optionally with one trailing newline" rather than
# "one exact path". On an allow-list that direction is the unsafe one -
# the widened form grants REVIEW where the intent is EDIT. Today no such
# string can reach here: ``rest`` is derived from ``request.url.path``,
# and Starlette builds that through ``urllib.parse.urlsplit()``, which
# strips ASCII CR/LF/TAB from a URL unconditionally (CPython hardening,
# bpo-43882), so ``.../audit%0a`` arrives as the plain, legitimately
# exempt ``.../audit``. That makes ``\Z`` defensive rather than a fix --
# but this table is now a standalone authorization surface that neither
# sees nor controls the caller that builds ``rest``, so it should not
# rest on an implementation detail of one of them.
#
# Containment for ``[^/]+`` / ``by-id/[A-Za-z0-9._-]+`` is not this
# table's job: the character classes only keep a slug or id out of the
# next path segment. What bounds a slug to a real shooter is the
# roster-membership check in ``state.shooter_root`` - a slug these
# patterns pass but the roster doesn't recognize 404s there.
_REVIEW_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"\Amatch/beep-queue/confirm\Z")),
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/\d+/videos/[^/]+/beep\Z")),
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/\d+/(audit/accept|attention)\Z")),
    # Both the by-number and the by-id coach PATCH (#631 Task 3). The id
    # form is the one a client that did not just write the document should
    # use - ``shot_number`` renumbers under it on any insert or delete.
    ("PATCH", re.compile(r"\Ashooters/[^/]+/stages/\d+/shots/(?:\d+|by-id/[A-Za-z0-9._-]+)/coach\Z")),
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/\d+/coach/reclassify\Z")),
    # The full stage audit PUT (#631 Task 6). Safe now that shots carry a
    # stable id and sync/merge.py merges their membership by it - before
    # the merge unit shipped, opening this would have let a desktop pull
    # silently discard phone edits. ``server._may_mint_shot_ids`` refuses
    # to mint a non-convergent id on a mirror at that save boundary; this
    # entry is what makes the path reachable at all.
    ("PUT", re.compile(r"\Ashooters/[^/]+/stages/\d+/audit\Z")),
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
