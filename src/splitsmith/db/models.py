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
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
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
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
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
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
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
    # "hosted" (native, created directly in the hosted app) or "desktop"
    # (mirrored down from a desktop-to-hosted sync push, doc 2026-08-07).
    # Set once at INSERT and never changed by a later upsert - see
    # ``PostgresMatchStore.upsert``.
    origin: Mapped[str] = mapped_column(String, nullable=False, server_default="hosted")
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
        return f"<MatchRow user_id={self.user_id!r} match_id={self.match_id!r} origin={self.origin!r}>"


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
    ``match`` rows (both with NULL slug + stage). The guard is therefore
    the ``coalesce`` expression index declared below
    (``coalesce(slug,'')``, ``coalesce(stage_number,-1)``), matching what
    migration ``d1f7b25c8a3e`` creates on Postgres. SQLite supports
    expression indexes too, so ``create_all`` builds the same guard for
    the test engine rather than a weaker NULL-distinct approximation --
    but a SQLite database built by ``alembic upgrade head`` instead (that
    migration's SQLite branch was not changed) still gets the plain,
    NULL-distinct unique index. That includes a hosted deploy that
    points ``splitsmith serve`` at a SQLite URL (see ``cli.py``'s
    ``serve`` command, which runs migrations, not ``create_all``).

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
        Index(
            "uq_state_docs_identity",
            "user_id",
            "match_id",
            "doc_kind",
            text("coalesce(slug, '')"),
            text("coalesce(stage_number, -1)"),
            unique=True,
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
    # Owning shooter's slug; part of the dedupe key in ``find_active``
    # because stage numbers repeat across every shooter in a match
    # (issue #664). NULL for jobs with no owning shooter
    # (model_download, generate_proxy, compare-grid, lab jobs).
    shooter_slug: Mapped[str | None] = mapped_column(String, nullable=True)
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
    # Wire-serialised submit args (job_journal.to_wire_args shape),
    # persisted so retry can re-enqueue a failed job. NULL only on rows
    # created before the retry migration - retry refuses those. Not part
    # of the wire Job model.
    args: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    # The submitting request's match binding (current_match_id ContextVar
    # at submit time), persisted so retry rebinds the ORIGINAL job's match
    # instead of whatever match is ambient on the retrying request. NULL
    # for rows predating this column and for legitimately match-less kinds
    # (model_download). Not part of the wire Job model.
    match_id: Mapped[str | None] = mapped_column(String, nullable=True)

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


class ShareTokenRow(Base):
    """One row per share link (public read-only match access, #349).

    The raw token IS the capability: 256 bits from ``secrets.token_urlsafe``,
    stored raw (not hashed) so the owner's share dialog can re-display the
    link. This is a deliberate departure from ``sessions`` /
    ``magic_link_tokens``: a leaked share token yields read-only access to
    one match and dies on revocation, not an account takeover.

    Not under RLS, following the ``sessions`` precedent: anonymous
    resolution runs before any ``app.user_id`` GUC exists. The unique-token
    lookup bounds the anonymous path; owner-management queries filter by
    ``user_id`` explicitly (see ``ShareTokenStore``).

    Revoke sets ``revoked_at`` instead of deleting - the share dialog keeps
    showing revoked links as an audit trail. ``expires_at`` is honored by
    the resolver but always NULL from the MVP UI.
    """

    __tablename__ = "share_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # #779: named scope keying what a share request may do. "read" (the
    # only value shipped today) maps to zero write capabilities - the
    # share middleware and engine enforce a READ ONLY transaction for it.
    # A later write-capable scope (e.g. "coach") is one new mapping, not
    # a schema change.
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="read")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ShareTokenRow user_id={self.user_id!r} match_id={self.match_id!r} "
            f"revoked={self.revoked_at is not None}>"
        )


