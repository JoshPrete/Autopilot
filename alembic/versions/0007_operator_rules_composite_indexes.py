"""Add composite indexes to operator_rules that the runtime DDL helper expects

The 0006_knowledge_layer migration created the operator_rules table with only
two single-column indexes (ix_operator_rules_site_id, ix_operator_rules_status).
The runtime _ensure_operator_rules_table helper in data/storage.py tries to
create two composite indexes at first use.  If the app DB role lacks CREATE
INDEX on public schema, the index step raised an exception that was caught by
a single broad try/except, rolling back the whole transaction — including the
preceding CREATE TABLE — and leaving _operator_rules_table_verified=False.

This migration creates those composite indexes under the migration role
(which has the required privileges) so that:
  1. The indexes exist before the app runs.
  2. The runtime helper's CREATE INDEX IF NOT EXISTS statements are no-ops.
  3. The verified flag is set on first call regardless of index permissions.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-05
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite index used by queries that filter on (site_id, status, active)
    # and order by updated_at — covers get_pending_operator_rule and
    # list_operator_rules.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operator_rules_site_status
        ON operator_rules(site_id, status, active, updated_at DESC)
        """
    )
    # Composite index used by rule-type lookups with recency ordering.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operator_rules_site_type
        ON operator_rules(site_id, rule_type, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_operator_rules_site_status")
    op.execute("DROP INDEX IF EXISTS idx_operator_rules_site_type")
