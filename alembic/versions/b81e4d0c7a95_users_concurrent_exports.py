"""users concurrent exports

An admin-set override of how many exports an account runs at once (spec
2026-10-25); NULL follows the account's tier.

Revision ID: b81e4d0c7a95
Revises: a3f9c2e81d47
Create Date: 2026-10-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b81e4d0c7a95"
down_revision: str | Sequence[str] | None = "a3f9c2e81d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("concurrent_exports", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "concurrent_exports")
