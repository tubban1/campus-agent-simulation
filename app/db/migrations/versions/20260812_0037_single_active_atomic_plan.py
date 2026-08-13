"""Guarantee that each Agent has at most one active atomic plan.

Revision ID: 20260812_0037
Revises: 20260810_0036
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0037"
down_revision = "20260810_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("agent_action_plans"):
        return

    # Keep the newest executing plan and preserve older rows as history.
    op.execute(
        """
        UPDATE agent_action_plans
        SET status = 'superseded'
        WHERE status = 'executing'
          AND id IN (
              SELECT id FROM (
                  SELECT id,
                         ROW_NUMBER() OVER (
                             PARTITION BY resident_id
                             ORDER BY id DESC
                         ) AS sequence
                  FROM agent_action_plans
                  WHERE status = 'executing'
              ) duplicates
              WHERE duplicates.sequence > 1
          )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_one_executing_atomic_plan
        ON agent_action_plans (resident_id)
        WHERE status = 'executing'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_one_executing_atomic_plan")
