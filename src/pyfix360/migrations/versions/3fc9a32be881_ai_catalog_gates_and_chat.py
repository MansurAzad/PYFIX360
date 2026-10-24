"""ai catalogue, discovery and quality gates

Adds everything introduced with the AI Settings Center and the quality-gate
engine:

* ``ai_providers.catalog_key`` / ``discovered_models`` / ``last_verified_at`` /
  ``last_verify_ok`` — provenance and live-verification state for a provider
* ``quality_gates`` — declarative pass/fail rules, project-scoped or global
* ``ai_chats`` — redacted-only AI conversation history

Revision ID: 3fc9a32be881
Revises: 5fdb43d5991b
Create Date: 2026-10-21 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3fc9a32be881"
down_revision: str | None = "5fdb43d5991b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- ai_providers: provenance + verification state ---------------------
    # batch_alter_table is used throughout so the migration also works on
    # SQLite, which cannot ALTER a column in place.
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("catalog_key", sa.String(length=64),
                                      nullable=False, server_default=""))
        batch_op.add_column(sa.Column("discovered_models", sa.Text(),
                                      nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("last_verified_at",
                                      sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_verify_ok", sa.Boolean(),
                                      nullable=True))

    # --- quality gates -----------------------------------------------------
    op.create_table(
        "quality_gates",
        sa.Column("id", sa.Integer(), nullable=False),
        # global gates have a NULL project_id
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False,
                  server_default="max_severity_count"),
        sa.Column("operator", sa.String(length=8), nullable=False,
                  server_default="<="),
        sa.Column("threshold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("severity", sa.String(length=16), nullable=False,
                  server_default="critical"),
        sa.Column("params", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_gates_project", "quality_gates", ["project_id"])

    # --- ai chat history (redacted text only) ------------------------------
    op.create_table(
        "ai_chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False,
                  server_default="user"),
        sa.Column("purpose", sa.String(length=32), nullable=False,
                  server_default="chat"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("redactions_applied", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("remote", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_chats_project", "ai_chats", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_chats_project", table_name="ai_chats")
    op.drop_table("ai_chats")
    op.drop_index("ix_quality_gates_project", table_name="quality_gates")
    op.drop_table("quality_gates")
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.drop_column("last_verify_ok")
        batch_op.drop_column("last_verified_at")
        batch_op.drop_column("discovered_models")
        batch_op.drop_column("catalog_key")
