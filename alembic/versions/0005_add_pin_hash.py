"""Add pin_hash column to contacts for phone+PIN auth

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-02
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS pin_hash TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS pin_hash")
