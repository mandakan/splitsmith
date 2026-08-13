"""add author_code to match_comments

No backfill. #866 landed after the 0.29.0 release and is unreleased, so
production has no comment rows. Dev and staging rows written before this
migration keep author_code NULL and get their code computed at read time
by ui/comments.to_out -- a backfill here would have to reproduce the
HMAC secret in the migration environment, and getting that wrong would
write plausible-looking wrong codes with no error.

Revision ID: 58603835d0bd
Revises: b4d8f1a90c27
Create Date: 2026-08-13 20:12:40.551300

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58603835d0bd"
down_revision: str | Sequence[str] | None = "b4d8f1a90c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("match_comments", sa.Column("author_code", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("match_comments", "author_code")
