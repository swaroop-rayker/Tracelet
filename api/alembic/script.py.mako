"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}

Checklist for every migration (CLAUDE.md §2):
  - docs/DATA_MODEL.md updated in the SAME commit
  - invariants expressed as constraints, partial unique indexes or triggers
    rather than left to application code
  - any table needing a narrower grant than the schema default (audit_log above
    all) revokes it explicitly here
  - forward-only: no destructive downgrade on production data
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
