"""Process state for the UI server.

``AppState`` and the pieces every project loader shares: the bundle of a
match's shooters, the storage scope for derived artifacts, the unbound-
server error and the module constants sibling routers read. ``server.py``
re-exports every name here; modules inside ``ui/`` import from here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from .. import account_profile as account_profile_module
from .. import export_presets as export_presets_module
from .. import export_runs, match_model, user_config
from .. import shooter_book as shooter_book_module
from .. import whats_new as whats_new_module
from ..access import AccessConfig
from ..async_bridge import run_sync
from ..auth import AuthBackend, LoopbackAuth
from ..compute import ComputeBackend, LocalComputeBackend
from ..look import store as look_store_module
from ..match_project import MatchProject
from ..match_registry import MatchRegistry
from ..storage import Storage
from ..sync.reconcile import ReconcileStep
from .account_backfill import local_backfill_source as _local_backfill_source
from .auto_sync import AutoSyncService
from .context import TenantContext, current_match_id, current_match_root, current_tenant
from .docstore import DbDocStore, DocStore, FileDocStore
from .jobs import JobBackend, JobBodyRegistry, JobRegistry

if TYPE_CHECKING:
    # Hosted-only; imported at runtime inside server._apply_hosted_mode_wiring
    # so local mode stays free of the db (procrastinate/psycopg) dependency.
    from ..db import (
        LoopEngines,
        PostgresMatchStore,
        PostgresProfileStore,
        PostgresYouTubeConnectionStore,
        ProjectStateStore,
    )
    from ..db.access_requests import AccessRequestStore, AccountAccessStore
    from ..db.comments import CommentStore
    from ..db.desktop_commands import DesktopCommandStore
    from ..db.desktop_tokens import DesktopTokenStore
    from ..db.device_auth import DeviceAuthStore
    from ..db.email import EmailSender
    from ..db.share_tokens import ResolvedShare, ShareTokenStore
    from ..db.workers import WorkersStore
    from ..worker_channel import WakeChannelRegistry


logger = logging.getLogger(__name__)

# Filename of the desktop export-run log; defined beside the model so
# ``backup`` can archive it without importing the server.
EXPORT_RUNS_FILE = export_runs.LOG_FILENAME

# Chunk size the multipart-upload client splits a file into. 16 MiB is
# comfortably above S3/R2's 5 MiB non-final-part minimum and keeps the part
# count modest (a 2 GB file = 128 parts), so per-part presign round-trips
# stay cheap.
_RAW_UPLOAD_PART_SIZE = 16 * 1024 * 1024

STATIC_DIR = Path(__file__).parent.parent / "ui_static" / "dist"


def _no_project_error() -> HTTPException:
    """Raised by :class:`AppState` when an endpoint that needs a bound
    project is hit while the server is in unbound (picker) mode.

    The SPA inspects ``detail.code == "no_project"`` to redirect to the
    picker route instead of rendering a generic error.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "no_project",
            "message": (
                "No project is currently open. Pick one from the project "
                "list (POST /api/user/recent-projects/bind) or restart "
                "with `splitsmith ui --project <path>`."
            ),
        },
    )


def _shooter_storage_scope(match_id: str | None, slug: str) -> str | None:
    """The per-shooter prefix for derived-artifact caches (audio, trims, peaks).

    ``None`` outside a ``/api/matches/{id}/`` scope. Every project loader
    binds storage with this, with or without a state store: the keys
    ``ui/audio`` derives depend on it, so two loaders disagreeing on the
    scope would make the same artifact live under two keys.
    """
    return f"matches/{match_id}/shooters/{slug}" if match_id is not None else None


@dataclass
class MatchBundle:
    """Every shooter of the bound match, loaded once (#1181).

    ``projects`` is keyed by slug in match order, minus shooters whose
    project doc is missing. ``audits`` is ``{slug: {stage_number: doc}}``
    on both sides, the same contract :meth:`AppState.load_audit_docs` has;
    a shooter with no audits has an empty dict.
    """

    projects: dict[str, MatchProject]
    audits: dict[str, dict[int, dict]]

    def audit_docs(self, slug: str) -> dict[int, dict]:
        return self.audits.get(slug, {})


