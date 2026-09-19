"""The declarative base every ORM model inherits from.

Keeping it in its own tiny module avoids a circular import: models import
`Base` from here, and the engine/session code imports both. Import this
directly whenever you define a table.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
