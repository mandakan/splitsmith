"""Share-request scope context + read-only enforcement helpers (#779).

The share alias middleware records the resolved token's scope here; the
engine's after_begin listener and the state stores consult it. Lives in
the db layer so the engine and stores can import it without reaching
into the UI server module.
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


def share_request_is_read_only() -> bool:
    """True when the current request is a share request whose scope
    grants no writes.

    Keyed off the scope rather than mere share-ness so that write-scoped
    tokens later skip both the READ ONLY transaction and the store
    guard without touching this module's callers.
    """
    return current_share_scope.get() == "read"
