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
from sqlalchemy import DateTime, String, func
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

    # Soft delete -- the row survives until the 7-day grace
    # expires so the user can recover by re-signing in.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"
