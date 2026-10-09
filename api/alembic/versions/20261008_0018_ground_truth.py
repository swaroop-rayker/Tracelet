"""ground truth: labels and accuracy runs

Revision ID: 0018
Revises: 0017
Created: 2026-10-08

SPEC F4.AC15, F14.AC12 and section 11 row 30; DATA_MODEL section 10; ADR-0024.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ground_truth_labels",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("visit_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("cant_tell", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("true_country_code", sa.CHAR(2), nullable=True),
        sa.Column("true_admin1", sa.Text(), nullable=True),
        sa.Column("true_admin2", sa.Text(), nullable=True),
        sa.Column("true_city", sa.Text(), nullable=True),
        sa.Column("true_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("true_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("connection_kind", sa.Text(), nullable=True),
        sa.Column("vpn_used", sa.Boolean(), nullable=True),
        sa.Column("network", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("labeled_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "labeled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["visits.id"], ondelete="CASCADE", name="fk_ground_truth_labels_visit_id"
        ),
        sa.ForeignKeyConstraint(
            ["labeled_by"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_ground_truth_labels_labeled_by",
        ),
        sa.UniqueConstraint("visit_id", name="uq_ground_truth_labels_visit_id"),
        # "Can't tell" is exactly the absence of a truth.
        sa.CheckConstraint("cant_tell = (true_country_code IS NULL)", name="cant_tell_no_truth"),
        sa.CheckConstraint(
            "true_admin1 IS NULL OR true_country_code IS NOT NULL", name="admin1_needs_country"
        ),
        sa.CheckConstraint(
            "(true_admin2 IS NULL AND true_city IS NULL) OR true_admin1 IS NOT NULL",
            name="place_needs_admin1",
        ),
        sa.CheckConstraint(
            "(true_lat IS NULL) = (true_lng IS NULL)", name="coordinates_both_or_neither"
        ),
        sa.CheckConstraint(
            "true_lat IS NULL OR (true_lat BETWEEN -90 AND 90 AND true_lng BETWEEN -180 AND 180)",
            name="coordinates_in_range",
        ),
        sa.CheckConstraint(
            "connection_kind IS NULL OR connection_kind IN ('wifi', 'mobile_data', 'ethernet')",
            name="connection_kind_known",
        ),
        sa.CheckConstraint(
            "network IS NULL OR network IN ('airtel', 'jio', 'vi', 'bsnl', 'act', 'other')",
            name="network_known",
        ),
        sa.CheckConstraint(
            "true_country_code IS NULL OR true_country_code ~ '^[A-Z]{2}$'", name="country_upper"
        ),
        sa.CheckConstraint(
            "char_length(coalesce(true_admin1, '')) <= 100 "
            "AND char_length(coalesce(true_admin2, '')) <= 100 "
            "AND char_length(coalesce(true_city, '')) <= 100",
            name="place_length",
        ),
        sa.CheckConstraint("char_length(coalesce(notes, '')) <= 500", name="notes_length"),
    )
    op.create_index("ix_ground_truth_labels_labeled_at", "ground_truth_labels", ["labeled_at"])

    op.create_table(
        "accuracy_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("inference_version", sa.Text(), nullable=False),
        sa.Column("classifier_version", sa.Text(), nullable=False),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.Column("label_count", sa.Integer(), nullable=False),
        sa.Column("metrics", pg.JSONB(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("recorded_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recorded_by"], ["admins.id"], ondelete="SET NULL", name="fk_accuracy_runs_recorded_by"
        ),
        sa.CheckConstraint("origin IN ('cli', 'dashboard')", name="origin_known"),
        sa.CheckConstraint("label_count >= 0", name="label_count_nonnegative"),
        sa.CheckConstraint("char_length(coalesce(note, '')) <= 200", name="note_length"),
        sa.CheckConstraint(
            "git_sha IS NULL OR git_sha ~ '^[0-9a-f]{7,40}$'", name="git_sha_hex"
        ),
    )
    op.create_index("ix_accuracy_runs_run_at", "accuracy_runs", ["run_at"])
    # History is not edited (DATA_MODEL section 10.2): the audit_log pattern of migration 0002.
    op.execute("REVOKE UPDATE, DELETE ON TABLE accuracy_runs FROM tracelet_app")


def downgrade() -> None:
    op.drop_index("ix_accuracy_runs_run_at", table_name="accuracy_runs")
    op.drop_table("accuracy_runs")
    op.drop_index("ix_ground_truth_labels_labeled_at", table_name="ground_truth_labels")
    op.drop_table("ground_truth_labels")