class CommentRow(Base):
    """One public, timestamped comment on a shooter's stage video.

    **``user_id`` is the match owner, not the author.** Counterintuitive
    and deliberate: the comment is about the owner's footage, it dies
    with the owner's match through the CASCADE below, and an anonymous
    author has no account for it to belong to. Tenancy therefore stays
    exactly what it is in every other table, and the RLS policy needs no
    special case. ``author_user_id`` is the separate, nullable column
    that records a *signed-in* author.

    **``anchor_t`` is always set, even when ``anchor_kind == "shot"``.**
    The shot id is a label; ``anchor_t`` is the truth. A re-detect, a
    renumber, or a recycled ``cand-<n>`` (#842) therefore degrades a
    shot-anchored comment to a plain time pin -- it is never hidden and
    never silently re-attaches to a different shot, which is the failure
    that would actually mislead a reader.

    **``author_key_hash`` is convenience, not a security boundary.** The
    client mints a random opaque key once and keeps it in localStorage;
    it exists so a commenter can delete their own comment without an
    account. Anyone can mint one, so it must never gate anything whose
    exposure matters.

    **``share_token_id`` is the moderation primitive.** It makes "remove
    everything that came through the link I sent to that guy" one query,
    and it composes with revocation (#788).
    """

    __tablename__ = "match_comments"
    # The thread index backs the only listing query: every comment for
    # one (owner, match, shooter, stage). Created by migration
    # b4d8f1a90c27 alongside the per-column indexes declared below.
    __table_args__ = (Index("ix_match_comments_thread", "user_id", "match_id", "slug", "stage_number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Paired with user_id the way state_docs pairs them, rather than a
    # single-column FK: matches are keyed (user_id, match_id).
    match_id: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False)

    anchor_t: Mapped[float] = mapped_column(Float, nullable=False)
    anchor_kind: Mapped[str] = mapped_column(String, nullable=False, default="time")
    anchor_shot_id: Mapped[str | None] = mapped_column(String, nullable=True)

    author_kind: Mapped[str] = mapped_column(String, nullable=False, default="handle")
    author_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_handle: Mapped[str] = mapped_column(String, nullable=False)
    author_key_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Stable public identifier for the author, denormalized at write
    # time for the same reason author_handle is: rotating the handle
    # secret must not re-identify every historical author. Nullable only
    # to carry rows written before #867 - see ui/comments.to_out, which
    # computes the same value for those through author_code_for.
    author_code: Mapped[str | None] = mapped_column(String, nullable=True)
    share_token_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    body: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<CommentRow id={self.id!r} match_id={self.match_id!r} stage={self.stage_number}>"


class WorkerRow(Base):
    """One compute-worker target (self-hosted box or the Railway service).

    Operator infrastructure, not tenant data: no user_id column and not
    under RLS - the multi-tenant table checklist does not apply. Tokens
    are stored as sha256 hex digests (sessions precedent, NOT the raw
    share_tokens one): a worker token bootstraps infra credentials, so a
    DB leak must not yield usable tokens.

    kind is "self_hosted" (registered via the admin UI) or "railway"
    (a single row seeded at serve boot when the Railway launcher env
    vars are present, so the Railway worker gets the same enabled and
    priority knobs).
    """

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="self_hosted")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    registration_token_hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_token_hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Semver string of the code the worker is running (self-hosted agents report
    # it at register + on each channel reconnect; the Railway row is stamped at
    # serve boot). Observational only - nothing gates on it today.
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<WorkerRow id={self.id!r} name={self.name!r} kind={self.kind!r}>"


class DesktopTokenRow(Base):
    """One row per issued desktop-to-hosted sync credential (doc 2026-08-07).

    Account-scoped credential: the desktop app presents the raw token as a
    bearer secret when pushing a match up to the user's hosted account. We
    store only its SHA-256 hash (``token_hash``, ``sessions``/``workers``
    precedent, not the raw ``share_tokens`` one) - a DB leak must not yield
    a usable token. One row per issued token, so a user can name and revoke
    individual desktop installs independently (``name`` is user-chosen,
    e.g. "MacBook Pro").

    Resolved pre-tenant via the raw session factory, same rationale as
    ``share_tokens`` resolution: the sync-push endpoint authenticates the
    request from the bearer token alone, before any ``app.user_id`` GUC
    exists, so this table is **not** under RLS. Owner-management queries
    (list/revoke from the account settings UI) filter by ``user_id``
    explicitly once the caller is already authenticated by session cookie.

    Revocation sets ``revoked_at`` instead of deleting the row - same as
    ``share_tokens``, the settings UI keeps showing revoked tokens as an
    audit trail rather than losing the record entirely.
    """

    __tablename__ = "desktop_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Device-flow scoping (#719). ``'full'`` is the legacy pasted token
    # that resolves to an unrestricted User; ``'sync'`` is the scoped
    # credential the device flow (and, from #719 on, the account page's
    # manual button) mints. The server-side default is 'full' so rows
    # that predate this column -- and only those -- read as legacy.
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="full", default="full")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DesktopTokenRow id={self.id!r} user_id={self.user_id!r} "
            f"name={self.name!r} revoked={self.revoked_at is not None}>"
        )


class DeviceAuthorizationRow(Base):
    """One in-flight browser-assisted device authorization (#719).

    The desktop install POSTs to ``/api/device/authorize`` and gets back a
    ``device_code`` (32 bytes, stored only as a SHA-256 hash -- the real
    secret) plus a ``user_code`` (8 characters, low entropy on purpose:
    only usable by a caller who already holds a session and who then has
    to approve, and it dies in 10 minutes).

    Not under RLS, same rationale as ``DesktopTokenRow`` and
    ``ShareTokenRow``: the polling request authenticates from the device
    code alone, before any ``app.user_id`` GUC exists. An RLS'd table
    would make the resolution query return zero rows and break the flow
    outright.

    ``status`` walks pending -> approved|denied -> consumed. Approving
    records the approver and nothing else; the token is minted by the
    first poll that wins the conditional approved -> consumed update, so
    no plaintext credential is ever stored at rest and two concurrent
    polls cannot mint two tokens.

    ``last_polled_at`` backs the per-device_code interval throttle that
    produces the ``slow_down`` poll verdict.
    """

    __tablename__ = "device_authorizations"
    # Uniqueness on both ``user_code`` and ``device_code_hash`` is a
    # named table constraint, not a bare ``unique=True``: that is what
    # migration 0c1dbb2ce678 created and what the deployed schema has.
    # The plain ``index=True`` on ``user_code`` below is the separate
    # (redundant but shipped) lookup index the same migration created.
    __table_args__ = (
        UniqueConstraint("user_code", name="uq_device_authorizations_user_code"),
        UniqueConstraint("device_code_hash", name="uq_device_authorizations_device_code_hash"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_ulid)
    device_code_hash: Mapped[str] = mapped_column(String, nullable=False)
    user_code: Mapped[str] = mapped_column(String, nullable=False, index=True)
    device_name: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False, server_default="sync", default="sync")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending", default="pending")
    user_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DeviceAuthorizationRow id={self.id!r} user_code={self.user_code!r} "
            f"status={self.status!r} device_name={self.device_name!r}>"
        )
