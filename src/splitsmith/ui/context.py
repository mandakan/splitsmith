"""Per-request and per-job context for the UI server.

The context variables the alias middleware, the auth gate, the share alias
and the queue task pin for one request or job, the tenant they resolve,
and the one switch that picks hosted mode. ``server.py`` re-exports every
name here, so callers outside ``ui/`` keep importing from it; modules
inside ``ui/`` import from here and no longer reach back into ``server``.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .. import account_profile as account_profile_module
from .. import export_presets as export_presets_module
from .. import shooter_book as shooter_book_module
from .. import user_config
from .. import whats_new as whats_new_module
from ..auth import User
from ..look import store as look_store_module
from ..storage import Storage

if TYPE_CHECKING:
    # Hosted-only; imported at runtime inside server._apply_hosted_mode_wiring
    # so local mode stays free of the db (procrastinate/psycopg) dependency.
    from ..db import (
        PostgresMatchStore,
        PostgresProfileStore,
        PostgresYouTubeConnectionStore,
        ProjectStateStore,
    )
    from ..db.comments import CommentStore
    from ..db.desktop_commands import DesktopCommandStore
    from ..db.desktop_tokens import DesktopTokenStore
    from ..db.share_tokens import ShareTokenStore
    from .jobs import JobBackend


SPLITSMITH_MODE_ENV = "SPLITSMITH_MODE"

# Public origin the deployment is reached at (e.g. https://splitsmith.app,
# or http://localhost:5174 for docker-compose). Required in hosted mode: it
# is the base of the magic-link callback URL and decides the session
# cookie's Secure flag (Secure iff the scheme is https).
SPLITSMITH_PUBLIC_URL_ENV = "SPLITSMITH_PUBLIC_URL"


def _hosted_mode_active() -> bool:
    """Return True when ``SPLITSMITH_MODE=hosted`` is in the environment.

    The single switch that picks Postgres-backed stores + the hosted
    auth bootstrap over the local file-system defaults. Local-mode
    (``splitsmith ui``) leaves the env var unset; ``splitsmith serve``
    sets it before constructing the app.
    """
    return os.environ.get(SPLITSMITH_MODE_ENV, "").strip().lower() == "hosted"


# Per-request match root resolved by the ``/api/matches/{match_id}/...``
# alias middleware (#353 Phase 3 PR C). When set, ``AppState.shooter_root``
# reads from here instead of the process singleton -- which means two
# concurrent requests carrying different match ids resolve to their own
# match roots without fighting over ``state._bound_root``. The singleton
# is kept as a fallback for legacy bare-path traffic + picker UX; it can
# go away entirely once every endpoint is exercised under the new prefix.
current_match_root: ContextVar[Path | None] = ContextVar("splitsmith_current_match_root", default=None)
current_match_id: ContextVar[str | None] = ContextVar("splitsmith_current_match_id", default=None)
# The bound match's origin ("hosted" | "desktop" | "local"), set by the
# alias middleware from the same row it already fetched for the ownership
# check (#631 Task 6) - so handlers that need to expose origin (e.g. the
# shooter list) don't issue a second matches_store query. ``None`` outside
# an aliased request (e.g. legacy bare-path traffic).
current_match_origin: ContextVar[str | None] = ContextVar("splitsmith_current_match_origin", default=None)
# The bound match's capability set (#756), computed by the alias
# middleware from the same origin fact (or, on share requests, from the
# token's scope) that decides the 403 - handlers serialize it so the SPA
# gates affordances on the same truth the guard enforces. None outside
# an aliased request.
current_match_capabilities: ContextVar[frozenset[str] | None] = ContextVar(
    "splitsmith_current_match_capabilities", default=None
)
# Set True by _share_alias for anonymous read requests so handlers can
# strip server-local fields (e.g. match_root, last_scanned_dir) from their
# response payloads before returning them to anonymous viewers.
current_share_request: ContextVar[bool] = ContextVar("splitsmith_current_share_request", default=False)
# The share link's own starting cameras (``ShareTokenRow.cameras``), set by
# ``_share_alias`` beside ``current_share_request``. The coach payload
# hands a shooter's entry to the viewer's pages as ``compare_camera``.
current_share_cameras: ContextVar[dict[str, str] | None] = ContextVar(
    "splitsmith_current_share_cameras", default=None
)

# The signed-in user behind a share request, or None. Set by
# _share_alias purely so a comment can carry an account name instead of
# a generated handle.
#
# It grants NOTHING. The tenant stays the match owner's, the allowlist
# does not widen, and the scope gate does not soften. If a future change
# makes this value authorize anything, that is the bug - the token is
# the authorization on this surface, and a second one would mean two
# answers to "who may do this".
current_share_viewer: ContextVar[User | None] = ContextVar("splitsmith_current_share_viewer", default=None)


@dataclass
class TenantContext:
    """The per-user stores resolved for the lifetime of one request / job.

    In hosted mode every ``/api/*`` request and every worker job runs as a
    distinct user, so the per-user stores cannot be process singletons --
    a singleton bound to user A would serve user B's request with A's
    Row-Level-Security GUC. The hosted wiring builds one of these per
    request (in the auth-gate middleware, from the resolved ``User``) and
    per job (in the queue task, from the queued ``user_id``); the matching
    :class:`AppState` properties read it via the :data:`current_tenant`
    ContextVar. In local mode there is exactly one operator, so this is
    never populated and the properties fall back to the process singletons.
    """

    user_id: str
    recent_projects: user_config.RecentProjectsStore
    scoreboard_identity: user_config.ScoreboardIdentityStore
    jobs: JobBackend
    matches_store: PostgresMatchStore | None
    project_state: ProjectStateStore | None
    storage: Storage | None
    # Per-user owner-scoped share-token store (public share links, issue #349).
    # None in local mode; in hosted mode built per-request with the tenant
    # factory so write operations filter on this user's rows. share_tokens is
    # not under RLS - isolation is by explicit user_id predicates on every
    # query - but the tenant factory is still correct here because the GUC is
    # inert on a non-RLS table and the store filters by user_id explicitly.
    share_tokens: ShareTokenStore | None
    # Per-user desktop-sync bearer token store (desktop-to-hosted sync MVP,
    # #631). None in local mode; in hosted mode built per-request with the
    # tenant factory, same rationale as share_tokens directly above -
    # desktop_tokens is not under RLS either (see DesktopTokenRow), so the
    # store's own user_id filter is the real isolation boundary, but the
    # tenant factory is still the established construction pattern here.
    desktop_tokens: DesktopTokenStore | None
    # Per-user public comment-thread store (timestamped comments). None in
    # local mode; in hosted mode built per-request with the tenant factory.
    # Unlike share_tokens and desktop_tokens directly above, match_comments
    # IS under the tenant_isolation RLS policy (Task 1's migration) - so
    # here the tenant factory is load-bearing, not merely the established
    # pattern: it is what sets the ``app.user_id`` GUC the policy keys on.
    comments: CommentStore | None
    # Per-user account-profile writer (#867). None in local mode: the
    # loopback sentinel has no user row, and the PATCH route that uses
    # this 404s there.
    profile: PostgresProfileStore | None = None
    # Per-user saved export presets (spec 2026-09-15 s1). ``None`` in
    # local mode, where ``AppState.export_presets`` falls back to the
    # JSON file store.
    export_presets: export_presets_module.ExportPresetStore | None = None
    # The account's YouTube connection (issue #1000, phase 2). ``None`` in
    # local mode, where ``youtube_api`` reads the file under the user
    # config dir instead.
    youtube: PostgresYouTubeConnectionStore | None = None
    # The account's shooter book and profile (spec 2026-10-08). Hosted builds
    # one per tenant; ``AppState`` never falls back to the local files for a
    # hosted request, so a container's own disk is never read as an account.
    shooter_book: shooter_book_module.ShooterBookStore | None = None
    account_profile: account_profile_module.AccountProfileStore | None = None
    # The desktop command queue (#1100): requests the phone makes for the
    # user's desktop to run. ``None`` in local mode; under the
    # ``tenant_isolation`` RLS policy, so the tenant factory is load-bearing.
    desktop_commands: DesktopCommandStore | None = None
    # The account's saved Looks (#1263): manifests only, drawn by the
    # shipped templates. ``None`` in local mode, where ``AppState.looks``
    # is the Looks folder.
    looks: look_store_module.HostedLookStore | None = None
    # The What's new ids the account has seen. ``None`` in local mode, where
    # ``AppState.whats_new`` is the prefs file.
    whats_new: whats_new_module.WhatsNewStore | None = None


# Per-request / per-job tenant resolved by the hosted-mode auth gate
# (API) and the ``run_compute_job`` queue task (worker). When set, the
# per-user ``AppState`` properties (``jobs``, ``recent_projects``,
# ``scoreboard_identity``, ``matches_store``, ``storage``) read from it
# instead of the local-mode singleton -- the same pattern
# ``current_match_root`` uses for match scoping. ``None`` in local mode
# and on boot/non-request code paths.
current_tenant: ContextVar[TenantContext | None] = ContextVar("splitsmith_current_tenant", default=None)
