"""sources: referrers cut to their origin, SQL NULL for absent JSON, rollups rebuilt

Revision ID: 0016
Revises: 0015
Created: 2026-10-08

SPEC section 11 row 28, F3.AC1 (amended), F9.AC21-F9.AC24, DATA_MODEL sections 5.1 and 9.2a,
ERRORS E74.

1. **Every stored referrer is cut to its origin** -- scheme and host, lower-cased, without
   user info -- in ``visits.referer`` and in ``request_headers`` alike, as capture now
   does (``capture/signals.py`` ``referrer_origin``). A path or query can carry personal
   data; nothing is kept that capture would not keep today. A value with no scheme and
   host is removed.

2. **JSON ``null`` becomes SQL ``NULL``** in ``utm``, ``request_headers`` and
   ``client_probes`` (E74).

3. **Rollup days that still have raw visits are forgotten**, as 0008 did, so the read path
   answers them from raw rows at once and the settle job rebuilds them with the six new
   dimensions. Days past retention keep their rollups, without the new dimensions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The SQL twin of signals.referrer_origin: scheme "://" authority, then user info dropped.
ORIGIN = r"""
    left(
        regexp_replace(
            lower(substring({value} from '^\s*([A-Za-z][A-Za-z0-9+.\-]*://[^/?#\s]+)')),
            '://[^/@]*@', '://'
        ),
        255
    )
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE visits SET referer = {ORIGIN.format(value="referer")}
        WHERE referer IS NOT NULL
        """
    )
    op.execute(
        f"""
        UPDATE visits SET request_headers = CASE
            WHEN o.origin IS NULL THEN request_headers - 'referer'
            ELSE jsonb_set(request_headers, '{{referer}}', to_jsonb(o.origin))
        END
        FROM (
            SELECT id, {ORIGIN.format(value="request_headers->>'referer'")} AS origin
            FROM visits WHERE request_headers ? 'referer'
        ) o
        WHERE visits.id = o.id
        """
    )
    for column in ("utm", "request_headers", "client_probes"):
        op.execute(f"UPDATE visits SET {column} = NULL WHERE {column} = 'null'::jsonb")  # noqa: S608 -- fixed names
    op.execute(
        """
        DELETE FROM rollup_state s
        WHERE s.day >= (SELECT (min(v.occurred_at) AT TIME ZONE s.reporting_tz)::date
                        FROM visits v)
        """
    )


def downgrade() -> None:
    # Cut referrers cannot be restored: that is the point. Rollup days rebuilt with the new
    # dimensions are left as they are; `tracelet analytics rebuild` rebuilds them.
    pass