@dataclass
class AppState:
    """Process state. One bound Match folder at a time.

    The bound project is always a Match folder; the legacy single-
    shooter layout was retired in Tier 1 step 3 of doc 10.

    - Match-level operations (list shooters, merge, export the whole
      match) read :attr:`match_root`, which resolves from the per-
      request ``current_match_root`` ContextVar set by the
      ``/api/matches/{match_id}/`` alias middleware.
    - Shooter-scoped operations require a slug from the URL path and
      resolve via :meth:`shooter_root` / :meth:`shooter_project`,
      which also read the same ContextVar.

    There is no notion of an "active" or "current" shooter on the
    server. Every shooter-scoped request must carry the slug in its
    path; the SPA's URL is the single source of truth.

    There is no notion of a "bound" project on the server -- the URL
    prefix ``/api/matches/{match_id}/`` carries match identity per
    request, and the alias middleware resolves it via
    :class:`MatchRegistry`. ``splitsmith ui --project <path>`` and
    the SPA picker register matches in :attr:`matches` and emit a
    URL the browser navigates to; the server itself never has a
    "current open project" state.
    """

    # The ``JobBackend`` Protocol (see ``splitsmith.ui.jobs``) is
    # what handlers depend on; ``JobRegistry`` is the local in-memory
    # implementation. In hosted mode the per-request / per-job tenant
    # backend resolves through the ``jobs`` property below; this backing
    # slot stays the local ``JobRegistry`` and is consulted only when no
    # tenant is pinned (boot-time body registration), never for queries.
    _jobs: JobBackend = field(default_factory=JobRegistry)
    # Process-level ``kind`` -> body registry, shared by the local backend
    # and every per-request hosted backend. ``register_job_bodies`` (and the
    # dev routes) register here via ``state.jobs.bodies``; ``__post_init__``
    # points the backing ``_jobs`` at it, and ``_apply_hosted_mode_wiring``
    # injects it into each tenant backend. Decoupling the map from any one
    # backend is what lets hosted mode build backends per request without
    # re-registering bodies -- and what lets the loopback boot user go away.
    job_bodies: JobBodyRegistry = field(default_factory=JobBodyRegistry)
    matches: MatchRegistry = field(default_factory=MatchRegistry)
    # Per-user ``matches`` table store (PR-delta). ``None`` in local mode;
    # in hosted mode resolved per request / per job via the property below.
    # See ``splitsmith.db.matches.PostgresMatchStore``.
    _matches_store: PostgresMatchStore | None = None
    # Per-user ``state_docs`` store (state refactor). Holds the match /
    # per-shooter project / per-stage audit JSON docs in Postgres with
    # optimistic locking. ``None`` in local mode (state stays file-based);
    # in hosted mode resolved per request / per job via the property below.
    # See ``splitsmith.db.project_state.ProjectStateStore``.
    _project_state: ProjectStateStore | None = None
    # Per-user preference stores (Tier 3 of doc 10). Local mode
    # delegates to the ``~/.splitsmith/*.json`` module functions;
    # in hosted mode resolved per request from the authenticated user
    # via the properties below. See ``splitsmith.user_config``.
    _recent_projects: user_config.RecentProjectsStore = field(
        default_factory=user_config.JsonRecentProjectsStore
    )
    _scoreboard_identity: user_config.ScoreboardIdentityStore = field(
        default_factory=user_config.JsonScoreboardIdentityStore
    )
    _looks: look_store_module.LookStore = field(default_factory=look_store_module.FolderLookStore)
    _whats_new: whats_new_module.WhatsNewStore = field(default_factory=whats_new_module.PrefsWhatsNewStore)
    _export_presets: export_presets_module.ExportPresetStore = field(
        default_factory=export_presets_module.JsonExportPresetStore
    )
    _shooter_book: shooter_book_module.ShooterBookStore = field(
        default_factory=lambda: shooter_book_module.JsonShooterBookStore(
            backfill_source=_local_backfill_source
        )
    )
    _account_profile: account_profile_module.AccountProfileStore = field(
        default_factory=account_profile_module.JsonAccountProfileStore
    )
    # Hosted-mode factory: build a :class:`TenantContext` for a ``user_id``.
    # ``None`` in local mode. Set by ``_apply_hosted_mode_wiring``; called
    # per request (auth gate) and per job (queue task).
    _build_tenant: Callable[[str], TenantContext] | None = None
    # Auth backend. ``LoopbackAuth`` in local mode (every request
    # resolves to the same sentinel user); a hosted-mode backend will
    # be injected here when SaaS lands. See ``splitsmith.auth``.
    auth: AuthBackend = field(default_factory=LoopbackAuth)
    # Compute backend. ``LocalComputeBackend`` runs the shot-detection
    # ensemble in this process; a hosted ``RemoteComputeBackend`` will
    # ship audio to a cloud worker. See ``splitsmith.compute`` and
    # ``docs/saas-readiness/04-compute-backends.md``. The default uses
    # ``load_ensemble_runtime`` directly; ``create_app`` overrides this
    # with one wired to the local ``_get_ensemble_runtime`` so test
    # monkeypatches and the shared model cache still work.
    compute: ComputeBackend = field(default_factory=LocalComputeBackend)
    # Tenant-scoped object storage. ``None`` in local mode -- the
    # desktop codepaths read/write the user's chosen project folder
    # directly via ``pathlib.Path`` and never consult this slot. In
    # hosted mode (``SPLITSMITH_MODE=hosted``) ``_apply_hosted_mode_wiring``
    # constructs a per-user :class:`S3Storage` scoped to
    # ``users/<user_id>/`` so the upload endpoints can stream raw
    # footage into R2 / MinIO without ever touching the API
    # container's filesystem. Resolved per request / per job via the
    # property below. See ``docs/saas-readiness/03-storage-layer.md``.
    _storage: Storage | None = None
    # Stop callback registered by :func:`splitsmith.ui.embedded.run_embedded`
    # so the /api/shutdown route can ask uvicorn to exit. None under the
    # ``splitsmith ui`` CLI path -- Ctrl-C via _JobAwareServer.handle_exit
    # is the stop mechanism there.
    shutdown_handler: Callable[[], None] | None = None
    # Idempotency latch for /api/shutdown: first call kicks the drain
    # thread, subsequent calls return 202 without rescheduling.
    shutdown_initiated: bool = False

    # Hosted-mode auth config, set by ``_apply_hosted_mode_wiring`` from
    # ``SPLITSMITH_PUBLIC_URL``. ``public_base_url`` is the origin the
    # magic-link callback lives under; ``cookie_secure`` is True iff that
    # origin is https (so the session cookie's Secure flag round-trips
    # over docker-compose http but is set in production). Both stay at
    # their defaults in local mode (no auth, no cookies).
    public_base_url: str | None = None
    cookie_secure: bool = False
    # Lowercase email addresses granted admin access. Populated from
    # ``SPLITSMITH_ADMIN_EMAILS`` by ``_apply_hosted_mode_wiring``; stays
    # empty in local mode so nobody is admin there.
    admin_emails: frozenset[str] = frozenset()
    # Account tier registry (spec 2026-10-03). Populated from
    # ``SPLITSMITH_CONFIG`` by ``_apply_hosted_mode_wiring``; local mode
    # never consults it.
    access: AccessConfig = field(default_factory=AccessConfig)
    # Access requests and the admin's account list (spec 2026-10-03), plus
    # the mail transport the app sends access mail through. Raw (non-tenant)
    # session factory: requests arrive before any account exists. ``None``
    # in local mode.
    access_requests: AccessRequestStore | None = None
    accounts: AccountAccessStore | None = None
    email_sender: EmailSender | None = None
    # Records an access request and alerts the admins when it is new. Set
    # by ``_apply_hosted_mode_wiring``; ``None`` in local mode.
    record_access_request: Callable[..., Awaitable[None]] | None = None
    # Fire-and-forget tasks the app started off the request path (the
    # access-request admin alerts). Held here so the event loop's weak
    # reference is not the only one; each task removes itself when done.
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    # Hosted + launcher only: serve-boot pending-jobs re-check, registered
    # as a FastAPI startup handler by create_app. Runs on every cold start
    # (serve scales to zero on Cloud Run, so that is every first visit).
    boot_retrigger: Callable[[], Awaitable[None]] | None = None
    # Operator-scoped worker registry (self-hosted workers). ``None`` in
    # local mode. Built over the RAW session factory - workers are operator
    # infrastructure shared across tenants, not per-user data (no RLS; the
    # unique token hash is the isolation boundary).
    workers_store: WorkersStore | None = None
    # Hosted only: the per-loop pooled engines (#1178). ``None`` in local
    # mode, which has no database. The lifespan adopts the app loop and
    # disposes it; the process ``DbRunner`` (``async_bridge.get_runner``)
    # adopts its own.
    db_engines: LoopEngines | None = None
    # Device-flow authorizations (#719). Raw (non-tenant) session factory,
    # same as workers_store: the poll authenticates from the device code
    # alone, before any tenant is pinned. None in local mode.
    device_auth: DeviceAuthStore | None = None
    # In-flight device-flow state for the LOCAL install (#719): the
    # device code, the hosted-declared interval, when it expires, and
    # when we last forwarded a poll. Also the non-secret half of the
    # hosted response - ``user_code``, ``verification_uri``,
    # ``verification_uri_complete`` - so a second ``device/start`` can
    # hand the still-live flow back to the SPA instead of dead-ending it
    # (the operator who cancels the dialog and signs in again must get
    # the code and the approve link, not a ten-minute wait). Only
    # ``device_code`` is a secret and it never leaves this process.
    # Deliberately in memory and not on disk - polling is lazy (the
    # SPA's own poll drives it), so closing the tab leaves no orphaned
    # poller, and restarting the local server mid-flow just means
    # starting over inside a 10-minute window.
    device_flow: dict[str, object] | None = None
    # Monotonic timestamp of the last upstream account refresh (#877), or
    # None when none has run in this process. Held here rather than in
    # config.yaml because it is per-process liveness, not user
    # preference: a restart should refresh, and two installs sharing a
    # home directory should not share a cooldown.
    hosted_account_refreshed_at: float | None = None
    # Guards every read-modify-write of ``device_flow`` (#719). The three
    # local device routes each ``await`` a threadpool HTTP call in the
    # middle of reading/mutating ``device_flow``; without a lock, two
    # concurrent status polls can both pass the throttle check before
    # either records ``last_polled_at``, doubling load on the hosted side
    # and letting one poll's approval response get discarded by the SPA
    # while the other request that "won" the race serves a stale status.
    # A plain ``asyncio.Lock`` is enough - this is one desktop process
    # serving one operator, not a multi-worker server, so there is no
    # cross-process case to cover.
    device_flow_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Guards the whole load/append/save of the DESKTOP export-run log
    # (#629). Local mode's ``load_export_runs`` always reports version 0
    # and ``save_export_runs`` ignores the version it is handed, so the
    # optimistic-lock retry in ``_record_export_run`` cannot protect the
    # read-modify-write there -- and it genuinely races: the Export page's
    # trims-only bundle button submits one export job per selected stage
    # and ``JobRegistry`` runs two at a time against one document.
    # A ``threading.Lock`` and not a file lock, deliberately: desktop is a
    # single server process, so this serialises every writer that exists.
    # It is NOT cross-process safe, and it does not need to be -- two
    # ``splitsmith ui`` processes over one project folder already race on
    # far more than this file. Hosted never takes it; its optimistic
    # locking is the real mechanism there.
    export_run_lock: threading.Lock = field(default_factory=threading.Lock)
    #: Serialises a job's project read-modify-write (``DocStore.update_project``)
    #: on the desktop, where the file store has no version to conflict on.
    project_lock: threading.Lock = field(default_factory=threading.Lock)
    # The same guard for DESKTOP audit docs (#931): ``save_audit`` holds it
    # across the write and the ``.bak`` rotation, and
    # ``_save_audit_with_remerge`` across its re-load/merge/save. A sibling
    # of ``export_run_lock`` rather than that lock, because the two
    # documents share no writer and a shot-detect save has no reason to
    # queue behind an export's bookkeeping. Re-entrant because the re-merge
    # calls ``save_audit`` while holding it. Same process-local scope, and
    # hosted never takes it.
    audit_lock: threading.RLock = field(default_factory=threading.RLock)
    # Reconcile steps this process submitted, keyed by
    # ``_reconcile_key`` (match, kind, slug, stage), so the terminal
    # listener can record a failure with the inputs it ran on (spec
    # 2026-09-27 s3). Registered before submit: a job that fails in
    # milliseconds can reach the listener before submit() returns. Local
    # only; an entry leaves on its job's terminal event.
    reconcile_jobs: dict[tuple[str | None, str, str | None, int | None], tuple[Path, ReconcileStep]] = field(
        default_factory=dict
    )
    # Desktop auto-sync service (spec 2026-09-27); None hosted and when
    # SPLITSMITH_AUTO_SYNC=0.
    auto_sync: AutoSyncService | None = None
    # Live SSE wake channels, one asyncio.Queue per connected self-hosted
    # worker. ``None`` in local mode and on the headless worker process
    # (which must never hold launcher capabilities); set by the non-worker
    # hosted wiring together with the dispatcher that pushes into it.
    wake_channels: WakeChannelRegistry | None = None
    # Connection material returned by ``POST /api/workers/register``:
    # database_url + public_url, plus the S3 sub-dict when the bucket is
    # configured (``None`` otherwise). ``None`` in local mode.
    worker_credentials: dict[str, Any] | None = None
    # Anonymous share-token resolver (public share links, issue #349). Set
    # only by hosted wiring; None in local mode. Built over the RAW
    # session_factory (not a tenant factory) because share_tokens is not
    # under RLS - anonymous resolution happens before any app.user_id GUC
    # exists, and the unique-token lookup is itself the isolation boundary.
    resolve_share_token: Callable[[str], Awaitable[ResolvedShare | None]] | None = None

    def __post_init__(self) -> None:
        # Point the backing local backend's body map at the shared
        # process-level registry so ``state.jobs.bodies.register(...)`` (used
        # by ``register_job_bodies`` + the dev routes, all at boot with no
        # tenant pinned) lands on the same registry the hosted per-tenant
        # backends read from. In local mode this is also the registry the
        # JobRegistry executes against.
        self._jobs.bodies = self.job_bodies

    # ------------------------------------------------------------------
    # Per-user stores -- tenant-resolving in hosted mode, singletons local
    # ------------------------------------------------------------------
    #
    # Each getter returns the request/job's :class:`TenantContext` store
    # when ``current_tenant`` is set (hosted mode), else the process
    # singleton in the backing field (local mode + boot/non-request code).
    # The setters write the backing field so tests + the local path can
    # inject fakes; hosted requests never assign these (they set
    # ``current_tenant`` instead). This mirrors ``shooter_root`` reading
    # ``current_match_root``.

    @property
    def jobs(self) -> JobBackend:
        tenant = current_tenant.get()
        return tenant.jobs if tenant is not None else self._jobs

    @jobs.setter
    def jobs(self, value: JobBackend) -> None:
        self._jobs = value

    @property
    def recent_projects(self) -> user_config.RecentProjectsStore:
        tenant = current_tenant.get()
        return tenant.recent_projects if tenant is not None else self._recent_projects

    @recent_projects.setter
    def recent_projects(self, value: user_config.RecentProjectsStore) -> None:
        self._recent_projects = value

    @property
    def scoreboard_identity(self) -> user_config.ScoreboardIdentityStore:
        tenant = current_tenant.get()
        return tenant.scoreboard_identity if tenant is not None else self._scoreboard_identity

    @scoreboard_identity.setter
    def scoreboard_identity(self, value: user_config.ScoreboardIdentityStore) -> None:
        self._scoreboard_identity = value

    @property
    def shooter_book(self) -> shooter_book_module.ShooterBookStore:
        """The account's shooter book: the tenant's hosted, the local files
        locally, and an empty one for a hosted request with no tenant."""
        tenant = current_tenant.get()
        if tenant is not None:
            return tenant.shooter_book or shooter_book_module.EmptyShooterBookStore()
        if self._build_tenant is not None:
            return shooter_book_module.EmptyShooterBookStore()
        return self._shooter_book

    @property
    def account_profile(self) -> account_profile_module.AccountProfileStore:
        tenant = current_tenant.get()
        if tenant is not None:
            return tenant.account_profile or account_profile_module.EmptyAccountProfileStore()
        if self._build_tenant is not None:
            return account_profile_module.EmptyAccountProfileStore()
        return self._account_profile

    @property
    def export_presets(self) -> export_presets_module.ExportPresetStore:
        tenant = current_tenant.get()
        if tenant is not None and tenant.export_presets is not None:
            return tenant.export_presets
        return self._export_presets

    @property
    def whats_new(self) -> whats_new_module.WhatsNewStore:
        tenant = current_tenant.get()
        if tenant is not None and tenant.whats_new is not None:
            return tenant.whats_new
        return self._whats_new

    @property
    def looks(self) -> look_store_module.LookStore:
        tenant = current_tenant.get()
        if tenant is not None and tenant.looks is not None:
            return tenant.looks
        return self._looks

    @property
    def matches_store(self) -> PostgresMatchStore | None:
        tenant = current_tenant.get()
        return tenant.matches_store if tenant is not None else self._matches_store

    @matches_store.setter
    def matches_store(self, value: PostgresMatchStore | None) -> None:
        self._matches_store = value

    @property
    def project_state(self) -> ProjectStateStore | None:
        tenant = current_tenant.get()
        return tenant.project_state if tenant is not None else self._project_state

    @project_state.setter
    def project_state(self, value: ProjectStateStore | None) -> None:
        self._project_state = value

    @property
    def storage(self) -> Storage | None:
        tenant = current_tenant.get()
        return tenant.storage if tenant is not None else self._storage

    @storage.setter
    def storage(self, value: Storage | None) -> None:
        self._storage = value

    @property
    def share_tokens(self) -> ShareTokenStore | None:
        # Local mode has no per-user share-token store - share links are a
        # hosted-only feature. Returns None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.share_tokens if tenant is not None else None

    @property
    def desktop_tokens(self) -> DesktopTokenStore | None:
        # Local mode has no per-user desktop-token store - desktop sync is a
        # hosted-only feature. Returns None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.desktop_tokens if tenant is not None else None

    @property
    def desktop_commands(self) -> DesktopCommandStore | None:
        # Hosted-only, like desktop_tokens. None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.desktop_commands if tenant is not None else None

    @property
    def comments(self) -> CommentStore | None:
        # Local mode has no per-user comment store - public comments are a
        # hosted-only feature. Returns None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.comments if tenant is not None else None

    @property
    def profile(self) -> PostgresProfileStore | None:
        # Local mode has no per-user profile store - display names are a
        # hosted-account concept. Returns None when no tenant is pinned.
        tenant = current_tenant.get()
        return tenant.profile if tenant is not None else None

    @property
    def youtube_connections(self) -> PostgresYouTubeConnectionStore | None:
        # None in local mode: the connection is the youtube.json file there.
        tenant = current_tenant.get()
        return tenant.youtube if tenant is not None else None

    def build_tenant(self, user_id: str) -> TenantContext:
        """Build the :class:`TenantContext` for ``user_id`` (hosted mode).

        Raises if called in local mode -- there is no per-user tenancy
        there, and a caller reaching this in local mode is a bug. The
        hosted wiring sets :attr:`_build_tenant`.
        """
        if self._build_tenant is None:
            raise RuntimeError(
                "AppState.build_tenant called without a hosted-mode tenant "
                "factory; this is only valid when SPLITSMITH_MODE=hosted "
                "wiring has run."
            )
        return self._build_tenant(user_id)

    @property
    def match_root(self) -> Path:
        """The match folder resolved from the request URL's
        ``/api/matches/{match_id}/`` prefix.

        Tier 1 of the singleton-elimination work (doc 10): match-level
        operations are addressable only by URL, never via a process-
        level bind. The alias middleware sets ``current_match_root``
        after validating ``match_id``; this accessor reads only from
        there. A bare-path request (no prefix) gets 409 ``no_project``.
        Legacy single-shooter projects don't have a ``match_id`` and
        are unreachable via match-level routes by construction.
        """
        scoped = current_match_root.get()
        if scoped is None:
            raise _no_project_error()
        return scoped

    def shooter_root(self, slug: str) -> Path:
        """Resolve ``slug`` to the shooter's project directory on disk.

        The path is always ``<match_root>/shooters/<slug>``. The
        match root comes from the per-request ``current_match_root``
        ContextVar set by the ``/api/matches/{match_id}/`` alias
        middleware -- there is no process-level singleton fallback,
        and there is no legacy single-shooter layout to support
        (Tier 1 step 3 of doc 10 retired both). Bare-path requests
        without the URL prefix get 409 ``no_project``.
        """
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        # Roster / ownership check. Hosted: the match doc lives in Postgres
        # (state_docs), so read the shooter list from there -- a worker or a
        # post-redeploy replica has no match.json on disk. Local: load the
        # on-disk match.json. Either way the returned value is the on-disk
        # path, which is where *media* (S3-mirrored) lives for ffmpeg.
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            doc, _ = run_sync(store.load_match(mid))
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"match {mid!r} has no state document",
                )
            shooters = doc.get("shooters", [])
        else:
            shooters = match_model.Match.load(scoped_root).shooters
        if slug not in shooters:
            raise HTTPException(
                status_code=404,
                detail=f"shooter {slug!r} is not registered on this match",
            )
        return match_model.Match.shooter_root(scoped_root, slug)

    def _audit_file(self, slug: str, stage_number: int) -> Path:
        """The on-disk path for a stage's audit JSON. Used only by the
        *local-mode* branches of ``load_audit`` / ``save_audit`` /
        ``materialize_audit`` -- hosted mode keeps audit docs in
        ``state_docs`` and never touches this file."""
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        return match_model.Match.shooter_root(scoped_root, slug) / "audit" / f"stage{stage_number}.json"

    # ------------------------------------------------------------------
    # State-doc accessors (state refactor)
    # ------------------------------------------------------------------

    def match(self) -> match_model.Match:
        """Load the match for the current ``/api/matches/{match_id}/`` scope.

        Hosted: from the ``state_docs`` table, with the state store bound so
        a subsequent ``.save()`` round-trips back under optimistic locking
        (carrying the loaded version). Local: from the on-disk ``match.json``
        (``Match.load`` may self-assign + save a pre-v4 ``match_id`` -- that
        path is file-only and never reached for a store-backed match, whose
        docs are born with an id).
        """
        scoped_root = current_match_root.get()
        if scoped_root is None:
            raise _no_project_error()
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            doc, version = run_sync(store.load_match(mid))
            if doc is None:
                raise HTTPException(status_code=404, detail=f"match {mid!r} has no state document")
            match = match_model.Match.model_validate(doc)
            match.bind_state(self.docstore(), version=version)
            return match
        return match_model.Match.load(scoped_root)

    def docstore_for(self, match_id: str, root: Path | None = None) -> DocStore:
        """The storage port for a match named outright rather than by the
        request's context: ``DbDocStore`` when a state store is bound, else
        ``FileDocStore`` over ``root``. For the match creation routes, whose
        match the alias middleware has not seen yet, the registration paths
        that hold a path and an id, and the delete cascade's walk over a
        tenant's other matches (hosted only, so no root)."""
        store = self.project_state
        if store is not None:
            return DbDocStore(store, match_id)
        if root is None:
            raise _no_project_error()
        return FileDocStore(
            root,
            audit_lock=self.audit_lock,
            export_run_lock=self.export_run_lock,
            project_lock=self.project_lock,
        )

    def docstore(self) -> DocStore:
        """The storage port bound to the current request's match (spec
        2026-10-19): ``DbDocStore`` when a state store is bound and the alias
        middleware pinned a match id, else ``FileDocStore`` over the pinned
        match root with this process's locks (the audit lock is the one the
        sync pull holds, #1075)."""
        store = self.project_state
        mid = current_match_id.get()
        if store is not None and mid is not None:
            return DbDocStore(store, mid)
        root = current_match_root.get()
        if root is None:
            raise _no_project_error()
        # The pure path, not :meth:`shooter_root`: the file accessors never
        # checked the roster (an unknown slug reads as "no document"), and
        # the routes that must 404 resolve the project first.
        return FileDocStore(
            root,
            audit_lock=self.audit_lock,
            export_run_lock=self.export_run_lock,
            project_lock=self.project_lock,
        )

    def load_audit(self, slug: str, stage_number: int) -> tuple[dict | None, int]:
        """Load a stage's audit doc + its version through the storage port:
        ``(None, 0)`` when none exists yet. Hosted: ``state_docs``. Local:
        ``audit/stage<N>.json`` (version always 0)."""
        try:
            return self.docstore().load_audit(slug, stage_number)
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit read failed: {exc}") from exc

    def load_audit_docs(self, slug: str) -> dict[int, dict]:
        """Per-stage audit docs for one shooter, ``{stage_number: doc}``,
        through the storage port: one bulk query hosted, the audit files
        locally. This is the single loader the status and count routes hand
        to :meth:`MatchProject.stage_statuses` / :meth:`audited_count` /
        :meth:`export_overview`, which never read a file themselves."""
        try:
            return {stage: doc for stage, (doc, _version) in self.docstore().list_audits(slug).items()}
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit read failed: {exc}") from exc

    def save_audit(self, slug: str, stage_number: int, doc: dict, *, version: int) -> int:
        """Persist a stage's audit doc through the storage port; return the
        new version. Hosted: ``state_docs`` under optimistic locking
        (``version==0`` INSERTs, ``>0`` UPDATEs at that version, a stale
        version raises ``StateConflictError`` -> 409). Local: unique temp
        file -> ``.bak`` rotate -> rename under :attr:`audit_lock` (#931),
        returns 0."""
        try:
            return self.docstore().save_audit(slug, stage_number, doc, version=version)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"audit write failed: {exc}") from exc

    def update_audit_at(
        self, slug: str, stage_number: int, merge: Callable[[dict | None, int], dict]
    ) -> tuple[dict, int]:
        """A handler's audit read-modify-write through the storage port (spec
        2026-10-19, amended): ``merge`` gets the stored doc (``None`` when
        there is none) and its version, raises its own 404 or 409 part-way,
        or returns the doc to write; the call returns that doc and its new
        version. Local: under :attr:`audit_lock`, the one the sync pull
        holds. Hosted: optimistic, a lost race re-applies ``merge`` to the
        winner. An error ``merge`` raises writes nothing and is the
        handler's answer."""
        try:
            return self.docstore().update_audit_at(slug, stage_number, merge)
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"audit update failed: {exc}") from exc

    def delete_audit(self, slug: str, stage_number: int) -> bool:
        """Delete a stage's audit doc through the storage port. Returns True
        when one was removed. Used when a stage is removed from the match
        (#521): the doc describes shots on a stage that no longer exists.
        Local: the file and its ``.bak`` go together, under the save's lock."""
        try:
            return self.docstore().delete_audit(slug, stage_number)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"audit delete failed: {exc}") from exc

    def load_export_runs(self, slug: str) -> tuple[dict | None, int]:
        """The shooter's export history doc + version (#629), through the
        storage port. Hosted: ``state_docs``. Local:
        ``<shooter_root>/export_runs.json``; an unreadable file degrades to
        "no history" rather than a 500: this is bookkeeping."""
        try:
            return self.docstore().load_export_runs(slug)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("export run record: unreadable for %s: %s", slug, exc)
            return None, 0

    def save_export_runs(self, slug: str, doc: dict, *, version: int) -> int:
        """Persist the export history doc through the storage port; return
        the new version (0 locally, where there is no version to lose a
        race on; ``_record_export_run`` holds :attr:`export_run_lock` across
        its read-modify-write)."""
        return self.docstore().save_export_runs(slug, doc, version=version)

    def materialize_audit(self, slug: str, stage_number: int) -> Path:
        """Ensure the stage's audit doc is present at its on-disk path and
        return that path.

        For file-path consumers that hand the audit *path* to downstream
        readers (the FCPXML/report exporters, the lab). Hosted: the doc
        lives in ``state_docs``, so write it to the local file first.
        Local: the file is already on disk, return its path. When no audit
        doc exists the returned path simply won't exist -- the caller's
        existing "missing audit" handling fires, same as before.

        Hosted with no doc, a local file an earlier job on this worker
        materialized is removed (#1332): it is a copy of a doc that is
        gone, and the export (and the overlay's audit revision) would read
        it as current."""
        audit_file = self._audit_file(slug, stage_number)
        if not self.docstore().documents_on_disk:
            doc, _ = self.load_audit(slug, stage_number)
            if doc is not None:
                audit_file.parent.mkdir(parents=True, exist_ok=True)
                audit_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            else:
                audit_file.unlink(missing_ok=True)
        return audit_file

    def shooter_project(self, slug: str) -> MatchProject:
        """Load the per-shooter ``MatchProject`` (each shooter slot
        inside a Match folder owns its own ``project.json``).

        Binds :attr:`storage` onto the project so
        :meth:`MatchProject.resolve_video_path` can mirror hosted-mode
        raw videos into the local cache on first access. The bound
        ``scope`` is ``matches/<match_id>/shooters/<slug>`` -- used as
        the prefix for derived-artifact caches (audio WAVs today,
        trim outputs later) so two shooters in different matches
        can't collide on the same ``video_id``. Local mode leaves
        ``storage`` at ``None``; the bind is a no-op and the legacy
        disk-only resolvers win.
        """
        shooter_root = self.shooter_root(slug)
        match_id = current_match_id.get()
        store = self.project_state
        if store is not None and match_id is not None:
            # Hosted: the project doc lives in Postgres. Load it, then bind
            # it exactly as a bundled doc is bound (``_project_from_doc``).
            doc, version = run_sync(store.load_project(match_id, slug))
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"shooter {slug!r} has no project document",
                )
            return self._project_from_doc(slug, doc, version)
        project = MatchProject.load(shooter_root)
        # A disk-backed project under a ``/api/matches/{id}/`` scope still
        # gets the per-shooter storage scope: storage without a state store
        # is a valid configuration (local disk docs + an object store for
        # derived artifacts), and its audio / peaks keys depend on it.
        project.bind_storage(self.storage, scope=_shooter_storage_scope(match_id, slug))
        return project

    def _project_from_doc(self, slug: str, doc: dict, version: int) -> MatchProject:
        """Bind a hosted project doc the way every hosted loader must.

        The store is bound so ``save()`` round-trips back under optimistic
        locking (carrying ``version``, the version the doc was read at);
        the paths are confined because the doc may carry another machine's
        (a desktop mirror's own sources and cache dirs), so they resolve
        inside the shooter root only; storage is bound under the per-shooter
        scope for derived-artifact caches. :meth:`shooter_project` and
        :meth:`match_bundle` both go through here so one shooter loaded
        alone and one loaded with its match are the same object.
        """
        match_id = current_match_id.get()
        assert self.project_state is not None and match_id is not None  # callers are hosted-only
        project = MatchProject.model_validate(doc)
        docs = self.docstore()
        project.bind_state(docs, slug=slug, version=version)
        if docs.confines_paths:
            project.confine_paths()
        project.bind_storage(self.storage, scope=_shooter_storage_scope(match_id, slug))
        return project

    def match_bundle(self, match: match_model.Match) -> MatchBundle:
        """Every shooter's project and audit docs for ``match``, in one query (#1181).

        Triage, the triage summary and the beep queue each walked the match
        shooter by shooter -- a ``state_docs`` query per project doc and
        another per shooter's audit docs -- which is what made opening a
        match cost 2.5-3.4 s per fetch on production. Hosted, this loads
        them all with :meth:`ProjectStateStore.load_docs_for_matches` and
        binds each project through :meth:`_project_from_doc`. A shooter
        whose project doc is missing is left out, as the per-shooter loops
        skipped it. Local mode reads each ``project.json`` from disk and
        each shooter's audit files through the port, so ``audits`` is
        filled on both sides.
        """
        match_id = current_match_id.get()
        store = self.project_state
        # One shooter's unloadable project (a missing or corrupt doc) skips
        # that shooter, never the match: the per-shooter loops this replaces
        # answered for the rest, and the Overview must still render.
        if store is None or match_id is None:
            projects: dict[str, MatchProject] = {}
            for slug in match.shooters:
                try:
                    projects[slug] = self.shooter_project(slug)
                except Exception as exc:  # noqa: BLE001 -- isolate per shooter
                    logger.warning("Skipping shooter %s: %s", slug, exc)
            return MatchBundle(
                projects=projects, audits={slug: self.load_audit_docs(slug) for slug in projects}
            )
        docs = run_sync(store.load_docs_for_matches([match_id]))[match_id]
        projects = {}
        for slug in match.shooters:
            doc = docs.projects.get(slug)
            if doc is None:
                logger.warning("Skipping shooter %s: no project document", slug)
                continue
            try:
                projects[slug] = self._project_from_doc(slug, doc, docs.project_versions.get(slug, 0))
            except Exception as exc:  # noqa: BLE001 -- isolate per shooter
                logger.warning("Skipping shooter %s: %s", slug, exc)
        return MatchBundle(projects=projects, audits=docs.audits)

    async def shooter_project_async(self, slug: str) -> MatchProject:
        """Async twin of :meth:`shooter_project`.

        Awaits the store load directly instead of blocking the event loop
        via ``run_sync``. Use from hot async handlers (assignment moves) so
        one shooter's load doesn't stall every other in-flight request. Local
        mode has no store and falls back to the sync file loader (cheap read).
        """
        shooter_root = self.shooter_root(slug)
        match_id = current_match_id.get()
        store = self.project_state
        if store is not None and match_id is not None:
            doc, version = await store.load_project(match_id, slug)
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"shooter {slug!r} has no project document",
                )
            return self._project_from_doc(slug, doc, version)
        project = MatchProject.load(shooter_root)
        project.bind_storage(self.storage, scope=_shooter_storage_scope(match_id, slug))
        return project
