"""Create operator_rules table for knowledge layer

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-09
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS operator_rules (
            rule_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            site_id         UUID NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
            rule_type       TEXT NOT NULL,
            rule_name       TEXT NOT NULL,
            payload         JSONB NOT NULL,
            source          TEXT NOT NULL DEFAULT 'chat',
            status          TEXT NOT NULL DEFAULT 'proposed',
            confidence      REAL,
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            created_by      TEXT,
            confirmed_by    TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            confirmed_at    TIMESTAMPTZ,
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_operator_rules_site_id "
        "ON operator_rules (site_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_operator_rules_status "
        "ON operator_rules (status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_rules")
