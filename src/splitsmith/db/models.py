"""SQLAlchemy 2.x declarative models.

One table to start: ``users``. The rest of doc 02's schema
(``sessions``, ``desktop_links``, ``projects``, ``project_members``,
``upload_sessions``, ``compute_jobs``, ``billing_events``) lands
as each corresponding hosted-impl PR needs it -- a schema flood
at once would be hard to review and most of the tables sit unused
until their feature ships.

Forward-compat: every table uses ULID string primary keys
(per doc 02, "not auto-increment") so a project record stays
stable through bucket migrations / engine swaps and you can
generate ids client-side without a round-trip.
"""

from __future__ import annotations

from datetime import datetime

import ulid
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base. All ORM models derive from this; Alembic's
    ``--autogenerate`` reads ``Base.metadata`` to diff against the
    live schema."""


def new_ulid() -> str:
    """Generate a fresh ULID string. Picked over UUID4 for the same
    reason doc 02 calls out: sortable by creation time, URL-safe,
    same 128 bits of entropy."""
    return str(ulid.ULID())


class User(Base):
    """One row per Splitsmith account (doc 02).

    The ``email`` column is the natural key for magic-link auth
    lookups but we use a synthetic ULID PK so an email change
    doesn't rewrite every foreign key referencing the user. The
    soft-delete column lets the 7-day account-deletion grace
    period live entirely in this row -- no separate "deleted
    accounts" table.
    """

    __tablename__ = "users"
    # The vendor + vendor-id pair is unique across the table -- two
    # rows can't share the same (provider, id), but two providers
    # can each have their own "user_abc" since the ids live in
    # disjoint namespaces. The constraint is partial in spirit
    # (only meaningful when both columns are non-null) but Postgres
    # and SQLite both treat NULL as "distinct" in unique indexes
    # so the local-mode rows with NULLs don't collide.
    __table_args__ = (
        UniqueConstraint(
            "external_auth_provider",
            "external_auth_id",
            name="uq_users_external_auth",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Billing block (doc 02 + doc 08). Stripe customer id is unique
    # because exactly one Stripe customer maps to exactly one user;
    # ``entitlement`` is the gate for premium-only endpoints.
    stripe_customer_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    entitlement: Mapped[str] = mapped_column(String, nullable=False, default="free")
    entitlement_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # External auth vendor link. The provider (Clerk / WorkOS /
    # Auth.js / etc.) owns the authentication; this column carries
    # the vendor's user id so we can resolve a session back to a
    # local user. Vendor-agnostic on purpose: ``external_auth_provider``
    # is the discriminator and the ``external_auth_id`` shape is
    # whatever string the vendor emits. ``None`` for local-mode
    # ``LoopbackAuth`` (the operator is implicit; no vendor exists).
    #
    # Foreign keys throughout the schema reference ``users.id``
    # (the local ULID), not this column -- swapping vendors only
    # rewrites this pair, never the rest of the data graph.
    external_auth_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_auth_provider: Mapped[str | None] = mapped_column(String, nullable=True)

    # Soft delete -- the row survives until the 7-day grace
    # expires so the user can recover by re-signing in.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Per-user SSI Scoreboard binding (shooter_id + display name +
    # division + club + base_url). Hosted-mode counterpart to
    # ``~/.splitsmith/scoreboard.json``. One identity per user, so
    # it lives as a JSON column on the user row instead of a
    # separate table -- saves a join and a migration for what is
    # structurally a profile field. ``None`` until the user pins
    # themselves via the SPA's scoreboard import flow.
    #
    # Generic ``JSON`` (not ``JSONB``) so SQLite tests work; the
    # field is read whole + written whole, never queried into, so
    # JSONB's indexing wins don't apply.
    scoreboard_identity: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"


class MagicLinkTokenRow(Base):
    """One row per issued magic-link challenge (doc 02).

    The passwordless login primitive: ``begin_login(email)`` inserts a row
    with a freshly-minted high-entropy token (we store only its SHA-256
    hash, never the raw value -- a DB leak must not yield usable links),
    a 15-minute expiry, and the requested email. ``complete_login(token)``
    hashes the presented token, finds the row, checks it is unexpired and
    unconsumed, then stamps ``consumed_at`` so it is single-use.

    The ``email`` is recorded as presented (not necessarily an existing
    ``users`` row -- first sign-in creates the account on redemption), so
    this table has **no** FK to ``users`` and is **not** under RLS: it is
    auth infrastructure resolved before any ``app.user_id`` GUC exists,
    same reasoning as ``users`` itself.
    """

    __tablename__ = "magic_link_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # SHA-256 hex of the raw token the email link carries. Unique so a
    # redemption is an indexed point lookup; the raw token never lands here.
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Single-use latch: set on the first successful redemption. A second
    # redemption of the same token finds it non-null and is rejected.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<MagicLinkTokenRow email={self.email!r} consumed={self.consumed_at is not None}>"


class SessionRow(Base):
    """One row per authenticated browser session (doc 02).

    Created on a successful magic-link redemption; the browser carries an
    httpOnly cookie holding the raw session secret, and we store only its
    SHA-256 hash (``token_hash``) so the cookie is a bearer capability a DB
    leak can't reconstruct. ``authenticate_request`` hashes the cookie,
    looks the row up, and resolves it back to a ``users`` row.

    Sessions live here (not in an auth vendor) so we can list a user's
    devices, revoke one session without nuking the rest, and hold
    last-used / UA metadata. 30-day sliding expiry: ``expires_at`` extends
    on activity (bumped lazily to avoid a write per request).

    Like ``magic_link_tokens`` this is auth infrastructure resolved before
    any GUC exists, so it is **not** under RLS -- the ``user_id`` FK +
    ``token_hash`` lookup are the isolation boundary.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    # SHA-256 hex of the raw session secret stored in the cookie.
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    # Stored as text (not a Postgres INET) so the same model builds the
    # SQLite test schema; the column is metadata for the "your devices"
    # UI, never queried as a network type.
    ip: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<SessionRow id={self.id!r} user_id={self.user_id!r}>"


