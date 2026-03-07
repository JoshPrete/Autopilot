"""Baseline — initial schema.sql state

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

This revision is the anchor point for all subsequent migrations.
It does NOT recreate the schema — schema.sql is the canonical source
for fresh installs.

EXISTING DATABASES: stamp this revision without running it:
    make db-stamp

FRESH DATABASES: apply the full schema first, then stamp:
    psql $DATABASE_URL -f schema.sql
    make db-stamp
    make migrate
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Anchor only. Fresh installs use schema.sql directly.
    # Existing DBs stamp this revision via: make db-stamp
    pass


def downgrade() -> None:
    pass
