"""add match_id to recent_projects

Hosted picker/bind resolved a recent project by its on-disk path, but in
hosted mode that path is an ephemeral container working root that doesn't
survive a redeploy -- so reopening an existing match 404'd
(``project_path_missing``). Recording the match's stable ``match_id`` on
the row lets hosted ``bind`` resolve the match through Postgres (this
column + the ``matches`` ownership table) instead of the filesystem.

Nullable: local-mode rows and pre-existing rows have none; the bind path
falls back to the filesystem flow when it's absent.

Revision ID: e2a5f9c4b318
Revises: d1f7b25c8a3e
Create Date: 2026-06-01 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2a5f9c4b318"
down_revision: str | Sequence[str] | None = "d1f7b25c8a3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("recent_projects", sa.Column("match_id", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recent_projects", "match_id")
