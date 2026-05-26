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
from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
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
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RecentProjectRow user_id={self.user_id!r} path={self.path!r}>"
