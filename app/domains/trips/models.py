"""Database tables for the trips domain.

This is the ORM layer — one Python class per table. The repository is the only
thing that reads or writes these; nothing above it should import this module.

Right now there is one table (saved trips) as a worked example. The search
results (trains/flights/stays) are NOT stored here yet — they come from
external providers at request time. Persist them only if you need history.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavedTrip(Base):
    __tablename__ = "saved_trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Who saved it. No users table yet — this is the id the app sends. When auth
    # arrives, make it a foreign key.
    user_ref: Mapped[str] = mapped_column(String(64), index=True)

    title: Mapped[str] = mapped_column(String(200))
    destination: Mapped[str] = mapped_column(String(120))
    nights: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