class RecentProjectRow(Base):
    """One row per (user, project path) the user has opened.

    Hosted-mode counterpart to the local-mode ``projects.json``
    file: the picker reads from here ordered by ``last_opened_at``
    DESC. The (``user_id``, ``path``) pair is unique so re-opening
    a project bumps the timestamp instead of inserting a duplicate.

    ``path`` is the resolved on-disk path the user picked. In a
    pure-hosted future this becomes a project id or a bucket key
    instead; for the local-via-Postgres bridge it stays a literal
    path so the existing `RecentProject` pydantic shape round-trips
    without translation.

    ``kind`` mirrors the JSON store: ``"match"`` for redesigned
    folders, ``None``/``"legacy"`` for pre-redesign rows surfaced
    from older indexes.

    **Multi-tenant:** ``user_id`` is non-nullable and CASCADEs on
    user deletion. The unique constraint scopes paths per-user so
    Alice and Bob can open the same path without colliding. This
    table is intentionally per-user even after matches become
    shareable -- a "recently opened" list is personal state.
    Sharing happens at the (future) ``projects`` + ``project_members``
    layer; when that lands, this row may gain a nullable
    ``project_id`` column so shared projects surface in each
    member's picker without duplicating the underlying record.
    """

    __tablename__ = "recent_projects"
    __table_args__ = (UniqueConstraint("user_id", "path", name="uq_recent_projects_user_path"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # Stable match identifier (hosted). Lets the picker/bind resolve the
    # match through Postgres instead of the ephemeral on-disk ``path``,
    # which doesn't survive a redeploy. ``None`` for local-mode rows and
    # rows written before this column existed.
    match_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RecentProjectRow user_id={self.user_id!r} path={self.path!r}>"


class MatchRow(Base):
    """One row per (user, match) so a worker process can resolve a
    ``match_id`` it never opened locally.

    The local desktop flow resolves ``match_id`` -> on-disk path by
    scanning ``projects.json`` (see :class:`splitsmith.match_registry.MatchRegistry`).
    A separate hosted worker has no such file, so PR-delta gives
    ``match_id`` a first-class, queryable identity here: given just the
    ``(user_id, match_id)`` carried on the Procrastinate queue, the
    worker looks up the match's ``storage_prefix`` and mirrors its
    metadata + inputs down from S3 into a local working root.

    ``storage_prefix`` is the per-user-storage-root-relative prefix for
    the match's objects (``matches/<match_id>``); the per-user S3 root
    (``users/<user_id>/``) is supplied by the bound :class:`Storage`, so
    the prefix here stays tenant-agnostic.

    **Multi-tenant:** ``user_id`` is non-nullable and CASCADEs on user
    delete. The unique ``(user_id, match_id)`` pair scopes matches
    per-user. Every query in :class:`splitsmith.db.matches.PostgresMatchStore`
    filters by ``user_id``; isolation tests guard the invariant.
    """

    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("user_id", "match_id", name="uq_matches_user_match"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    storage_prefix: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<MatchRow user_id={self.user_id!r} match_id={self.match_id!r}>"


class StateDocRow(Base):
    """One row per hosted match-state JSON document (state refactor).

    Hosted-mode per-match state used to live in JSON files on each
    serve/worker container's ephemeral local disk, mirrored whole to S3
    (``match.json``, per-shooter ``project.json``, ``audit/stage<N>.json``).
    That model lost the state on every redeploy (empty working dir) and,
    even fully mirrored, was whole-file last-writer-wins on S3 -- two
    writers silently clobbered each other. This table holds those same
    small JSON docs in Postgres with optimistic-concurrency versioning, so
    match resolution is stateless across redeploys/replicas and concurrent
    edits are *detected* (409) instead of lost.

    **Polymorphic on ``doc_kind``** -- one table for all three kinds
    because they share an identical load-whole / save-whole lifecycle and
    are never queried *into* (the ``doc`` column is read and written
    whole, never filtered on a key). Three near-duplicate tables would buy
    nothing. The shape per kind:

    - ``"match"``   -- the match-level doc (``Match`` model). ``slug`` and
      ``stage_number`` are NULL.
    - ``"project"`` -- a per-shooter project doc (``MatchProject`` model).
      ``slug`` is the shooter slug; ``stage_number`` is NULL.
    - ``"audit"``   -- a per-stage audit doc (raw dict, no model).
      ``slug`` is the shooter slug; ``stage_number`` is the 1-based stage.

    The existing ``matches`` table (:class:`MatchRow`) stays as the
    ownership/index registry resolved by ``(user_id, match_id)``; this
    table holds the bodies.

    **Uniqueness.** Logically a doc is unique on
    ``(user_id, match_id, doc_kind, slug, stage_number)``. But NULL is
    distinct-from-NULL in a SQL unique index, so a plain
    :class:`UniqueConstraint` over those columns would happily admit two
    ``match`` rows (both with NULL slug + stage). The real guard is a
    Postgres ``coalesce`` expression index created in the migration
    (``coalesce(slug,'')``, ``coalesce(stage_number,-1)``). The
    ``UniqueConstraint`` declared here is only so SQLite's ``create_all``
    builds *a* unique index for the test engine -- tests never create
    duplicates, so its NULL-distinct weakness is harmless there.

    **Optimistic concurrency.** ``version`` starts at 1 on insert and the
    store bumps it on every save guarded by ``WHERE version =
    expected_version``; a stale writer's UPDATE matches 0 rows and raises
    ``StateConflictError`` (-> 409). See
    :class:`splitsmith.db.project_state.ProjectStateStore`.

    **Multi-tenant:** ``user_id`` is non-nullable, CASCADEs on user
    delete, and is added to the ``tenant_isolation`` RLS policy in the
    migration. Every query in ``ProjectStateStore`` filters by
    ``user_id``; isolation tests guard the invariant.
    """

    __tablename__ = "state_docs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "match_id",
            "doc_kind",
            "slug",
            "stage_number",
            name="uq_state_docs_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    doc_kind: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str | None] = mapped_column(String, nullable=True)
    stage_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Read whole, written whole, never queried into -> generic JSON for
    # SQLite tests, JSONB on Postgres (migration ALTERs the type). JSONB
    # buys nothing on read-whole access but is the right column type and
    # keeps the door open to future indexed access without a migration.
    doc: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<StateDocRow user_id={self.user_id!r} match_id={self.match_id!r} "
            f"kind={self.doc_kind!r} slug={self.slug!r} stage={self.stage_number!r} "
            f"v={self.version}>"
        )


class ComputeJobRow(Base):
    """One row per submitted job (doc 04).

    Hosted-mode counterpart to :class:`splitsmith.ui.jobs.JobRegistry`'s
    in-memory dict. Persists the full :class:`Job` wire shape (status,
    progress, message, error, result, timestamps, cancel/ack flags) so
    a server restart doesn't lose the SPA's view of recently-finished
    work. The dispatch model stays in-process for now -- workers run
    on a :class:`ThreadPoolExecutor` inside the API server -- but
    rows that were PENDING/RUNNING at the moment of a restart get
    swept to FAILED on boot so the SPA doesn't see ghosts that no
    worker will ever pick up.

    **Multi-tenant:** ``user_id`` is non-nullable and CASCADEs on user
    delete. Every query in :class:`PostgresJobBackend` filters by
    ``ComputeJobRow.user_id == self._user_id``. Isolation tests in
    ``test_job_backend.py`` guard the invariant.
    """

    __tablename__ = "compute_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Wire-shape mirror of :class:`splitsmith.ui.jobs.Job`. Status is
    # a free-form string keyed to ``JobStatus``; the enum lives in
    # ``jobs.py`` and we don't import it here to keep the DB layer
    # free of UI dependencies. Stored values: pending/running/
    # succeeded/failed/cancelled.
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    stage_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_id: Mapped[str | None] = mapped_column(String, nullable=True)

    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Per-job observability metadata (queue_wait_ms, total_ms, phases[], meta{}).
    # Generic ``JSON`` on SQLite (unit tests); the migration ALTERs to JSONB on
    # Postgres. Nullable: populated once by the backend on job completion.
    timings: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<ComputeJobRow id={self.id!r} kind={self.kind!r} status={self.status!r}>"
