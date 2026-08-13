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
- ``comment_write``: posting a timestamped comment (share surface,
  ``comment`` scope only) and deleting one (share surface via the
  ``comment`` scope, or an authenticated owner via ``capabilities_for_origin``
  on ``desktop``/``hosted`` origin, moderating a comment someone else
  posted through the same DELETE route). It also covers the owner's
  bulk-moderation route, ``DELETE match/comments``. ``local`` origin
  never grants it - there is no share surface, so nothing to moderate.

  The capability means two different things depending on where the
  request came from, and the SPA has to keep them apart: on a share
  mount it means "may post", on the owner's own mount it means "may
  moderate" (the owner cannot post - ``create_stage_comment`` requires a
  ``share_token_id``). See ``ResultsStage.tsx``'s ``canComment`` /
  ``canModerate`` pair.
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
        # the mirror hosted-side. Comment moderation (the owner's own
        # DELETE on a comment someone else posted) is the same kind of
        # hosted-side-only action, so it stays here too.
        return frozenset({REVIEW, SHARE_MANAGE, COMMENT_WRITE})
    if origin == "hosted":
        return frozenset({EDIT, REVIEW, SHARE_MANAGE, COMMENT_WRITE})
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


# The comment routes. The first two are the anonymous share surface,
# mapped explicitly rather than falling through to EDIT: a
# comment-scoped token grants only COMMENT_WRITE, and an unmapped route
# would refuse with a 403 among 404s - a discriminator that enumerates
# the write allowlist.
#
# The third, ``DELETE match/comments``, is the owner's bulk-moderation
# route (Task 8). It is here for a different reason: it was falling
# through to the EDIT default, which ``capabilities_for_origin("desktop")``
# does not grant, so both bulk selectors 403'd on exactly the matches
# most likely to have share links - a desktop project mirrored up for
# sharing (final review, I4). Moderating comments is COMMENT_WRITE
# wherever it happens. It is deliberately absent from
# ``server._SHARE_WRITE_ROUTES``, so no share token of any scope can
# reach it; an anonymous caller still gets the uniform 404.
#
# ``[0-9]`` rather than ``\d``: ``\d`` matches Unicode decimal digits the
# route's ``int`` path parameter cannot parse, which leaked a 422 among
# the uniform 404s (final review, I6). See ``server._SHARE_PATH_RE``.
_COMMENT_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"\Ashooters/[^/]+/stages/[0-9]+/comments\Z")),
    ("DELETE", re.compile(r"\Ashooters/[^/]+/stages/[0-9]+/comments/[A-Za-z0-9]+\Z")),
    ("DELETE", re.compile(r"\Amatch/comments\Z")),
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
    for allowed_method, pattern in _COMMENT_ROUTES:
        if method == allowed_method and pattern.match(rest) is not None:
            return COMMENT_WRITE
    for allowed_method, pattern in _REVIEW_ROUTES:
        if method == allowed_method and pattern.match(rest) is not None:
            return REVIEW
    return EDIT
