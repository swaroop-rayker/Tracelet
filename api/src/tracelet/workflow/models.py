"""Annotations and saved views (DATA_MODEL section 9a, F9.AC25, F9.AC26).

Both hold only what admins type -- a note, a name, a page's filter query -- never anything
about visitors. The CHECKs in migration 0017 hold the bounds the API validates.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import uuid7
from tracelet.db.base import Base

ANNOTATION_MAX: Final = 200
VIEW_NAME_MAX: Final = 60
VIEW_QUERY_MAX: Final = 2000
VIEWS_PER_ADMIN: Final = 50
# The dashboard pages a view can open (CHECK ck_saved_views_path_known, migration 0017).
VIEW_PATH_PATTERN: Final = (
    r"^/(visits|geography|breakdowns|sources|returning|compare|links(/[a-z0-9-]{4,32})?)?$"
)


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL: a note for every link.
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("links.id", ondelete="CASCADE")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SavedView(Base):
    __tablename__ = "saved_views"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
