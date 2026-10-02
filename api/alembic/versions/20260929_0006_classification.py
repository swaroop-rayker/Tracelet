"""classification: persist the client's headless-browser probes

Revision ID: 0006
Revises: 0005
Created: 2026-09-29

M4. M2 accepted the capture page's ``probes`` and stored nothing, because what a probe
*means* was M4's decision (DATA_MODEL 5.1). It is now decided (ADR-0011 amendment), and
the classifier runs in the ADR-0015 job, after the request has gone -- so the values have
to be on the row for it to read.

One JSONB column rather than eight: probes are read together, for one visit, by one
consumer; none is filtered on; and the set will change as automation tooling does, which
should not cost a migration each time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("visits", sa.Column("client_probes", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("visits", "client_probes")
