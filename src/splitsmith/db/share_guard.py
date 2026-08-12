"""Share-request scope context + read-only enforcement helpers (#779).

The share alias middleware records the resolved token's scope here; the
engine's after_begin listener and the state stores consult it. Lives in
the db layer so the engine and stores can import it without reaching
into the UI server module.

The read-only enforcement covers Postgres state only - object-storage
writes from share paths (e.g. the OG card PNG cache) are governed by
their token-scoped storage keys, and code bypassing the tenant session
factory (e.g. the procrastinate connector) is outside this listener.
"""

from __future__ import annotations

from contextvars import ContextVar

# Scope of the share token authorizing the current request, or None
# outside a share request. "read" is the only scope shipped today; a
# write-capable scope added later (e.g. "coach") simply stops matching
# the read-only check below and the capability table decides what it
# may write.
current_share_scope: ContextVar[str | None] = ContextVar("splitsmith_current_share_scope", default=None)


class ShareReadOnlyError(RuntimeError):
    """A mutation was attempted while serving a read-scoped share request.

    Always a bug: a share-whitelisted route grew a write side effect.
    Surfaces as a 500 by design - loud beats silent anonymous writes.
    """


# Scopes allowed to write through share auth. Empty today - the coach
# chunk adds entries here (and the capability table decides what they
# may write). Any scope NOT in this set is treated as read-only, so an
# unknown or mistyped scope fails closed instead of silently skipping
# every defense layer.
_WRITE_CAPABLE_SCOPES: frozenset[str] = frozenset()


def share_request_is_read_only() -> bool:
    """True when the current request is a share request whose scope
    grants no writes.

    Fails closed: any scope outside _WRITE_CAPABLE_SCOPES - including
    unknown values - is read-only. A write-capable scope added later
    joins the set without touching this module's callers.
    """
    scope = current_share_scope.get()
    return scope is not None and scope not in _WRITE_CAPABLE_SCOPES
