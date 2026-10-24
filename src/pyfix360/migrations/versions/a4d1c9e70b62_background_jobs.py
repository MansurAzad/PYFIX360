"""background jobs

Adds ``background_jobs`` — the queue a detached host consumes after the window
closes. The row is the entire protocol between the desktop process and the
background one, so it carries both directions:

* intent written by the operator: ``confirmed``, ``cancel_requested``,
  ``resume_action``
* progress written by the host: ``status``, ``detail``, ``steps``, ``pid``,
  ``host``, ``updated_at`` (its heartbeat)

``updated_at`` is *not* an auto-stamp: the host re-stamps it deliberately on each
poll, and a job whose stamp goes quiet is reaped as ``interrupted``.

Revision ID: a4d1c9e70b62
Revises: 3fc9a32be881
Create Date: 2026-10-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d1c9e70b62"
down_revision: str | None = "3fc9a32be881"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        # a job without a project is legal: the daemon can run a queue that is
        # not tied to one imported folder
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False,
                  server_default="autopilot"),
        sa.Column("status", sa.String(length=24), nullable=False,
                  server_default="queued"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False,
                  server_default="0"),
        sa.Column("resume_action", sa.String(length=8), nullable=False,
                  server_default=""),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("host", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # the host's poll is "what is queued / what is mine", so it filters on status
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_table("background_jobs")
